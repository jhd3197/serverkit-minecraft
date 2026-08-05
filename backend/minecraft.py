"""serverkit-minecraft API blueprint (plan 53).

The Phase 1-3 surface: create wizard → compose → DeploymentJob (D4, the
Deploy Console), delete with volume confirm + firewall cleanup (D5), §3.4
lifecycle (broadcast → save-all flush → stop; user-stop vs crash marker),
players management over RCON, overview/logs for the detail tabs, settings
(server.properties as a grouped form with restart-required flagging), backups
(hot-backup sequence + retention + stop-first restore), and schedules on core
cron rails with the in-game countdown. Notifications (join/leave/crash/
backup) flow through the core notify bus from server_service.

Routes are mounted at /api/v1/minecraft (manifest url_prefix). Guards use the
rbac decorators (API-key friendly), never bare flask @jwt_required.
"""
from flask import Blueprint, request, jsonify

from app.middleware.rbac import auth_required, admin_required, get_current_user
from app.plugins_sdk import logger
from app.plugins_sdk import db  # noqa: F401  (blueprint module pattern parity)

from .models import MinecraftServer, MinecraftBackup
from . import gamekit, server_service

minecraft_bp = Blueprint('minecraft', __name__)
log = logger(__name__)


def _server_or_404(server_id):
    return MinecraftServer.query.get(server_id)


def _java_only(server):
    """Bedrock has no RCON in the default image path (documented asymmetry)."""
    if server.edition == 'bedrock':
        return jsonify({'error': 'This action needs RCON, which the Bedrock '
                                 'edition does not provide'}), 400
    return None


def _rcon_errors(fn):
    """Map RCON failures to 502s instead of 500s."""
    try:
        return fn()
    except gamekit.rcon.RconAuthError:
        return jsonify({'error': 'RCON authentication failed'}), 502
    except gamekit.rcon.RconError as e:
        return jsonify({'error': f'RCON error: {e}'}), 502


# --------------------------------------------------------------------------- #
# Servers: list / create / get / delete
# --------------------------------------------------------------------------- #

@minecraft_bp.route('', methods=['GET'])
@minecraft_bp.route('/', methods=['GET'])
@auth_required()
def list_servers():
    servers = MinecraftServer.query.order_by(MinecraftServer.created_at.desc()).all()
    return jsonify({'servers': [s.to_dict() for s in servers]})


@minecraft_bp.route('', methods=['POST'])
@admin_required
def create_server():
    """Create wizard submit (§3.1). Body: {name, edition, flavor, version,
    world_name, seed, memory, port, eula_accepted}. EULA must be explicitly
    accepted (D3). Returns the server row + the DeploymentJob id — the wizard
    redirects to the Deploy Console to watch the install live (D4)."""
    user = get_current_user()
    spec = request.get_json(silent=True) or {}
    result = server_service.create_server(spec, user_id=user.id if user else None)
    if not result.get('success'):
        return jsonify({'error': '; '.join(result.get('errors') or ['Invalid request']),
                        'errors': result.get('errors')}), 400
    job_id = result['job_id']
    return jsonify({
        'server': result['server'],
        'job_id': job_id,
        'deploy_url': f'/deployments/{job_id}',
    }), 201


@minecraft_bp.route('/port-check', methods=['GET'])
@auth_required()
def port_check():
    """Wizard port availability probe: ?port=25565&edition=java."""
    try:
        port = int(request.args.get('port') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'port must be a number'}), 400
    edition = request.args.get('edition') or 'java'
    protocol = gamekit.compose.port_protocol(edition)
    available = server_service.port_available(port, protocol)
    suggestion = None
    if not available:
        candidate = gamekit.compose.next_free_port(port + 1, set())
        if server_service.port_available(candidate, protocol):
            suggestion = candidate
    return jsonify({'port': port, 'protocol': protocol,
                    'available': available, 'suggestion': suggestion})


@minecraft_bp.route('/<int:server_id>', methods=['GET'])
@auth_required()
def get_server(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    return jsonify(server.to_dict())


@minecraft_bp.route('/<int:server_id>', methods=['DELETE'])
@admin_required
def delete_server(server_id):
    """Teardown: graceful stop → compose down (optionally -v for the world
    volume) → firewall cleanup → rows removed. ?remove_volume=1 deletes the
    world — the UI confirm checkbox maps to it."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    remove_volume = request.args.get('remove_volume') in ('1', 'true', 'yes')
    result = server_service.delete_server(server, remove_volume=remove_volume)
    return jsonify(result)


# --------------------------------------------------------------------------- #
# Lifecycle (§3.4)
# --------------------------------------------------------------------------- #

@minecraft_bp.route('/<int:server_id>/start', methods=['POST'])
@admin_required
def start_server(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    result = server_service.start_server(server)
    return jsonify(result), 200 if result.get('success') else 502


@minecraft_bp.route('/<int:server_id>/stop', methods=['POST'])
@admin_required
def stop_server(server_id):
    """Graceful stop: RCON broadcast → save-all flush → docker stop, with the
    user-stop marker set so a stopped server never reads as a crash."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    result = server_service.stop_server(server)
    return jsonify(result), 200 if result.get('success') else 502


@minecraft_bp.route('/<int:server_id>/restart', methods=['POST'])
@admin_required
def restart_server(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    result = server_service.restart_server(server)
    return jsonify(result), 200 if result.get('success') else 502


# --------------------------------------------------------------------------- #
# Runtime surfaces: overview, logs, RCON console
# --------------------------------------------------------------------------- #

@minecraft_bp.route('/<int:server_id>/overview', methods=['GET'])
@auth_required()
def get_overview(server_id):
    """Status (reconciled with Docker, crash-aware), uptime, players online/max,
    docker stats, version/flavor/world, and the share-card address."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    return jsonify(server_service.get_overview(server, host_fallback=request.host))


@minecraft_bp.route('/<int:server_id>/logs', methods=['GET'])
@auth_required()
def get_logs(server_id):
    """Container log tail for the Console tab (polled while the tab is open —
    no background streaming when nobody's looking, §3.5)."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    result = server_service.get_logs(server, tail=request.args.get('tail', 200))
    return jsonify(result), 200 if result.get('success') else 502


@minecraft_bp.route('/<int:server_id>/rcon', methods=['POST'])
@admin_required
def run_rcon(server_id):
    """Proxy a single RCON command to the server (loopback-only, D5).

    The panel talks to RCON server-side; the port is never published. Java
    edition only — Bedrock's default image has no RCON (documented asymmetry).
    """
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    blocked = _java_only(server)
    if blocked:
        return blocked

    cmd = (request.get_json(silent=True) or {}).get('command', '').strip()
    if not cmd:
        return jsonify({'error': 'command required'}), 400

    def run():
        output = server_service._run_rcon(server, cmd)
        return jsonify({'command': cmd, 'output': output})

    return _rcon_errors(run)


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #

@minecraft_bp.route('/<int:server_id>/players', methods=['GET'])
@auth_required()
def list_players(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    blocked = _java_only(server)
    if blocked:
        return blocked
    return _rcon_errors(lambda: jsonify(server_service.list_players(server)))


def _player_action(server_id, action):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    blocked = _java_only(server)
    if blocked:
        return blocked
    body = request.get_json(silent=True) or {}
    player = (body.get('player') or '').strip()
    if not player:
        return jsonify({'error': 'player required'}), 400
    reason = (body.get('reason') or '').strip() or None

    def run():
        result = action(server, player, reason) if action in (
            server_service.kick_player, server_service.ban_player) else action(server, player)
        status = 200 if result.get('success') else 400
        return jsonify(result), status

    return _rcon_errors(run)


@minecraft_bp.route('/<int:server_id>/players/kick', methods=['POST'])
@admin_required
def kick_player(server_id):
    return _player_action(server_id, server_service.kick_player)


@minecraft_bp.route('/<int:server_id>/players/ban', methods=['POST'])
@admin_required
def ban_player(server_id):
    return _player_action(server_id, server_service.ban_player)


@minecraft_bp.route('/<int:server_id>/players/pardon', methods=['POST'])
@admin_required
def pardon_player(server_id):
    return _player_action(server_id, server_service.pardon_player)


@minecraft_bp.route('/<int:server_id>/players/op', methods=['POST'])
@admin_required
def op_player(server_id):
    return _player_action(server_id, server_service.op_player)


@minecraft_bp.route('/<int:server_id>/players/deop', methods=['POST'])
@admin_required
def deop_player(server_id):
    return _player_action(server_id, server_service.deop_player)


@minecraft_bp.route('/<int:server_id>/players/bans', methods=['GET'])
@auth_required()
def ban_list(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    blocked = _java_only(server)
    if blocked:
        return blocked
    return _rcon_errors(lambda: jsonify(server_service.banlist(server)))


@minecraft_bp.route('/<int:server_id>/players/ops', methods=['GET'])
@auth_required()
def ops_list(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    blocked = _java_only(server)
    if blocked:
        return blocked
    return jsonify(server_service.ops_list(server))


@minecraft_bp.route('/<int:server_id>/whitelist', methods=['GET'])
@auth_required()
def get_whitelist(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    blocked = _java_only(server)
    if blocked:
        return blocked

    def run():
        listed = server_service.whitelist(server, 'list')
        return jsonify({
            'enabled': server_service.whitelist_enabled(server),
            'players': listed.get('players', []),
        })

    return _rcon_errors(run)


@minecraft_bp.route('/<int:server_id>/whitelist', methods=['POST'])
@admin_required
def update_whitelist(server_id):
    """Body: {action: 'enable'|'disable'|'add'|'remove', player?}."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    blocked = _java_only(server)
    if blocked:
        return blocked
    body = request.get_json(silent=True) or {}
    action = (body.get('action') or '').strip()
    player = (body.get('player') or '').strip() or None

    def run():
        result = server_service.whitelist(server, action, player)
        return jsonify(result), 200 if result.get('success') else 400

    return _rcon_errors(run)


# --------------------------------------------------------------------------- #
# Settings (server.properties as a grouped form)
# --------------------------------------------------------------------------- #

@minecraft_bp.route('/<int:server_id>/settings', methods=['GET'])
@auth_required()
def get_settings(server_id):
    """Grouped form model (label/description/group/restart-required) built from
    the live server.properties + the sidecar metadata."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    result = server_service.get_settings(server)
    return jsonify(result), 200 if result.get('success') else 409


@minecraft_bp.route('/<int:server_id>/settings', methods=['PUT'])
@admin_required
def update_settings(server_id):
    """Apply {changes: {key: value}}. The response flags restart_required when
    a changed key needs one — the UI then offers "Restart now" (the graceful
    §3.4 restart route)."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    changes = (request.get_json(silent=True) or {}).get('changes')
    result = server_service.update_settings(server, changes)
    return jsonify(result), 200 if result.get('success') else 400


# --------------------------------------------------------------------------- #
# Backups
# --------------------------------------------------------------------------- #

def _backup_or_404(server_id, backup_id):
    return MinecraftBackup.query.filter_by(id=backup_id, server_id=server_id).first()


@minecraft_bp.route('/<int:server_id>/backups', methods=['GET'])
@auth_required()
def list_backups(server_id):
    backups = (MinecraftBackup.query
               .filter_by(server_id=server_id)
               .order_by(MinecraftBackup.created_at.desc()).all())
    return jsonify({'backups': [b.to_dict() for b in backups]})


@minecraft_bp.route('/<int:server_id>/backups', methods=['POST'])
@admin_required
def create_backup(server_id):
    """Manual world backup: the §3.2 hot-backup sequence (quiesce →
    copy-then-zip → resume) with retention + skip-when-empty."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    result = server_service.run_backup(server, kind='manual')
    return jsonify(result), 200 if result.get('success') else 500


@minecraft_bp.route('/<int:server_id>/backups/config', methods=['PUT'])
@admin_required
def update_backup_config(server_id):
    """Backup options: {retention, skip_when_empty}."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    body = request.get_json(silent=True) or {}
    result = server_service.update_backup_config(
        server, retention=body.get('retention'),
        skip_when_empty=body.get('skip_when_empty'))
    return jsonify(result), 200 if result.get('success') else 400


@minecraft_bp.route('/<int:server_id>/backups/<int:backup_id>/restore', methods=['POST'])
@admin_required
def restore_backup(server_id, backup_id):
    """Stop-first restore: graceful stop → swap world dirs from the archive →
    start again. The UI owns the confirm."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    backup = _backup_or_404(server_id, backup_id)
    if not backup:
        return jsonify({'error': 'Backup not found'}), 404
    result = server_service.restore_backup(server, backup)
    return jsonify(result), 200 if result.get('success') else 500


@minecraft_bp.route('/<int:server_id>/backups/<int:backup_id>', methods=['DELETE'])
@admin_required
def delete_backup(server_id, backup_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    backup = _backup_or_404(server_id, backup_id)
    if not backup:
        return jsonify({'error': 'Backup not found'}), 404
    return jsonify(server_service.delete_backup(server, backup))


# --------------------------------------------------------------------------- #
# Schedules (core ScheduledJob rails; restart broadcasts the countdown)
# --------------------------------------------------------------------------- #

def _schedule_or_404(server_id, schedule_id):
    from .models import MinecraftSchedule
    return MinecraftSchedule.query.filter_by(
        id=schedule_id, server_id=server_id).first()


@minecraft_bp.route('/<int:server_id>/schedules', methods=['GET'])
@auth_required()
def list_schedules(server_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    return jsonify({'schedules': server_service.list_schedules(server)})


@minecraft_bp.route('/<int:server_id>/schedules', methods=['POST'])
@admin_required
def create_schedule(server_id):
    """Body: {type: restart|announce|backup, cron, message?, label?, enabled?}."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    body = request.get_json(silent=True) or {}
    result = server_service.create_schedule(
        server, body.get('type'), body.get('cron') or '',
        message=body.get('message'), label=body.get('label'),
        enabled=body.get('enabled', True))
    return jsonify(result), 201 if result.get('success') else 400


@minecraft_bp.route('/<int:server_id>/schedules/<int:schedule_id>', methods=['PUT'])
@admin_required
def update_schedule(server_id, schedule_id):
    """Body: {cron?, enabled?, message?, label?}."""
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    schedule = _schedule_or_404(server_id, schedule_id)
    if not schedule:
        return jsonify({'error': 'Schedule not found'}), 404
    body = request.get_json(silent=True) or {}
    result = server_service.update_schedule(
        schedule, cron=body.get('cron'), enabled=body.get('enabled'),
        message=body.get('message'), label=body.get('label'))
    return jsonify(result), 200 if result.get('success') else 400


@minecraft_bp.route('/<int:server_id>/schedules/<int:schedule_id>', methods=['DELETE'])
@admin_required
def delete_schedule(server_id, schedule_id):
    server = _server_or_404(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    schedule = _schedule_or_404(server_id, schedule_id)
    if not schedule:
        return jsonify({'error': 'Schedule not found'}), 404
    return jsonify(server_service.delete_schedule(schedule))
