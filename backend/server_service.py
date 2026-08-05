"""Server lifecycle service for serverkit-minecraft (plan 53 Phases 1-3).

The Docker-touching half of the extension. Everything verifiable without a
daemon (compose shape, spec validation, RCON parsing, the graceful-stop
sequence) lives in ``gamekit``; this module wires those pure pieces to
ServerKit's existing machinery (D7 — reuse, never fork):

- create  → validate spec → MinecraftServer row → a ``minecraft.install``
  DeploymentJob via plugins_sdk.deploys, so the install rides the Deploy
  Console with live logs exactly like a template install (D4)
- delete  → compose down (optional ``-v`` for the world volume) + firewall
  cleanup via the existing firewall service (D5)
- start/stop/restart → the §3.4 sequence: in-game broadcast → ``save-all
  flush`` → container stop; ``stop_requested`` on the row distinguishes a
  user stop from a crash for the notification wiring
- players → RCON list/kick/ban/pardon/op/deop/whitelist/banlist
- overview → container state + docker stats + RCON player count + the
  share-card address + next scheduled restart
- settings → server.properties as a grouped form (gamekit.config_form +
  the sidecar metadata), write-back with restart-required flagging
- backups → the §3.2 hot-backup sequence (quiesce → copy-then-zip → resume)
  with retention + skip-when-empty, restore with stop-first, all riding
  gamekit.save_backup
- schedules → restart/announce/backup cadences on core ScheduledJob rails;
  restarts broadcast the in-game countdown first (§3.2)
- events → log_events parse + crash detection → the core notify bus (§3.3)

Everything Docker is behind small module-level functions so the offline unit
suite can substitute fakes (the plan's "verify by code and layout" rule).
"""
import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime

import yaml

from app import paths
from app.models import Application
from app.plugins_sdk import db, logger, deploys, jobs, notify
from app.services.docker_service import DockerService
from app.services.firewall_service import FirewallService

from .models import MinecraftServer, MinecraftBackup, MinecraftSchedule
from .gamekit import compose as mc_compose
from .gamekit import players as mc_players
from .gamekit import rcon as mc_rcon
from .gamekit import config_form, log_events, save_backup

log = logger(__name__)

DEPLOY_KIND = 'minecraft.install'
INSTALL_STEPS = [
    'Generate server configuration',
    'Write app files',
    'Start Docker Compose stack',
    'Open firewall port',
    'Register application',
]

# Unified-job kinds the extension owns (registered at import, like DEPLOY_KIND).
SCHEDULE_JOB_KIND = 'minecraft.schedule'
EVENT_SCAN_JOB_KIND = 'minecraft.event_scan'
EVENT_SCAN_SCHEDULE_NAME = 'minecraft-event-scan'
EVENT_SCAN_INTERVAL_SECONDS = 60

# Scheduled restarts broadcast a countdown before the §3.4 sequence (§3.2).
RESTART_COUNTDOWN_SECONDS = 60


def register_deploy_kinds():
    """Contribute the install kind to the Deploy Console. Idempotent."""
    deploys.register(DEPLOY_KIND, run_install, replace=True)


def register_job_kinds():
    """Contribute the schedule-dispatch and event-scan kinds to the unified
    job system (core rails, D7). Idempotent."""
    jobs.register(SCHEDULE_JOB_KIND, run_scheduled_job, replace=True)
    jobs.register(EVENT_SCAN_JOB_KIND, run_event_scan, replace=True)


def register_notify_events():
    """Register the extension's events in the notify catalog so they render
    and preference-gate like core events (§3.3 — the existing notifications
    UI owns the per-category/channel preferences)."""
    for key, title, severity, category in (
        ('minecraft.player_join', 'Player joined {server}: {player}', 'info', 'apps'),
        ('minecraft.player_leave', 'Player left {server}: {player}', 'info', 'apps'),
        ('minecraft.server_started', 'Minecraft server is online: {server}', 'success', 'apps'),
        ('minecraft.server_crashed', 'Minecraft server crashed: {server}', 'critical', 'apps'),
        ('minecraft.backup_completed', 'World backup completed: {server}', 'success', 'backups'),
        ('minecraft.backup_failed', 'World backup failed: {server}', 'critical', 'backups'),
    ):
        try:
            notify.register_event(key, title, severity=severity, category=category)
        except Exception as exc:
            log.warning('Could not register notify event %s: %s', key, exc)


# --------------------------------------------------------------------------- #
# Small injectable seams (unit tests patch these)
# --------------------------------------------------------------------------- #

def _rcon_client(server):
    """An RCON client for *server* (loopback-only, D5). Java edition only."""
    return mc_rcon.RconClient(
        host='127.0.0.1',
        port=server.rcon_port or mc_compose.DEFAULT_RCON_PORT,
        password=server.rcon_password or '',
    )


def _run_rcon(server, command):
    with _rcon_client(server) as rc:
        return rc.command(command)


def app_path_for(name):
    return os.path.join(paths.APPS_DIR, name)


def port_available(port, protocol='tcp'):
    """True when nothing we know of holds *port* (panel DB + a bind probe).

    Three sources, same idea as the template installer's port picker: our own
    Minecraft rows, core Application rows, and a live socket bind (TCP for the
    Java game port/RCON, UDP for Bedrock — a TCP probe can't see a UDP
    listener and vice versa).
    """
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if not mc_compose.MIN_PORT <= port <= mc_compose.MAX_PORT:
        return False
    if MinecraftServer.query.filter_by(port=port).first():
        return False
    if Application.query.filter_by(port=port).first():
        return False
    sock_type = socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM
    try:
        probe = socket.socket(socket.AF_INET, sock_type)
        try:
            probe.bind(('0.0.0.0', port))
        finally:
            probe.close()
        return True
    except OSError:
        return False


def _pick_rcon_port():
    """First free loopback RCON port from 25575 up (per-server, D5)."""
    taken = {s.rcon_port for s in MinecraftServer.query.all() if s.rcon_port}
    candidate = mc_compose.next_free_port(mc_compose.DEFAULT_RCON_PORT, taken)
    attempts = 0
    while not port_available(candidate, 'tcp') and attempts < 100:
        candidate += 1
        attempts += 1
    return candidate


# --------------------------------------------------------------------------- #
# Create → compose → DeploymentJob (D4: rides the Deploy Console)
# --------------------------------------------------------------------------- #

def create_server(spec, user_id=None):
    """Validate the wizard spec, persist the row, queue the install job.

    Returns ``{'success', 'server', 'job_id'}`` — the frontend redirects to
    ``/deployments/<job_id>`` so the user watches the image pull and world
    generation live (D4). Validation errors come back as ``{'success': False,
    'errors': [...]}`` without anything being created.
    """
    spec = dict(spec or {})
    # Names arrive human-typed ('My Server'); make them container-safe slugs
    # before validation (lowercase, whitespace → dashes).
    spec['name'] = re.sub(r'\s+', '-', str(spec.get('name') or '').strip().lower())
    edition = spec.get('edition') or 'java'
    spec['edition'] = edition
    if edition == 'bedrock':
        spec['flavor'] = 'vanilla'
    spec['port'] = int(spec.get('port') or mc_compose.default_port(edition))

    errors = mc_compose.validate_spec(spec)
    if errors:
        return {'success': False, 'errors': errors}

    name = spec['name']
    if MinecraftServer.query.filter_by(name=name).first():
        return {'success': False, 'errors': [f'A Minecraft server named "{name}" already exists']}
    if Application.query.filter_by(name=name).first():
        return {'success': False, 'errors': [f'An application named "{name}" already exists']}

    if not port_available(spec['port'], mc_compose.port_protocol(edition)):
        suggestion = mc_compose.next_free_port(spec['port'] + 1, set())
        return {'success': False, 'errors': [
            f"Port {spec['port']} is already in use"
            + (f' — try {suggestion}' if port_available(suggestion, mc_compose.port_protocol(edition)) else '')]}

    if edition == 'java':
        spec['rcon_port'] = _pick_rcon_port()
        spec['rcon_password'] = secrets.token_urlsafe(24)

    server = MinecraftServer(
        name=name,
        edition=edition,
        flavor=spec.get('flavor') or 'vanilla',
        version=str(spec.get('version') or 'latest'),
        world_name=str(spec.get('world_name') or 'world'),
        seed=str(spec.get('seed') or '') or None,
        memory=mc_compose.normalize_memory(spec.get('memory') or mc_compose.DEFAULT_MEMORY),
        port=spec['port'],
        rcon_port=spec.get('rcon_port'),
        rcon_password=spec.get('rcon_password'),
        container_name=mc_compose.container_name(name),
        status='creating',
        stop_requested=False,
        eula_accepted=True,                      # validated user click (D3)
    )
    db.session.add(server)
    db.session.commit()

    result = deploys.start(
        DEPLOY_KIND,
        steps=INSTALL_STEPS,
        user_id=user_id,
        plan={
            'server_id': server.id,
            'spec': spec,
            'title': f'Install Minecraft server "{name}"',
        },
    )
    if not result.get('success'):
        server.status = 'error'
        db.session.commit()
        return {'success': False, 'errors': [result.get('error', 'Failed to queue deployment')],
                'server': server.to_dict()}

    server.deployment_job_id = result['job_id']
    db.session.commit()
    ensure_event_scan()
    return {'success': True, 'server': server.to_dict(), 'job_id': result['job_id']}


def run_install(job):
    """DeploymentJob handler for ``minecraft.install`` (Deploy Console, D4).

    Writes the generated compose + marker into the app dir, runs
    ``docker compose up`` with the transcript streamed to the console, opens
    the game port in the firewall (D5), and registers the core Application
    row the rest of the panel keys off (D9).
    """
    plan = job.get_plan() or {}
    server = MinecraftServer.query.get(plan.get('server_id'))
    if not server:
        return {'success': False, 'error': 'Minecraft server record not found'}
    spec = plan.get('spec') or {}
    app_path = app_path_for(server.name)

    try:
        with deploys.steps(job) as step:
            with step('Generate server configuration'):
                compose_doc = mc_compose.build_compose(spec)
                compose_yaml = yaml.safe_dump(compose_doc, default_flow_style=False,
                                              sort_keys=False)
                deploys.log(job, f"Image: {compose_doc['services']['minecraft']['image']}")
                deploys.log(job, f"Game port: {server.port}/"
                                 f"{mc_compose.port_protocol(server.edition)}")

            with step('Write app files'):
                os.makedirs(app_path, exist_ok=True)
                compose_file = os.path.join(app_path, 'docker-compose.yml')
                with open(compose_file, 'w', encoding='utf-8') as f:
                    f.write(compose_yaml)
                marker = os.path.join(app_path, '.serverkit-minecraft.json')
                # The marker records the spec minus the RCON secret — enough to
                # identify the install without leaking the password to a stray
                # reader.
                safe_spec = {k: v for k, v in spec.items() if k != 'rcon_password'}
                with open(marker, 'w', encoding='utf-8') as f:
                    json.dump({'extension': 'serverkit-minecraft',
                               'server_id': server.id,
                               'spec': safe_spec}, f, indent=2)
                if os.name != 'nt':
                    os.chmod(marker, 0o600)

            with step('Start Docker Compose stack'):
                up = DockerService.compose_up_streaming(
                    app_path, on_line=lambda line: deploys.log(job, line))
                if not up.get('success'):
                    raise RuntimeError(
                        f"docker compose up failed (exit {up.get('exit_code')})")

            with step('Open firewall port'):
                fw = FirewallService.allow_port(
                    server.port, mc_compose.port_protocol(server.edition))
                if not fw.get('success'):
                    deploys.log(job, f"Firewall: {fw.get('error')} — open port "
                                     f"{server.port} manually if friends can't connect",
                                'warn')

            with step('Register application'):
                app = Application(
                    name=server.name,
                    app_type='docker',
                    status='running',
                    root_path=app_path,
                    docker_image=compose_doc['services']['minecraft']['image'],
                    user_id=job.requested_by or 1,
                    port=server.port,
                )
                db.session.add(app)
                db.session.flush()
                server.application_id = app.id
                server.status = 'running'
                server.stop_requested = False
                db.session.commit()
                deploys.log(job, f'Server "{server.name}" is up — share '
                                 f'<server-ip>:{server.port} with your friends')

        return {'success': True, 'server_id': server.id}
    except Exception as exc:
        db.session.rollback()
        try:
            server.status = 'error'
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {'success': False, 'error': str(exc)}


# --------------------------------------------------------------------------- #
# Delete (container + volume confirm + firewall cleanup, D5)
# --------------------------------------------------------------------------- #

def delete_server(server, remove_volume=False):
    """Tear a server down. ``remove_volume`` also deletes the world volume —
    the confirm checkbox in the UI maps to it (worlds are unrecoverable).
    """
    # Graceful stop first if it looks alive (best-effort; teardown proceeds).
    try:
        state = _container_state(server)
        if state and state.get('running'):
            stop_server(server)
    except Exception as exc:
        log.warning('Pre-delete graceful stop failed for %s: %s', server.name, exc)

    app_path = app_path_for(server.name)
    compose_ok = False
    if os.path.isdir(app_path):
        down = DockerService.compose_down(app_path, volumes=remove_volume)
        compose_ok = down.get('success', False)
        if not compose_ok:
            log.warning('compose down failed for %s: %s', server.name, down.get('error'))

    if remove_volume and not compose_ok:
        # Fallback when the compose path was already gone.
        DockerService.remove_volume(mc_compose.volume_name(server.name), force=True)

    fw = FirewallService.deny_port(server.port, mc_compose.port_protocol(server.edition))
    if not fw.get('success'):
        log.warning('Firewall cleanup for port %s: %s', server.port, fw.get('error'))

    if server.application_id:
        app = Application.query.get(server.application_id)
        if app:
            db.session.delete(app)
    # Schedules die with the server (core rows + extension metadata). World
    # backups are deliberately KEPT — they may be the last copy of the world.
    for sched in MinecraftSchedule.query.filter_by(server_id=server.id).all():
        _delete_core_schedule(sched.job_name)
        db.session.delete(sched)
    db.session.delete(server)
    db.session.commit()

    # The app dir holds only files we wrote (compose + marker).
    if os.path.isdir(app_path):
        import shutil
        shutil.rmtree(app_path, ignore_errors=True)

    return {'success': True}


# --------------------------------------------------------------------------- #
# Lifecycle (§3.4 — save-before-stop, user-stop vs crash)
# --------------------------------------------------------------------------- #

def _container_info(server):
    if not server.container_name:
        return None
    return DockerService.get_container(server.container_name)


def _container_state(server):
    info = _container_info(server)
    if not info:
        return None
    state = info.get('State', {}) or {}
    return {
        'running': bool(state.get('Running')),
        'status': state.get('Status', 'unknown'),
        'exit_code': state.get('ExitCode'),
        'started_at': state.get('StartedAt'),
        'finished_at': state.get('FinishedAt'),
    }


def _quiesce_if_possible(server, action):
    """Run the §3.4 broadcast+save sequence; True when it fully ran.

    Java + reachable RCON only — Bedrock has no RCON and a dead server can't
    hear warnings, so both skip quietly (the image still gets its graceful
    SIGTERM window from stop_grace_period).
    """
    if server.edition != 'java':
        return False
    try:
        with _rcon_client(server) as rc:
            mc_players.quiesce(rc, action)
        return True
    except mc_rcon.RconError as exc:
        log.warning('Graceful %s broadcast skipped for %s: %s', action, server.name, exc)
        return False


def stop_server(server):
    """Broadcast → save-all flush → stop. Sets the user-stop marker (§3.4)."""
    server.stop_requested = True
    db.session.commit()
    _quiesce_if_possible(server, 'stop')
    result = DockerService.stop_container(server.container_name, timeout=60)
    if not result.get('success'):
        server.status = 'error'
        db.session.commit()
        return {'success': False, 'error': result.get('error', 'docker stop failed')}
    server.status = 'stopped'
    db.session.commit()
    return {'success': True, 'status': server.status}


def start_server(server):
    result = DockerService.start_container(server.container_name)
    if not result.get('success'):
        # Container gone but the compose project is still on disk — recreate.
        app_path = app_path_for(server.name)
        if os.path.isdir(app_path):
            result = DockerService.compose_up(app_path)
    if not result.get('success'):
        return {'success': False, 'error': result.get('error', 'docker start failed')}
    server.stop_requested = False
    server.status = 'running'
    db.session.commit()
    return {'success': True, 'status': server.status}


def restart_server(server):
    """Broadcast → save-all flush → restart. Clears the user-stop marker."""
    _quiesce_if_possible(server, 'restart')
    result = DockerService.restart_container(server.container_name, timeout=60)
    if not result.get('success'):
        return {'success': False, 'error': result.get('error', 'docker restart failed')}
    server.stop_requested = False
    server.status = 'running'
    db.session.commit()
    return {'success': True, 'status': server.status}


def reconcile_status(server):
    """Sync the row with Docker truth; distinguishes user-stop from crash.

    A container that isn't running without our stop marker is a crash
    (``crashed`` + exit code) — Phase 3's notification wiring keys off this.
    """
    state = _container_state(server)
    if state is None:
        return {'status': server.status, 'exit_code': None}
    if state['running']:
        new_status = 'running'
    elif server.stop_requested:
        new_status = 'stopped'
    else:
        new_status = 'crashed'
    if new_status != server.status:
        server.status = new_status
        db.session.commit()
    return {'status': new_status, 'exit_code': state['exit_code'],
            'started_at': state['started_at'], 'finished_at': state['finished_at']}


# --------------------------------------------------------------------------- #
# Players (Java/RCON only — Bedrock routes answer 400, documented asymmetry)
# --------------------------------------------------------------------------- #

def list_players(server):
    """RCON ``list`` → {online, max, players}."""
    return mc_players.parse_list_output(_run_rcon(server, 'list'))


def _player_command(server, verb, player, reason=None):
    if not mc_players.valid_player_name(player):
        return {'success': False, 'error': f'Invalid player name: {player!r}'}
    cmd = f'{verb} {player}'
    if reason:
        cmd += f' {reason}'
    output = _run_rcon(server, cmd)
    return {'success': True, 'command': cmd, 'output': output}


def kick_player(server, player, reason=None):
    return _player_command(server, 'kick', player, reason)


def ban_player(server, player, reason=None):
    return _player_command(server, 'ban', player, reason)


def pardon_player(server, player):
    return _player_command(server, 'pardon', player)


def op_player(server, player):
    return _player_command(server, 'op', player)


def deop_player(server, player):
    return _player_command(server, 'deop', player)


def whitelist(server, action, player=None):
    """Whitelist manager: enable|disable|add <p>|remove <p>|list."""
    if action in ('add', 'remove'):
        if not mc_players.valid_player_name(player):
            return {'success': False, 'error': f'Invalid player name: {player!r}'}
        cmd = f'whitelist {action} {player}'
    elif action == 'enable':
        cmd = 'whitelist on'
    elif action == 'disable':
        cmd = 'whitelist off'
    elif action == 'list':
        cmd = 'whitelist list'
    else:
        return {'success': False, 'error': f'Unknown whitelist action: {action}'}

    output = _run_rcon(server, cmd)
    result = {'success': True, 'command': cmd, 'output': output}
    if action == 'list':
        result['players'] = mc_players.parse_whitelist_output(output)
    return result


def banlist(server):
    return {'bans': mc_players.parse_banlist_output(_run_rcon(server, 'banlist'))}


def ops_list(server):
    """Operator names from /data/ops.json (no RCON query exists for ops).

    Best-effort via docker exec; an unreadable file (stopped server, Bedrock,
    non-Java layout) yields an empty list rather than an error.
    """
    if not server.container_name:
        return {'ops': []}
    result = DockerService.exec_command(server.container_name, 'cat /data/ops.json')
    if not result.get('success'):
        return {'ops': []}
    try:
        entries = json.loads(result.get('stdout') or '[]')
        return {'ops': [e.get('name') for e in entries if isinstance(e, dict) and e.get('name')]}
    except (ValueError, TypeError):
        return {'ops': []}


def whitelist_enabled(server):
    """white-list flag from the live server.properties (None when unknown)."""
    if not server.container_name:
        return None
    result = DockerService.exec_command(server.container_name, 'cat /data/server.properties')
    if not result.get('success'):
        return None
    from .gamekit import config_form
    props = config_form.parse_properties(result.get('stdout') or '')
    raw = props.get('white-list')
    return None if raw is None else raw.lower() == 'true'


# --------------------------------------------------------------------------- #
# Overview (status + uptime + players + docker stats + share address)
# --------------------------------------------------------------------------- #

_SIZE_UNITS = {
    'b': 1, 'kb': 1024, 'mb': 1024 ** 2, 'gb': 1024 ** 3, 'tib': 1024 ** 4,
    'kib': 1024, 'mib': 1024 ** 2, 'gib': 1024 ** 3,
}
_SIZE_RE = re.compile(r'^\s*([\d.]+)\s*([A-Za-z]+)\s*$')


def parse_docker_size(text):
    """'1.23GiB' → bytes. Docker stats sizes are IEC (MiB/GiB)."""
    m = _SIZE_RE.match(text or '')
    if not m:
        return None
    factor = _SIZE_UNITS.get(m.group(2).lower())
    return None if factor is None else int(float(m.group(1)) * factor)


def parse_docker_stats(raw):
    """Normalize one ``docker stats --no-stream --format {{json .}}`` row.

    → {'cpu_percent', 'mem_usage_bytes', 'mem_limit_bytes', 'mem_percent'}
    with Nones where Docker gave us nothing parseable (never fake a metric,
    §3.5).
    """
    if not isinstance(raw, dict):
        return None
    mem_usage = mem_limit = None
    mem_usage_raw = raw.get('MemUsage') or ''
    if '/' in mem_usage_raw:
        used_part, _, limit_part = mem_usage_raw.partition('/')
        mem_usage = parse_docker_size(used_part)
        mem_limit = parse_docker_size(limit_part)

    def percent(key):
        try:
            return float(str(raw.get(key) or '').rstrip('%'))
        except (TypeError, ValueError):
            return None

    return {
        'cpu_percent': percent('CPUPerc'),
        'mem_usage_bytes': mem_usage,
        'mem_limit_bytes': mem_limit,
        'mem_percent': percent('MemPerc'),
    }


def _uptime_seconds(started_at):
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at).replace('Z', '+00:00'))
        delta = datetime.now(started.tzinfo) - started
        return max(0, int(delta.total_seconds()))
    except (ValueError, TypeError):
        return None


def public_host(fallback=None):
    """Address stem for the share card: the configured public IP/host first."""
    try:
        from app.models.system_settings import SystemSettings
        configured = SystemSettings.get('server_public_ip')
        if configured:
            return configured
    except Exception:
        pass
    return fallback


def get_overview(server, host_fallback=None):
    """Everything the Overview tab renders, in one call.

    Live pieces (container state, stats, RCON list) are best-effort: a stopped
    or crashed server still returns a full shape with nulls, so the tab never
    has to special-case a dead server.
    """
    state = reconcile_status(server)
    running = state['status'] == 'running'

    stats = None
    if running and server.container_name:
        stats = parse_docker_stats(DockerService.get_container_stats(server.container_name))

    players = {'online': None, 'max': None, 'players': []}
    if running and server.edition == 'java':
        try:
            players = list_players(server)
        except mc_rcon.RconError as exc:
            log.info('RCON list failed for %s: %s', server.name, exc)

    host = public_host(host_fallback)
    address = f'{host}:{server.port}' if host else None

    return {
        'server': server.to_dict(),
        'status': state['status'],
        'exit_code': state.get('exit_code'),
        'uptime_seconds': _uptime_seconds(state.get('started_at')) if running else None,
        'players': players,
        'stats': stats,
        'address': address,
        'next_restart_at': next_restart_at(server),
    }


def get_logs(server, tail=200):
    """Container log tail for the Console tab (the frontend polls while open)."""
    if not server.container_name:
        return {'success': False, 'error': 'Server has no container yet'}
    try:
        tail = max(1, min(int(tail), 1000))
    except (TypeError, ValueError):
        tail = 200
    return DockerService.get_container_logs(server.container_name, tail=tail)


# --------------------------------------------------------------------------- #
# Settings (§3.2): server.properties as a grouped form (gamekit config_form)
# --------------------------------------------------------------------------- #

PROPERTIES_PATH = '/data/server.properties'

_properties_meta_cache = None


def _properties_meta():
    """The sidecar metadata (groups/labels/descriptions/restart-required)."""
    global _properties_meta_cache
    if _properties_meta_cache is None:
        sidecar = os.path.join(os.path.dirname(__file__), 'gamekit',
                               'server_properties_meta.json')
        with open(sidecar, 'r', encoding='utf-8') as f:
            _properties_meta_cache = json.load(f)
    return _properties_meta_cache


def _read_container_file(server, path):
    """File contents from the container, or None when unreadable."""
    if not server.container_name:
        return None
    result = DockerService.exec_command(server.container_name, f'cat {path}')
    if not result.get('success'):
        return None
    return result.get('stdout')


def _write_container_file(server, path, content):
    """Write a file in the container. Base64 rides through shlex-split exec
    untouched (its alphabet has no shell metacharacters), sidestepping every
    quoting problem that raw content would have."""
    encoded = base64.b64encode(content.encode('utf-8')).decode('ascii')
    result = DockerService.exec_command(
        server.container_name,
        f"sh -c 'echo {encoded} | base64 -d > {path}'")
    return bool(result.get('success'))


def get_settings(server):
    """The grouped settings form model for the Settings tab.

    Reading needs a running container (docker exec); a stopped server gets an
    honest 409-style error rather than a silently stale file.
    """
    text = _read_container_file(server, PROPERTIES_PATH)
    if text is None:
        return {'success': False,
                'error': 'Settings can only be read while the server is running'}
    return {'success': True, 'form': config_form.build_form(text, _properties_meta())}


def update_settings(server, changes):
    """Apply {key: value} changes to server.properties; flags restart-required.

    The file is the source of truth (gamekit contract). Keys our compose
    manages via env (EULA/LEVEL/SEED/…) are reapplied by the image on every
    start, so changing them here would not survive a restart — those are
    refused rather than silently lost.
    """
    if not isinstance(changes, dict) or not changes:
        return {'success': False, 'error': 'changes object required'}

    env_managed = {'eula', 'level-name', 'level-seed', 'online-mode',
                   'enable-rcon', 'rcon.password', 'rcon.port', 'server-port'}
    refused = sorted(k for k in changes if k in env_managed)
    if refused:
        return {'success': False,
                'error': 'These keys are managed by the container environment '
                         f'and cannot be edited here: {", ".join(refused)}'}

    text = _read_container_file(server, PROPERTIES_PATH)
    if text is None:
        return {'success': False,
                'error': 'Settings can only be changed while the server is running'}

    old_props = config_form.parse_properties(text)
    new_text = config_form.apply_changes(text, changes)
    if not _write_container_file(server, PROPERTIES_PATH, new_text):
        return {'success': False, 'error': 'Failed to write server.properties'}

    # Flag only restart-required keys whose value actually changed.
    field_meta = (_properties_meta().get('fields') or {})
    restart_keys = []
    for key, value in changes.items():
        rendered = ('true' if value is True else
                    'false' if value is False else str(value))
        if (field_meta.get(key, {}).get('restart_required')
                and old_props.get(key) != rendered):
            restart_keys.append(key)

    return {'success': True,
            'restart_required': bool(restart_keys),
            'restart_keys': restart_keys}


# --------------------------------------------------------------------------- #
# Backups (§3.2): quiesce → copy-then-zip → resume, retention, restore
# --------------------------------------------------------------------------- #

def backups_dir_for(server):
    return os.path.join(paths.SERVERKIT_BACKUP_DIR, 'minecraft', server.name)


def world_container_paths(server):
    """Where the world lives inside the container, per edition/layout.

    Java: the level dir plus its dimension siblings (level_nether /
    level_the_end) — backing up only the overworld silently loses the rest.
    Bedrock: worlds/<level>.
    """
    level = server.world_name or 'world'
    if server.edition == 'bedrock':
        return [f'/data/worlds/{level}']
    return [f'/data/{level}', f'/data/{level}_nether', f'/data/{level}_the_end']


def _docker_cp(src, dst):
    """docker cp wrapper (module-level so tests substitute it)."""
    result = subprocess.run(['docker', 'cp', src, dst],
                            capture_output=True, text=True, timeout=600)
    return {'success': result.returncode == 0, 'error': result.stderr}


def _container_path_exists(server, path):
    result = DockerService.exec_command(server.container_name, f'test -d {path}')
    return bool(result.get('success'))


def _quiescing_rcon(server):
    """A connected+authed RCON client for the backup window, or None.

    Java + running: RCON must work — a hot copy without quiesce can tear the
    world, so the caller treats a failure here as fatal. Java + stopped and
    Bedrock (no RCON at all) get None: their copy is already cold/consistent.
    """
    if server.edition != 'java':
        return None
    state = _container_state(server)
    if not (state and state.get('running')):
        return None
    rc = _rcon_client(server)
    rc.connect()
    rc.authenticate()
    return rc


def run_backup(server, kind='manual', when=None):
    """The §3.2 hot backup: quiesce → copy out of the container → resume →
    zip the copy (locks are released by then) → retention → record + notify.

    Returns a descriptor dict; ``skipped`` when the world is empty and the
    skip-when-empty option is on.
    """
    when = when or datetime.utcnow()
    retention = server.backup_retention if server.backup_retention is not None else 5
    skip_empty = server.backup_skip_empty if server.backup_skip_empty is not None else True

    dest_dir = backups_dir_for(server)
    os.makedirs(dest_dir, exist_ok=True)
    staging = tempfile.mkdtemp(prefix='mc-world-')

    rc = None
    try:
        try:
            rc = _quiescing_rcon(server)
        except mc_rcon.RconError as exc:
            result = {'success': False,
                      'error': f'Could not quiesce the server for a safe backup: {exc}'}
            _notify_backup(server, result)
            return result

        def copy_world():
            copied = []
            for cpath in world_container_paths(server):
                if _container_path_exists(server, cpath):
                    out = _docker_cp(f'{server.container_name}:{cpath}', staging)
                    if not out['success']:
                        raise RuntimeError(f"docker cp failed for {cpath}: {out.get('error')}")
                    copied.append(cpath)
            if not copied:
                raise RuntimeError('World not found in the container yet '
                                   '(has the server finished starting?)')
            copy_world.copied = copied

        commands = save_backup.run_quiesced(rc, copy_world)

        if skip_empty and save_backup.world_is_empty(staging):
            return {'success': True, 'skipped': True, 'reason': 'world empty',
                    'commands': commands}

        name = save_backup.archive_name(server.world_name, server.version, when)
        archive_path = os.path.join(dest_dir, name)
        save_backup.zip_dir(staging, archive_path)
        pruned = save_backup.apply_retention(dest_dir, retention) if retention else []

        record = MinecraftBackup(
            server_id=server.id, name=name, file_path=archive_path,
            size_bytes=os.path.getsize(archive_path), kind=kind,
        )
        db.session.add(record)
        db.session.commit()

        result = {'success': True, 'skipped': False, 'backup': record.to_dict(),
                  'pruned': pruned, 'commands': commands,
                  'paths': getattr(copy_world, 'copied', [])}
        _notify_backup(server, result)
        return result
    except Exception as exc:
        result = {'success': False, 'error': str(exc)}
        _notify_backup(server, result)
        return result
    finally:
        if rc is not None:
            rc.close()
        shutil.rmtree(staging, ignore_errors=True)


def _notify_backup(server, result):
    """Backup success/failure → the notify bus (§3.3)."""
    try:
        if result.get('success'):
            if result.get('skipped'):
                return
            notify.send('minecraft.backup_completed', to='admins', data={
                'server': server.name,
                'backup': (result.get('backup') or {}).get('name'),
                'summary': f"World backup of {server.name} completed: "
                           f"{(result.get('backup') or {}).get('name')}",
            })
        else:
            notify.send('minecraft.backup_failed', to='admins', data={
                'server': server.name,
                'summary': f"World backup of {server.name} failed: {result.get('error')}",
            })
    except Exception as exc:
        log.warning('Backup notification failed for %s: %s', server.name, exc)


def _safe_extract(archive_path, dest_dir):
    """Extract a backup zip, refusing path escapes (zip-slip)."""
    with zipfile.ZipFile(archive_path) as zf:
        for member in zf.namelist():
            target = os.path.realpath(os.path.join(dest_dir, member))
            if not target.startswith(os.path.realpath(dest_dir) + os.sep):
                raise RuntimeError(f'Unsafe path in archive: {member}')
        zf.extractall(dest_dir)


def restore_backup(server, backup):
    """Stop-first restore (§3.2): graceful stop → swap the world dirs in the
    volume from the archive → start again. The confirm lives in the UI; by
    the time this runs the user has already said yes."""
    if not os.path.isfile(backup.file_path or ''):
        return {'success': False, 'error': 'Backup archive is missing on disk'}

    state = _container_state(server)
    was_running = bool(state and state.get('running'))
    if was_running:
        stopped = stop_server(server)
        if not stopped.get('success'):
            return {'success': False,
                    'error': f"Could not stop the server first: {stopped.get('error')}"}

    staging = tempfile.mkdtemp(prefix='mc-restore-')
    try:
        _safe_extract(backup.file_path, staging)
        restored = []
        for entry in sorted(os.listdir(staging)):
            staged_dir = os.path.join(staging, entry)
            if not os.path.isdir(staged_dir):
                continue
            if server.edition == 'bedrock':
                DockerService.exec_command(server.container_name,
                                           'mkdir -p /data/worlds')
                parent = '/data/worlds'
            else:
                parent = '/data'
            target = f'{parent}/{entry}'
            DockerService.exec_command(server.container_name, f'rm -rf {target}')
            out = _docker_cp(staged_dir, f'{server.container_name}:{parent}/')
            if not out['success']:
                raise RuntimeError(f"docker cp failed for {entry}: {out.get('error')}")
            restored.append(entry)
        if not restored:
            raise RuntimeError('Backup archive contained no world directories')
    except Exception as exc:
        return {'success': False, 'error': str(exc)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    started = start_server(server)
    return {'success': True, 'restored': restored,
            'restarted': bool(started.get('success'))}


def delete_backup(server, backup):
    """Remove the archive (best-effort) and its record."""
    if backup.file_path:
        try:
            os.remove(backup.file_path)
        except OSError:
            pass
    db.session.delete(backup)
    db.session.commit()
    return {'success': True}


def update_backup_config(server, retention=None, skip_when_empty=None):
    """Backups-tab options: retention count + skip-when-empty."""
    if retention is not None:
        try:
            retention = int(retention)
        except (TypeError, ValueError):
            return {'success': False, 'error': 'retention must be a number'}
        if not 0 <= retention <= 100:
            return {'success': False, 'error': 'retention must be between 0 and 100'}
        server.backup_retention = retention
    if skip_when_empty is not None:
        server.backup_skip_empty = bool(skip_when_empty)
    db.session.commit()
    return {'success': True, 'retention': server.backup_retention,
            'skip_when_empty': server.backup_skip_empty}


# --------------------------------------------------------------------------- #
# Schedules (§3.2): restart/announce/backup on core ScheduledJob rails
# --------------------------------------------------------------------------- #

def _valid_cron(cron):
    try:
        from croniter import croniter
        return bool(cron) and croniter.is_valid(cron)
    except ImportError:
        return bool(cron)


def _core_schedule(job_name):
    from app.jobs.models import ScheduledJob
    return ScheduledJob.query.filter_by(name=job_name).first()


def _delete_core_schedule(job_name):
    row = _core_schedule(job_name)
    if row:
        db.session.delete(row)
        db.session.flush()


def list_schedules(server):
    """Extension rows joined with their core ScheduledJob (next/last run)."""
    out = []
    rows = (MinecraftSchedule.query
            .filter_by(server_id=server.id)
            .order_by(MinecraftSchedule.created_at).all())
    for sched in rows:
        data = sched.to_dict()
        core = _core_schedule(sched.job_name)
        data['next_run_at'] = core.next_run_at.isoformat() if core and core.next_run_at else None
        data['last_run_at'] = core.last_run_at.isoformat() if core and core.last_run_at else None
        out.append(data)
    return out


def create_schedule(server, type_, cron, message=None, label=None, enabled=True):
    """Create a schedule: extension metadata row + core ScheduledJob cadence."""
    if type_ not in MinecraftSchedule.TYPES:
        return {'success': False,
                'error': f"type must be one of: {', '.join(MinecraftSchedule.TYPES)}"}
    if not _valid_cron(cron):
        return {'success': False, 'error': 'A valid cron expression is required'}
    if type_ == 'announce' and not (message or '').strip():
        return {'success': False, 'error': 'Announcement schedules need a message'}

    sched = MinecraftSchedule(
        server_id=server.id, type=type_, cron=cron.strip(),
        message=(message or '').strip() or None,
        label=(label or '').strip() or None, enabled=bool(enabled),
    )
    db.session.add(sched)
    db.session.flush()  # assign id for the core job name

    sched.job_name = f'minecraft-{server.id}-{sched.id}'
    jobs.schedule(sched.job_name, SCHEDULE_JOB_KIND, cron=sched.cron,
                  payload={'schedule_id': sched.id})
    core = _core_schedule(sched.job_name)
    if core:
        # ensure() seeds next_run_at to "now" (right for interval tasks, wrong
        # for a 4am restart) — recompute the first run from the cron itself.
        core.next_run_at = core.compute_next_run()
        if not sched.enabled:
            from app.jobs.service import ScheduledJobService
            ScheduledJobService.set_enabled(core.id, False)
    db.session.commit()
    return {'success': True, 'schedule': sched.to_dict()}


def update_schedule(schedule, cron=None, enabled=None, message=None, label=None):
    if cron is not None:
        if not _valid_cron(cron):
            return {'success': False, 'error': 'A valid cron expression is required'}
        schedule.cron = cron.strip()
        # ensure() updates the cadence but preserves next_run_at/enabled (core
        # upsert semantics) — then re-anchor the next run to the new cron.
        jobs.schedule(schedule.job_name, SCHEDULE_JOB_KIND, cron=schedule.cron,
                      payload={'schedule_id': schedule.id})
        core = _core_schedule(schedule.job_name)
        if core:
            core.next_run_at = core.compute_next_run()
    if message is not None:
        schedule.message = message.strip() or None
    if label is not None:
        schedule.label = label.strip() or None
    if enabled is not None:
        schedule.enabled = bool(enabled)
        from app.jobs.service import ScheduledJobService
        core = _core_schedule(schedule.job_name)
        if core:
            ScheduledJobService.set_enabled(core.id, schedule.enabled)
    db.session.commit()
    return {'success': True, 'schedule': schedule.to_dict()}


def delete_schedule(schedule):
    _delete_core_schedule(schedule.job_name)
    db.session.delete(schedule)
    db.session.commit()
    return {'success': True}


def run_scheduled_job(job):
    """Unified-job handler for ``minecraft.schedule`` — dispatches on the
    extension schedule row the payload points at."""
    payload = job.get_payload() or {}
    sched = MinecraftSchedule.query.get(payload.get('schedule_id'))
    if not sched or not sched.enabled:
        return {'skipped': True}
    server = MinecraftServer.query.get(sched.server_id)
    if not server:
        return {'skipped': True}

    if sched.type == 'announce':
        if server.edition != 'java':
            return {'skipped': True, 'reason': 'no RCON on Bedrock'}
        output = _run_rcon(server, f'say {sched.message}')
        return {'success': True, 'output': output}
    if sched.type == 'restart':
        return scheduled_restart(server)
    if sched.type == 'backup':
        return run_backup(server, kind='scheduled')
    return {'skipped': True, 'reason': f'unknown type {sched.type}'}


def scheduled_restart(server, countdown_seconds=RESTART_COUNTDOWN_SECONDS,
                      sleep=None):
    """Countdown broadcast (say … 5…1 min) then the §3.4 graceful restart.

    ``sleep`` is injectable so tests run the full plan instantly. Bedrock has
    no RCON to broadcast through — it goes straight to the graceful restart.
    """
    sleep = time.sleep if sleep is None else sleep
    broadcasts = []
    if server.edition == 'java':
        try:
            with _rcon_client(server) as rc:
                elapsed = 0
                for offset, cmd in mc_players.countdown_broadcasts(countdown_seconds):
                    if offset > elapsed:
                        sleep(offset - elapsed)
                        elapsed = offset
                    rc.command(cmd)
                    broadcasts.append(cmd)
                # Sleep out the final segment so the restart actually lands
                # when the last warning said it would.
                if elapsed < countdown_seconds:
                    sleep(countdown_seconds - elapsed)
        except mc_rcon.RconError as exc:
            log.warning('Restart countdown skipped for %s: %s', server.name, exc)
    result = restart_server(server)
    result['broadcasts'] = broadcasts
    return result


def next_restart_at(server):
    """Soonest enabled restart schedule's next run (ISO), or None."""
    rows = MinecraftSchedule.query.filter_by(
        server_id=server.id, type='restart', enabled=True).all()
    nexts = []
    for sched in rows:
        core = _core_schedule(sched.job_name)
        if core and core.next_run_at:
            nexts.append(core.next_run_at)
    return min(nexts).isoformat() if nexts else None


# --------------------------------------------------------------------------- #
# Events → notify bus (§3.3): join/leave/started from the log, crash from the
# stop marker, backup outcomes from run_backup above.
# --------------------------------------------------------------------------- #

# server_id -> ISO timestamp of the last scanned log line window. In-memory is
# deliberate: a panel restart just rescans one small window (docker --since),
# and the events are informational, not ledger entries.
_scan_state = {}


def ensure_event_scan():
    """The single interval schedule that scans all servers' logs for events."""
    try:
        jobs.schedule(EVENT_SCAN_SCHEDULE_NAME, EVENT_SCAN_JOB_KIND,
                      interval_seconds=EVENT_SCAN_INTERVAL_SECONDS)
    except Exception as exc:
        log.warning('Could not ensure event-scan schedule: %s', exc)


def run_event_scan(_job=None):
    """Unified-job handler: scan every server's recent log + crash state."""
    scanned = 0
    for server in MinecraftServer.query.all():
        try:
            _scan_server(server)
            scanned += 1
        except Exception as exc:
            log.warning('Event scan failed for %s: %s', server.name, exc)
    return {'scanned': scanned}


def _scan_server(server):
    """Crash transitions (stop-marker aware) + log events for one server."""
    previous = server.status
    state = reconcile_status(server)
    if previous == 'running' and state['status'] == 'crashed':
        _notify_event('minecraft.server_crashed', server, {
            'exit_code': state.get('exit_code'),
            'summary': f"{server.name} stopped unexpectedly "
                       f"(exit {state.get('exit_code')})",
        })

    if server.edition != 'java' or state['status'] != 'running':
        return

    since = _scan_state.get(server.id)
    _scan_state[server.id] = datetime.utcnow().isoformat()
    logs = DockerService.get_container_logs(
        server.container_name, tail=500, since=since or f'{EVENT_SCAN_INTERVAL_SECONDS + 10}s')
    if not logs.get('success'):
        return
    for event in log_events.parse_lines(logs.get('logs', '').splitlines()):
        if event['event'] == 'player_join':
            _notify_event('minecraft.player_join', server, {
                'player': event.get('player'),
                'summary': f"{event.get('player')} joined {server.name}",
            })
        elif event['event'] == 'player_leave':
            _notify_event('minecraft.player_leave', server, {
                'player': event.get('player'),
                'summary': f"{event.get('player')} left {server.name}",
            })
        elif event['event'] == 'server_started':
            _notify_event('minecraft.server_started', server, {
                'summary': f'{server.name} finished starting and is online',
            })


def _notify_event(event_key, server, data):
    try:
        notify.send(event_key, to='admins',
                    data={'server': server.name, **data})
    except Exception as exc:
        log.warning('Notification %s failed for %s: %s', event_key, server.name, exc)


register_deploy_kinds()
register_job_kinds()
register_notify_events()
