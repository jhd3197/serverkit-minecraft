"""serverkit-minecraft "Config & care" tests (plan 53 Phase 3).

Covers the Settings form (read/merge/write-back + restart-required flagging),
the Backups flow (hot-backup ordering, retention, skip-when-empty, stop-first
restore), Schedules on core ScheduledJob rails (countdown plan, enable/disable,
next_run anchoring), and the event scan → notify-bus wiring — all with faked
RCON/docker per the plan's "verify by code and layout" rule.

The extension lives in its own repository; these tests run inside a ServerKit
checkout (symlinked — see tests/README.md) and load its backend via
_mc_support, which mirrors how the panel loads an installed extension.
"""
import os
import sys
import zipfile
from types import SimpleNamespace

import pytest

from app import db

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import _mc_support  # noqa: E402

_M = _mc_support.load_ext()

_PROPS = (
    '# Minecraft server properties\n'
    'motd=Hello World\n'
    'difficulty=hard\n'
    'max-players=20\n'
    'white-list=true\n'
    'level-type=default\n'
    'view-distance=10\n'
)


@pytest.fixture
def mc(app, client):
    _mc_support.ensure_plugin(app)
    return SimpleNamespace(svc=_M['server_service'], models=_M['models'],
                           gamekit=_M['gamekit'], client=client)


FakeRcon = _mc_support.FakeRcon


def _fake_rcon(monkeypatch, mc, responses=None):
    return _mc_support.fake_rcon(monkeypatch, mc.svc, responses=responses)


def _make_server(mc, **overrides):
    return _mc_support.make_server(mc.models, **overrides)


def _patch_docker(monkeypatch, mc, **methods):
    _mc_support.patch_docker(monkeypatch, mc.svc, **methods)


def _running_container(name):
    return {'State': {'Running': True, 'Status': 'running',
                      'StartedAt': '2026-08-01T00:00:00Z', 'ExitCode': 0}}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

def test_settings_route_returns_grouped_form(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    _patch_docker(monkeypatch, mc, exec_command=lambda name, command, **kw: {
        'success': True, 'stdout': _PROPS})

    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/settings', headers=auth_headers)
    assert resp.status_code == 200
    form = resp.get_json()['form']
    fields = {f['key']: f for g in form['groups'] for f in g['fields']}
    assert fields['max-players']['value'] == 20            # coerced int
    assert fields['white-list']['value'] is True           # coerced bool
    assert fields['difficulty']['options']                 # from the sidecar
    assert fields['level-type']['restart_required'] is True
    assert fields['motd']['restart_required'] is False
    general = next(g for g in form['groups'] if g['id'] == 'general')
    assert 'motd' in [f['key'] for f in general['fields']]


def test_settings_route_409_when_unreadable(mc, auth_headers, monkeypatch):
    server = _make_server(mc, status='stopped')
    _patch_docker(monkeypatch, mc, exec_command=lambda name, command, **kw: {
        'success': False, 'error': 'container not running'})
    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/settings', headers=auth_headers)
    assert resp.status_code == 409


def test_update_settings_writes_back_and_flags_restart(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    monkeypatch.setattr(mc.svc, '_read_container_file', lambda s, p: _PROPS)
    written = {}
    monkeypatch.setattr(mc.svc, '_write_container_file',
                        lambda s, p, content: written.update(content=content) or True)

    resp = mc.client.put(f'/api/v1/minecraft/{server.id}/settings',
                         headers=auth_headers,
                         json={'changes': {'motd': 'New Motd', 'level-type': 'flat'}})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data['restart_required'] is True
    assert data['restart_keys'] == ['level-type']           # motd needs no restart
    assert 'motd=New Motd' in written['content']
    assert 'level-type=flat' in written['content']
    assert '# Minecraft server properties' in written['content']  # comments preserved


def test_update_settings_unchanged_value_not_flagged(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    monkeypatch.setattr(mc.svc, '_read_container_file', lambda s, p: _PROPS)
    monkeypatch.setattr(mc.svc, '_write_container_file', lambda s, p, c: True)

    resp = mc.client.put(f'/api/v1/minecraft/{server.id}/settings',
                         headers=auth_headers,
                         json={'changes': {'level-type': 'default'}})  # same as file
    assert resp.get_json()['restart_required'] is False


def test_update_settings_refuses_env_managed_keys(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    monkeypatch.setattr(mc.svc, '_read_container_file', lambda s, p: _PROPS)
    resp = mc.client.put(f'/api/v1/minecraft/{server.id}/settings',
                         headers=auth_headers,
                         json={'changes': {'level-name': 'other'}})
    assert resp.status_code == 400
    assert 'environment' in resp.get_json()['error']


# --------------------------------------------------------------------------- #
# Backups
# --------------------------------------------------------------------------- #

def _patch_backup_env(monkeypatch, mc, tmp_path, calls, write_world=True):
    """Fake the Docker side of a backup: rcon quiesce + docker cp staging."""
    monkeypatch.setattr(mc.svc.paths, 'SERVERKIT_BACKUP_DIR', str(tmp_path))
    fake = FakeRcon()
    fake.command = lambda cmd: calls.append(('rcon', cmd)) or ''
    monkeypatch.setattr(mc.svc, '_quiescing_rcon', lambda server: fake)
    monkeypatch.setattr(mc.svc, '_container_path_exists',
                        lambda server, path: path == '/data/world')

    def fake_cp(src, dst):
        calls.append(('cp', src))
        if write_world:
            world_dir = os.path.join(dst, 'world')
            os.makedirs(world_dir, exist_ok=True)
            with open(os.path.join(world_dir, 'level.dat'), 'wb') as f:
                f.write(b'x' * 32)
        return {'success': True}
    monkeypatch.setattr(mc.svc, '_docker_cp', fake_cp)
    return fake


def test_backup_now_runs_hot_sequence_and_records(mc, auth_headers,
                                                  monkeypatch, tmp_path):
    server = _make_server(mc)
    calls = []
    _patch_backup_env(monkeypatch, mc, tmp_path, calls)
    sent = []
    monkeypatch.setattr(mc.svc.notify, 'send',
                        lambda event, **kw: sent.append(event))

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/backups', headers=auth_headers)
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data['success'] is True and data['skipped'] is False

    # §3.2 order: save-off → save-all flush → copy → save-on.
    kinds = [c[0] for c in calls]
    assert kinds == ['rcon', 'rcon', 'cp', 'rcon']
    assert calls[0] == ('rcon', 'save-off')
    assert calls[1] == ('rcon', 'save-all flush')
    assert calls[2][0] == 'cp'
    assert calls[3] == ('rcon', 'save-on')

    # Self-describing name + row + archive on disk.
    name = data['backup']['name']
    assert name.startswith('world_v1.21.4_') and name.endswith('.zip')
    backup = mc.models.MinecraftBackup.query.filter_by(server_id=server.id).one()
    assert os.path.isfile(backup.file_path)
    with zipfile.ZipFile(backup.file_path) as zf:
        assert 'world/level.dat' in zf.namelist()

    # §3.3: backup success notified.
    assert 'minecraft.backup_completed' in sent


def test_backup_skips_empty_world(mc, auth_headers, monkeypatch, tmp_path):
    server = _make_server(mc)
    calls = []
    _patch_backup_env(monkeypatch, mc, tmp_path, calls, write_world=False)
    monkeypatch.setattr(mc.svc.notify, 'send', lambda *a, **kw: None)

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/backups', headers=auth_headers)
    data = resp.get_json()
    assert data['skipped'] is True
    assert mc.models.MinecraftBackup.query.count() == 0


def test_backup_retention_prunes_oldest(mc, auth_headers, monkeypatch, tmp_path):
    server = _make_server(mc, backup_retention=1)
    calls = []
    _patch_backup_env(monkeypatch, mc, tmp_path, calls)
    monkeypatch.setattr(mc.svc.notify, 'send', lambda *a, **kw: None)

    # An older archive already on disk.
    dest = tmp_path / 'minecraft' / server.name
    dest.mkdir(parents=True)
    old = dest / 'world_v1.20_2026-01-01_0000.zip'
    old.write_bytes(b'old')

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/backups', headers=auth_headers)
    data = resp.get_json()
    assert data['success'] is True
    assert 'world_v1.20_2026-01-01_0000.zip' in data['pruned']
    assert not old.exists()


def test_backup_fails_when_quiesce_impossible(mc, auth_headers, monkeypatch, tmp_path):
    server = _make_server(mc)
    monkeypatch.setattr(mc.svc.paths, 'SERVERKIT_BACKUP_DIR', str(tmp_path))
    rcon_module = mc.gamekit.rcon

    def boom(server):
        raise rcon_module.RconError('connection refused')
    monkeypatch.setattr(mc.svc, '_quiescing_rcon', boom)
    sent = []
    monkeypatch.setattr(mc.svc.notify, 'send',
                        lambda event, **kw: sent.append(event))

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/backups', headers=auth_headers)
    assert resp.status_code == 500
    assert 'quiesce' in resp.get_json()['error']
    assert 'minecraft.backup_failed' in sent


def test_restore_stops_swaps_and_starts(mc, auth_headers, monkeypatch, tmp_path):
    server = _make_server(mc)

    # A real archive with a world dir inside.
    archive = tmp_path / 'world_v1.21.4_2026-08-01_0000.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('world/level.dat', b'level')
    backup = mc.models.MinecraftBackup(
        server_id=server.id, name=archive.name, file_path=str(archive),
        size_bytes=archive.stat().st_size, kind='manual')
    db.session.add(backup)
    db.session.commit()

    order = []
    monkeypatch.setattr(mc.svc, '_container_state',
                        lambda s: {'running': True, 'exit_code': 0})
    monkeypatch.setattr(mc.svc, 'stop_server',
                        lambda s: order.append('stop') or {'success': True})
    monkeypatch.setattr(mc.svc, 'start_server',
                        lambda s: order.append('start') or {'success': True})
    _patch_docker(monkeypatch, mc, exec_command=lambda name, command, **kw: {
        'success': True, 'stdout': ''})
    monkeypatch.setattr(mc.svc, '_docker_cp',
                        lambda src, dst: order.append('cp') or {'success': True})

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/backups/{backup.id}/restore',
                          headers=auth_headers)
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data['restored'] == ['world']
    assert data['restarted'] is True
    assert order == ['stop', 'cp', 'start']               # stop-first, then swap, then start


def test_restore_refuses_missing_archive(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    backup = mc.models.MinecraftBackup(
        server_id=server.id, name='gone.zip',
        file_path='/nonexistent/gone.zip', kind='manual')
    db.session.add(backup)
    db.session.commit()
    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/backups/{backup.id}/restore',
                          headers=auth_headers)
    assert resp.status_code == 500
    assert 'missing' in resp.get_json()['error']


def test_delete_backup_removes_file_and_row(mc, auth_headers, tmp_path):
    server = _make_server(mc)
    archive = tmp_path / 'b.zip'
    archive.write_bytes(b'z')
    backup = mc.models.MinecraftBackup(
        server_id=server.id, name='b.zip', file_path=str(archive), kind='manual')
    db.session.add(backup)
    db.session.commit()

    resp = mc.client.delete(f'/api/v1/minecraft/{server.id}/backups/{backup.id}',
                            headers=auth_headers)
    assert resp.status_code == 200
    assert not archive.exists()
    assert mc.models.MinecraftBackup.query.count() == 0


def test_backup_config_route(mc, auth_headers):
    server = _make_server(mc)
    resp = mc.client.put(f'/api/v1/minecraft/{server.id}/backups/config',
                         headers=auth_headers,
                         json={'retention': 10, 'skip_when_empty': False})
    assert resp.status_code == 200
    db.session.refresh(server)
    assert server.backup_retention == 10
    assert server.backup_skip_empty is False

    resp = mc.client.put(f'/api/v1/minecraft/{server.id}/backups/config',
                         headers=auth_headers, json={'retention': 'lots'})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #

def test_create_restart_schedule_anchors_next_run_to_cron(mc, auth_headers):
    server = _make_server(mc)
    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/schedules',
                          headers=auth_headers,
                          json={'type': 'restart', 'cron': '0 4 * * *'})
    assert resp.status_code == 201, resp.get_json()

    sched = mc.models.MinecraftSchedule.query.filter_by(server_id=server.id).one()
    assert sched.type == 'restart' and sched.cron == '0 4 * * *'

    core = mc.svc._core_schedule(sched.job_name)
    assert core is not None
    assert core.kind == mc.svc.SCHEDULE_JOB_KIND
    assert core.get_payload() == {'schedule_id': sched.id}
    # Anchored to the cron (next 4am), not "now" — an immediate restart on
    # create would be a nasty surprise. Compare against croniter directly so
    # the assertion doesn't depend on the wall clock.
    from croniter import croniter
    from datetime import datetime
    expected = croniter('0 4 * * *', datetime.utcnow()).get_next(datetime)
    assert abs((core.next_run_at - expected).total_seconds()) < 60

    listed = mc.client.get(f'/api/v1/minecraft/{server.id}/schedules',
                           headers=auth_headers).get_json()['schedules']
    assert listed[0]['next_run_at'] is not None


def test_create_schedule_validation(mc, auth_headers):
    server = _make_server(mc)
    base = f'/api/v1/minecraft/{server.id}/schedules'
    assert mc.client.post(base, headers=auth_headers,
                          json={'type': 'restart', 'cron': 'not a cron'}).status_code == 400
    assert mc.client.post(base, headers=auth_headers,
                          json={'type': 'explode', 'cron': '0 4 * * *'}).status_code == 400
    assert mc.client.post(base, headers=auth_headers,
                          json={'type': 'announce', 'cron': '0 4 * * *'}).status_code == 400
    assert mc.models.MinecraftSchedule.query.count() == 0


def test_schedule_toggle_and_delete_sync_core_row(mc, auth_headers):
    server = _make_server(mc)
    created = mc.client.post(f'/api/v1/minecraft/{server.id}/schedules',
                             headers=auth_headers,
                             json={'type': 'backup', 'cron': '0 */6 * * *'}).get_json()
    sid = created['schedule']['id']

    resp = mc.client.put(f'/api/v1/minecraft/{server.id}/schedules/{sid}',
                         headers=auth_headers, json={'enabled': False})
    assert resp.status_code == 200
    sched = mc.models.MinecraftSchedule.query.get(sid)
    core = mc.svc._core_schedule(sched.job_name)
    assert sched.enabled is False and core.enabled is False

    resp = mc.client.delete(f'/api/v1/minecraft/{server.id}/schedules/{sid}',
                            headers=auth_headers)
    assert resp.status_code == 200
    assert mc.models.MinecraftSchedule.query.count() == 0
    assert mc.svc._core_schedule(sched.job_name) is None


def test_run_scheduled_job_announce_sends_say(mc, monkeypatch):
    server = _make_server(mc)
    result = mc.svc.create_schedule(server, 'announce', '0 * * * *',
                                    message='Server restart at midnight')
    assert result['success'] is True
    sid = result['schedule']['id']

    fake = _fake_rcon(monkeypatch, mc)
    job = SimpleNamespace(get_payload=lambda: {'schedule_id': sid})
    out = mc.svc.run_scheduled_job(job)
    assert out['success'] is True
    assert fake.sent == ['say Server restart at midnight']

    # Disabled schedules are skipped.
    mc.svc.update_schedule(mc.models.MinecraftSchedule.query.get(sid), enabled=False)
    fake.sent.clear()
    assert mc.svc.run_scheduled_job(job) == {'skipped': True}
    assert fake.sent == []


def test_scheduled_restart_broadcasts_countdown_then_restarts(mc, monkeypatch):
    server = _make_server(mc)
    fake = _fake_rcon(monkeypatch, mc)
    sleeps = []
    restarted = []
    monkeypatch.setattr(mc.svc, 'restart_server',
                        lambda s: restarted.append(s.id) or
                        {'success': True, 'status': 'running'})

    result = mc.svc.scheduled_restart(server, countdown_seconds=60,
                                      sleep=lambda s: sleeps.append(s))
    assert result['success'] is True
    # The countdown plan for 60s: warn at offset 0, sleep 50, warn at 50, sleep 10.
    assert sleeps == [50, 10]
    assert len(fake.sent) == 2
    assert fake.sent[0].startswith('say Server restarting in 1 minute')
    assert '10 seconds' in fake.sent[1]
    assert restarted == [server.id]                           # graceful restart after


def test_next_restart_at_feeds_overview(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    assert mc.svc.next_restart_at(server) is None

    mc.svc.create_schedule(server, 'restart', '0 4 * * *')
    expected = mc.svc.next_restart_at(server)
    assert expected is not None

    _patch_docker(monkeypatch, mc,
                  get_container=_running_container,
                  get_container_stats=lambda name: None)
    _fake_rcon(monkeypatch, mc, responses={
        'list': 'There are 0 of a max of 20 players online:'})
    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/overview', headers=auth_headers)
    assert resp.get_json()['next_restart_at'] == expected


# --------------------------------------------------------------------------- #
# Event scan → notify bus (§3.3)
# --------------------------------------------------------------------------- #

def test_event_scan_notifies_join_leave_started(mc, monkeypatch):
    server = _make_server(mc)
    _patch_docker(monkeypatch, mc,
                  get_container=_running_container,
                  get_container_logs=lambda name, **kw: {'success': True, 'logs': (
                      '[12:00:01] [Server thread/INFO]: Done (3.2s)! For help, type "help"\n'
                      '[12:01:00] [Server thread/INFO]: Steve joined the game\n'
                      '[12:05:00] [Server thread/INFO]: Steve left the game\n')})
    sent = []
    monkeypatch.setattr(mc.svc.notify, 'send',
                        lambda event, to=None, data=None, **kw: sent.append((event, data)))

    out = mc.svc.run_event_scan()
    assert out['scanned'] == 1
    events = [e for e, _ in sent]
    assert 'minecraft.server_started' in events
    assert 'minecraft.player_join' in events
    assert 'minecraft.player_leave' in events
    join = next(d for e, d in sent if e == 'minecraft.player_join')
    assert join['player'] == 'Steve' and join['server'] == 'testserver'


def test_event_scan_detects_crash_via_stop_marker(mc, monkeypatch):
    server = _make_server(mc, status='running', stop_requested=False)
    _patch_docker(monkeypatch, mc, get_container=lambda name: {
        'State': {'Running': False, 'Status': 'exited', 'ExitCode': 1}})
    sent = []
    monkeypatch.setattr(mc.svc.notify, 'send',
                        lambda event, **kw: sent.append(event))

    mc.svc.run_event_scan()
    assert 'minecraft.server_crashed' in sent
    db.session.refresh(server)
    assert server.status == 'crashed'

    # A user stop is NOT a crash — no notification.
    sent.clear()
    server2 = _make_server(mc, name='stopped-one', port=25566,
                           container_name='serverkit-mc-stopped-one',
                           status='stopped', stop_requested=True)
    mc.svc.run_event_scan()
    assert 'minecraft.server_crashed' not in sent
    db.session.refresh(server2)
    assert server2.status == 'stopped'


def test_event_scan_skips_log_tail_for_bedrock(mc, monkeypatch):
    _make_server(mc, name='pocket', edition='bedrock', port=19132,
                 container_name='serverkit-mc-pocket')
    _patch_docker(monkeypatch, mc, get_container=_running_container)
    log_calls = []
    _patch_docker(monkeypatch, mc, get_container_logs=lambda name, **kw: (
        log_calls.append(name) or {'success': True, 'logs': ''}))
    monkeypatch.setattr(mc.svc.notify, 'send', lambda *a, **kw: None)

    mc.svc.run_event_scan()
    assert log_calls == []          # no RCON parser runs on Bedrock logs


def test_job_kinds_registered(mc):
    from app.jobs import registry
    assert registry.get(mc.svc.SCHEDULE_JOB_KIND) is not None
    assert registry.get(mc.svc.EVENT_SCAN_JOB_KIND) is not None
