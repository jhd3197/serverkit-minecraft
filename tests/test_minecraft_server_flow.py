"""serverkit-minecraft create/lifecycle/players/overview flow tests (plan 53).

Covers the create wizard → DeploymentJob wiring, port-check logic, lifecycle
sequencing (broadcast → save-all flush → stop; user-stop vs crash marker),
players routes, and the overview shape — with Docker and RCON behind fakes,
per the plan's "verify by code and layout" rule (no Docker daemon on the dev
box). The install path itself (run_install) runs against a real DeploymentJob
row with only the docker/firewall calls patched.

The extension lives in its own repository; these tests run inside a ServerKit
checkout (symlinked — see tests/README.md) and load its backend via
_mc_support, which mirrors how the panel loads an installed extension.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

from app import db
from app.models import Application
from app.models.deployment_job import DeploymentJob

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import _mc_support  # noqa: E402

_M = _mc_support.load_ext()


@pytest.fixture
def mc(app, client):
    """Mount the extension and hand back its modules + the test client."""
    _mc_support.ensure_plugin(app)
    return SimpleNamespace(svc=_M['server_service'], models=_M['models'],
                           gamekit=_M['gamekit'], client=client)


FakeRcon = _mc_support.FakeRcon


def _fake_rcon(monkeypatch, mc, responses=None, fail=False):
    return _mc_support.fake_rcon(monkeypatch, mc.svc, responses=responses, fail=fail)


def _make_server(mc, **overrides):
    return _mc_support.make_server(mc.models, **overrides)


def _patch_docker(monkeypatch, mc, **methods):
    _mc_support.patch_docker(monkeypatch, mc.svc, **methods)


# --------------------------------------------------------------------------- #
# Create flow
# --------------------------------------------------------------------------- #

def test_create_requires_eula(mc, auth_headers):
    resp = mc.client.post('/api/v1/minecraft', headers=auth_headers, json={
        'name': 'noserver', 'edition': 'java', 'eula_accepted': False,
    })
    assert resp.status_code == 400
    assert 'EULA' in resp.get_json()['error']
    assert mc.models.MinecraftServer.query.count() == 0


def test_create_success_queues_deploy_job(mc, auth_headers, monkeypatch):
    monkeypatch.setattr(mc.svc, 'port_available', lambda port, protocol='tcp': True)
    monkeypatch.setattr(mc.svc.deploys, 'start',
                        lambda kind, **kw: {'success': True, 'job_id': 'job-123'})

    resp = mc.client.post('/api/v1/minecraft', headers=auth_headers, json={
        'name': 'My Server', 'edition': 'java', 'flavor': 'paper',
        'world_name': 'friends', 'seed': '42', 'memory': '4g',
        'eula_accepted': True,
    })
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    assert data['job_id'] == 'job-123'
    assert data['deploy_url'] == '/deployments/job-123'

    server = mc.models.MinecraftServer.query.filter_by(name='my-server').first()
    assert server is not None                       # name normalized to lowercase
    assert server.eula_accepted is True
    assert server.memory == '4G'                    # normalized
    assert server.port == 25565                     # java default
    assert server.rcon_password                     # generated server-side
    assert server.rcon_port == 25575
    assert server.status == 'creating'
    assert server.deployment_job_id == 'job-123'


def test_create_bedrock_has_no_rcon_and_udp_default(mc, auth_headers, monkeypatch):
    monkeypatch.setattr(mc.svc, 'port_available', lambda port, protocol='tcp': True)
    monkeypatch.setattr(mc.svc.deploys, 'start',
                        lambda kind, **kw: {'success': True, 'job_id': 'job-9'})

    resp = mc.client.post('/api/v1/minecraft', headers=auth_headers, json={
        'name': 'pocket', 'edition': 'bedrock', 'flavor': 'paper',  # flavor ignored
        'eula_accepted': True,
    })
    assert resp.status_code == 201, resp.get_json()
    server = mc.models.MinecraftServer.query.filter_by(name='pocket').first()
    assert server.edition == 'bedrock'
    assert server.flavor == 'vanilla'               # forced — flavors are Java-only
    assert server.port == 19132                     # bedrock default
    assert server.rcon_password is None             # no RCON on Bedrock


def test_create_rejects_duplicate_name_and_busy_port(mc, auth_headers, monkeypatch):
    monkeypatch.setattr(mc.svc, 'port_available', lambda port, protocol='tcp': True)
    monkeypatch.setattr(mc.svc.deploys, 'start',
                        lambda kind, **kw: {'success': True, 'job_id': 'job-1'})
    _make_server(mc, name='taken')

    resp = mc.client.post('/api/v1/minecraft', headers=auth_headers, json={
        'name': 'taken', 'eula_accepted': True,
    })
    assert resp.status_code == 400
    assert 'already exists' in resp.get_json()['error']

    # A busy game port refuses creation with a suggestion when one is free.
    probes = []

    def fake_available(port, protocol='tcp'):
        probes.append((int(port), protocol))
        return int(port) != 25565
    monkeypatch.setattr(mc.svc, 'port_available', fake_available)
    resp = mc.client.post('/api/v1/minecraft', headers=auth_headers, json={
        'name': 'newone', 'eula_accepted': True,
    })
    assert resp.status_code == 400
    assert '25565' in resp.get_json()['error']
    assert probes[0] == (25565, 'tcp')              # probed with the right protocol


def test_port_check_route(mc, auth_headers, monkeypatch):
    monkeypatch.setattr(mc.svc, 'port_available',
                        lambda port, protocol='tcp': int(port) == 25566)
    resp = mc.client.get('/api/v1/minecraft/port-check?port=25566&edition=java',
                         headers=auth_headers)
    assert resp.get_json()['available'] is True
    resp = mc.client.get('/api/v1/minecraft/port-check?port=25565&edition=java',
                         headers=auth_headers)
    data = resp.get_json()
    assert data['available'] is False
    assert data['suggestion'] == 25566              # next free suggestion


def test_port_available_checks_rows_and_bind(mc, monkeypatch):
    # Our own rows hold a port.
    _make_server(mc, port=25565)
    assert mc.svc.port_available(25565, 'tcp') is False
    # A live listener holds a port (real socket, loopback — no Docker needed).
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('0.0.0.0', 0))
    sock.listen(1)
    held = sock.getsockname()[1]
    try:
        assert mc.svc.port_available(held, 'tcp') is False
    finally:
        sock.close()
    # An obscure free port is available.
    free = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free.bind(('0.0.0.0', 0))
    free_port = free.getsockname()[1]
    free.close()
    assert mc.svc.port_available(free_port, 'tcp') is True


# --------------------------------------------------------------------------- #
# run_install (the DeploymentJob handler) — real job row, patched docker
# --------------------------------------------------------------------------- #

def test_run_install_writes_compose_and_registers(mc, monkeypatch, tmp_path):
    server = _make_server(mc, status='creating')
    spec = {
        'name': server.name, 'edition': 'java', 'flavor': 'paper',
        'version': '1.21.4', 'world_name': 'world', 'seed': '',
        'memory': '2G', 'port': 25565, 'rcon_port': 25575,
        'rcon_password': 'pw', 'eula_accepted': True,
    }
    job = DeploymentJob(id='job-install', kind=mc.svc.DEPLOY_KIND, status='pending')
    job.set_plan({'server_id': server.id, 'spec': spec})
    db.session.add(job)
    db.session.commit()

    monkeypatch.setattr(mc.svc.paths, 'APPS_DIR', str(tmp_path))
    streamed = []

    def fake_compose_up(project_path, on_line, **kwargs):
        on_line('Pulling itzg/minecraft-server')
        return {'success': True, 'exit_code': 0}
    _patch_docker(monkeypatch, mc, compose_up_streaming=fake_compose_up)
    monkeypatch.setattr(mc.svc.FirewallService, 'allow_port',
                        lambda port, protocol='tcp', permanent=True: {'success': True})
    monkeypatch.setattr(mc.svc.deploys, 'log',
                        lambda job, line, level='info': streamed.append(line))

    result = mc.svc.run_install(job)
    assert result['success'] is True, result

    # Compose file on disk carries the generated document (D2/D3/D5).
    compose_path = tmp_path / server.name / 'docker-compose.yml'
    assert compose_path.is_file()
    import yaml
    doc = yaml.safe_load(compose_path.read_text())
    svc_doc = doc['services']['minecraft']
    assert svc_doc['image'] == 'itzg/minecraft-server'
    assert svc_doc['environment']['EULA'] == 'TRUE'
    assert '127.0.0.1:25575:25575' in svc_doc['ports']
    assert f'{mc.gamekit.compose.volume_name(server.name)}:/data' in svc_doc['volumes']

    # Marker file records the spec minus the RCON secret.
    marker = json.loads((tmp_path / server.name / '.serverkit-minecraft.json').read_text())
    assert marker['spec']['name'] == server.name
    assert 'rcon_password' not in marker['spec']

    # Rows updated: Application registered, server running.
    app_row = Application.query.filter_by(name=server.name).first()
    assert app_row is not None and app_row.app_type == 'docker'
    db.session.refresh(server)
    assert server.status == 'running'
    assert server.application_id == app_row.id


def test_run_install_marks_server_error_on_compose_failure(mc, monkeypatch, tmp_path):
    server = _make_server(mc, status='creating')
    job = DeploymentJob(id='job-fail', kind=mc.svc.DEPLOY_KIND, status='pending')
    job.set_plan({'server_id': server.id, 'spec': {
        'name': server.name, 'edition': 'java', 'eula_accepted': True}})
    db.session.add(job)
    db.session.commit()

    monkeypatch.setattr(mc.svc.paths, 'APPS_DIR', str(tmp_path))
    _patch_docker(monkeypatch, mc, compose_up_streaming=lambda *a, **k: {
        'success': False, 'exit_code': 1})
    monkeypatch.setattr(mc.svc.deploys, 'log', lambda *a, **k: None)

    result = mc.svc.run_install(job)
    assert result['success'] is False
    db.session.refresh(server)
    assert server.status == 'error'


# --------------------------------------------------------------------------- #
# Lifecycle (§3.4)
# --------------------------------------------------------------------------- #

def test_stop_broadcasts_and_saves_before_docker_stop(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    calls = []
    fake = FakeRcon()
    fake.command = lambda cmd: calls.append(('rcon', cmd)) or ''
    monkeypatch.setattr(mc.svc, '_rcon_client', lambda s: fake)
    _patch_docker(monkeypatch, mc, stop_container=lambda name, timeout=10: (
        calls.append(('docker', 'stop')) or {'success': True}))

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/stop', headers=auth_headers)
    assert resp.status_code == 200, resp.get_json()

    db.session.refresh(server)
    assert server.status == 'stopped'
    assert server.stop_requested is True            # the user-stop marker
    # §3.4 order: broadcast → save-all flush → docker stop.
    kinds = [c[0] for c in calls]
    assert kinds == ['rcon', 'rcon', 'docker']
    assert calls[0][1].startswith('say ')
    assert calls[1][1] == 'save-all flush'


def test_stop_survives_unreachable_rcon(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    _fake_rcon(monkeypatch, mc, fail=True)          # server wedged — no RCON
    stopped = []
    _patch_docker(monkeypatch, mc, stop_container=lambda name, timeout=10: (
        stopped.append(name) or {'success': True}))

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/stop', headers=auth_headers)
    assert resp.status_code == 200                  # stop proceeds regardless
    assert stopped == ['serverkit-mc-testserver']


def test_restart_clears_stop_marker(mc, auth_headers, monkeypatch):
    server = _make_server(mc, status='stopped', stop_requested=True)
    fake = _fake_rcon(monkeypatch, mc)
    _patch_docker(monkeypatch, mc,
                  restart_container=lambda name, timeout=10: {'success': True})

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/restart', headers=auth_headers)
    assert resp.status_code == 200
    db.session.refresh(server)
    assert server.status == 'running'
    assert server.stop_requested is False
    assert 'restarting' in fake.sent[0]
    assert fake.sent[-1] == 'save-all flush'


def test_start_clears_stop_marker(mc, auth_headers, monkeypatch):
    server = _make_server(mc, status='stopped', stop_requested=True)
    _patch_docker(monkeypatch, mc, start_container=lambda name: {'success': True})
    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/start', headers=auth_headers)
    assert resp.status_code == 200
    db.session.refresh(server)
    assert server.status == 'running' and server.stop_requested is False


def test_reconcile_distinguishes_user_stop_from_crash(mc, monkeypatch):
    server = _make_server(mc, status='running')
    _patch_docker(monkeypatch, mc, get_container=lambda name: {
        'State': {'Running': False, 'Status': 'exited', 'ExitCode': 137}})

    out = mc.svc.reconcile_status(server)
    assert out['status'] == 'crashed'               # no stop marker → crash
    assert out['exit_code'] == 137

    server.stop_requested = True
    db.session.commit()
    out = mc.svc.reconcile_status(server)
    assert out['status'] == 'stopped'               # marker → user stop


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #

def test_players_list_route(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    _fake_rcon(monkeypatch, mc, responses={
        'list': 'There are 2 of a max of 20 players online: Steve, Alex'})

    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/players', headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json() == {'online': 2, 'max': 20, 'players': ['Steve', 'Alex']}


def test_player_actions_validate_names_and_send_commands(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    fake = _fake_rcon(monkeypatch, mc, responses={'kick Steve': 'Kicked Steve'})

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/players/kick',
                          headers=auth_headers, json={'player': 'Steve'})
    assert resp.status_code == 200
    assert fake.sent == ['kick Steve']

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/players/kick',
                          headers=auth_headers, json={'player': 'bad name; rm -rf'})
    assert resp.status_code == 400                  # never reaches RCON
    assert fake.sent == ['kick Steve']

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/players/ban',
                          headers=auth_headers, json={'player': 'Griefer', 'reason': 'griefing'})
    assert resp.status_code == 200
    assert fake.sent[-1] == 'ban Griefer griefing'

    for action, cmd in (('pardon', 'pardon Griefer'), ('op', 'op Steve'), ('deop', 'deop Steve')):
        player = cmd.split()[1]
        resp = mc.client.post(f'/api/v1/minecraft/{server.id}/players/{action}',
                              headers=auth_headers, json={'player': player})
        assert resp.status_code == 200, action
        assert fake.sent[-1] == cmd


def test_bedrock_player_routes_answer_400(mc, auth_headers):
    server = _make_server(mc, name='pocket', edition='bedrock', port=19132,
                          container_name='serverkit-mc-pocket')
    assert mc.client.get(f'/api/v1/minecraft/{server.id}/players',
                         headers=auth_headers).status_code == 400
    assert mc.client.post(f'/api/v1/minecraft/{server.id}/players/kick',
                          headers=auth_headers, json={'player': 'Steve'}).status_code == 400
    assert mc.client.post(f'/api/v1/minecraft/{server.id}/rcon',
                          headers=auth_headers, json={'command': 'list'}).status_code == 400


def test_whitelist_routes(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    fake = _fake_rcon(monkeypatch, mc, responses={
        'whitelist list': 'There are 2 whitelisted players: Steve, Alex'})
    _patch_docker(monkeypatch, mc, exec_command=lambda name, command, **kw: {
        'success': True, 'stdout': 'white-list=true\n'})

    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/whitelist', headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json() == {'enabled': True, 'players': ['Steve', 'Alex']}

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/whitelist',
                          headers=auth_headers, json={'action': 'add', 'player': 'NewGuy'})
    assert resp.status_code == 200
    assert fake.sent[-1] == 'whitelist add NewGuy'

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/whitelist',
                          headers=auth_headers, json={'action': 'enable'})
    assert fake.sent[-1] == 'whitelist on'

    resp = mc.client.post(f'/api/v1/minecraft/{server.id}/whitelist',
                          headers=auth_headers, json={'action': 'bogus'})
    assert resp.status_code == 400


def test_banlist_and_ops_routes(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    _fake_rcon(monkeypatch, mc, responses={
        'banlist': 'Griefer was banned by Server: griefing'})
    _patch_docker(monkeypatch, mc, exec_command=lambda name, command, **kw: {
        'success': True, 'stdout': '[{"uuid": "x", "name": "Steve", "level": 4}]'})

    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/players/bans', headers=auth_headers)
    assert resp.get_json() == {'bans': ['Griefer']}
    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/players/ops', headers=auth_headers)
    assert resp.get_json() == {'ops': ['Steve']}


# --------------------------------------------------------------------------- #
# Overview + logs
# --------------------------------------------------------------------------- #

def test_overview_shape(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    _patch_docker(monkeypatch, mc,
                  get_container=lambda name: {'State': {
                      'Running': True, 'Status': 'running',
                      'StartedAt': '2026-08-01T00:00:00Z', 'ExitCode': 0}},
                  get_container_stats=lambda name: {
                      'CPUPerc': '12.50%', 'MemUsage': '500MiB / 2GiB',
                      'MemPerc': '24.41%'})
    _fake_rcon(monkeypatch, mc, responses={
        'list': 'There are 1 of a max of 20 players online: Steve'})

    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/overview', headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'running'
    assert data['players'] == {'online': 1, 'max': 20, 'players': ['Steve']}
    assert data['stats']['cpu_percent'] == 12.5
    assert data['stats']['mem_usage_bytes'] == 500 * 1024 ** 2
    assert data['stats']['mem_limit_bytes'] == 2 * 1024 ** 3
    assert data['uptime_seconds'] > 0
    assert data['address'].endswith(':25565')       # request-host fallback
    assert data['next_restart_at'] is None          # Phase 3 placeholder


def test_overview_degrades_for_stopped_server(mc, auth_headers, monkeypatch):
    server = _make_server(mc, status='stopped', stop_requested=True)
    _patch_docker(monkeypatch, mc, get_container=lambda name: {
        'State': {'Running': False, 'Status': 'exited', 'ExitCode': 0}})

    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/overview', headers=auth_headers)
    data = resp.get_json()
    assert data['status'] == 'stopped'
    assert data['stats'] is None                    # never fake a metric (§3.5)
    assert data['players']['online'] is None
    assert data['uptime_seconds'] is None


def test_parse_docker_stats(mc):
    assert mc.svc.parse_docker_stats(None) is None
    parsed = mc.svc.parse_docker_stats({
        'CPUPerc': '0.75%', 'MemUsage': '1.5GiB / 4GiB', 'MemPerc': '37.5%'})
    assert parsed['cpu_percent'] == 0.75
    assert parsed['mem_usage_bytes'] == int(1.5 * 1024 ** 3)
    assert parsed['mem_percent'] == 37.5
    # Unparseable input yields Nones, not junk.
    parsed = mc.svc.parse_docker_stats({'CPUPerc': '--', 'MemUsage': '--'})
    assert parsed['cpu_percent'] is None
    assert parsed['mem_usage_bytes'] is None


def test_logs_route(mc, auth_headers, monkeypatch):
    server = _make_server(mc)
    _patch_docker(monkeypatch, mc, get_container_logs=lambda name, tail=100, **kw: {
        'success': True, 'logs': 'line1\nline2'})
    resp = mc.client.get(f'/api/v1/minecraft/{server.id}/logs?tail=50',
                         headers=auth_headers)
    assert resp.status_code == 200
    assert 'line1' in resp.get_json()['logs']


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #

def test_delete_cleans_up_container_volume_firewall_and_rows(mc, auth_headers,
                                                             monkeypatch, tmp_path):
    app_row = Application(name='testserver', app_type='docker', status='running',
                          root_path=str(tmp_path / 'testserver'), user_id=1, port=25565)
    db.session.add(app_row)
    db.session.commit()
    server = _make_server(mc, application_id=app_row.id)

    app_dir = tmp_path / 'testserver'
    app_dir.mkdir()
    (app_dir / 'docker-compose.yml').write_text('services: {}')
    monkeypatch.setattr(mc.svc.paths, 'APPS_DIR', str(tmp_path))

    calls = {'down': None, 'fw': None}
    _patch_docker(monkeypatch, mc,
                  get_container=lambda name: None,     # already gone — no graceful stop
                  compose_down=lambda path, volumes=False, **kw: (
                      calls.update(down=(path, volumes)) or {'success': True}))
    monkeypatch.setattr(mc.svc.FirewallService, 'deny_port',
                        lambda port, protocol='tcp', permanent=True: (
                            calls.update(fw=(port, protocol)) or {'success': True}))

    resp = mc.client.delete(f'/api/v1/minecraft/{server.id}?remove_volume=1',
                            headers=auth_headers)
    assert resp.status_code == 200, resp.get_json()

    assert calls['down'] == (str(app_dir), True)       # -v: world volume confirmed
    assert calls['fw'] == (25565, 'tcp')               # firewall cleanup (D5)
    assert not app_dir.exists()                        # app dir removed
    assert mc.models.MinecraftServer.query.count() == 0
    assert Application.query.filter_by(name='testserver').first() is None


def test_delete_without_volume_flag_keeps_the_world(mc, auth_headers,
                                                    monkeypatch, tmp_path):
    server = _make_server(mc)
    monkeypatch.setattr(mc.svc.paths, 'APPS_DIR', str(tmp_path))
    (tmp_path / server.name).mkdir()

    calls = {}
    _patch_docker(monkeypatch, mc,
                  get_container=lambda name: None,
                  compose_down=lambda path, volumes=False, **kw: (
                      calls.update(volumes=volumes) or {'success': True}))
    monkeypatch.setattr(mc.svc.FirewallService, 'deny_port',
                        lambda *a, **kw: {'success': True})

    resp = mc.client.delete(f'/api/v1/minecraft/{server.id}', headers=auth_headers)
    assert resp.status_code == 200
    assert calls['volumes'] is False                   # world volume survives
