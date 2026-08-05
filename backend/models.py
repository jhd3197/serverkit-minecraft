"""Extension-owned data models (plan 53 D6).

Registered via the manifest ``models: "models:register"`` mechanism (the
k8s/tramo pattern): tables are named ``ext_serverkit_minecraft_*`` and created on
install, dropped on uninstall --purge — no core migration. ``register(db)`` is
called by ``extension_lifecycle.register_models`` after this module is imported.

Instances key off a core Application (``application_id``) like any managed
service (D9), so several game servers per box — and later game servers on remote
agents — work without a schema change.
"""
from datetime import datetime

from app import db


class MinecraftServer(db.Model):
    __tablename__ = 'ext_serverkit_minecraft_servers'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    # Soft reference to core applications.id (kept an int, not an FK, so the
    # extension's create_all never depends on core table ordering).
    application_id = db.Column(db.Integer)

    name = db.Column(db.String(200), nullable=False)
    edition = db.Column(db.String(20), default='java')      # java | bedrock
    flavor = db.Column(db.String(20), default='vanilla')    # vanilla|paper|fabric|forge
    version = db.Column(db.String(40), default='latest')
    world_name = db.Column(db.String(200), default='world')
    seed = db.Column(db.String(64))
    memory = db.Column(db.String(16), default='2G')
    port = db.Column(db.Integer, default=25565)
    rcon_port = db.Column(db.Integer, default=25575)
    rcon_password = db.Column(db.String(128))               # loopback-only (D5)
    eula_accepted = db.Column(db.Boolean, default=False)

    container_name = db.Column(db.String(200))
    deployment_job_id = db.Column(db.String(64))            # last install/Deploy Console job
    status = db.Column(db.String(20), default='creating')   # creating|running|stopped|crashed|error
    # Distinguish "stopped by user" from "crashed" for notifications (§3.4).
    stop_requested = db.Column(db.Boolean, default=False)

    # Backup preferences (Backups tab; Phase 3).
    backup_retention = db.Column(db.Integer, default=5)     # archives to keep
    backup_skip_empty = db.Column(db.Boolean, default=True) # skip empty worlds

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'application_id': self.application_id,
            'name': self.name,
            'edition': self.edition,
            'flavor': self.flavor,
            'version': self.version,
            'world_name': self.world_name,
            'seed': self.seed,
            'memory': self.memory,
            'port': self.port,
            'rcon_port': self.rcon_port,
            'status': self.status,
            'eula_accepted': self.eula_accepted,
            'container_name': self.container_name,
            'deployment_job_id': self.deployment_job_id,
            'backup_retention': self.backup_retention,
            'backup_skip_empty': self.backup_skip_empty,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class MinecraftBackup(db.Model):
    __tablename__ = 'ext_serverkit_minecraft_backups'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500))
    size_bytes = db.Column(db.BigInteger, default=0)
    kind = db.Column(db.String(20), default='manual')       # manual | scheduled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'server_id': self.server_id,
            'name': self.name,
            'file_path': self.file_path,
            'size_bytes': self.size_bytes,
            'kind': self.kind,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class MinecraftSchedule(db.Model):
    """Per-server schedule metadata (plan 53 §3.2 Schedules tab).

    The cadence itself lives on a core ``ScheduledJob`` row (D7 — core cron
    rails, never forked); this row carries the game-specific bits (type,
    announcement message) and the core row's name, so enable/disable and
    last-run tracking ride the unified job system for free.
    """
    __tablename__ = 'ext_serverkit_minecraft_schedules'
    __table_args__ = {'extend_existing': True}

    TYPES = ('restart', 'announce', 'backup')

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(20), nullable=False)         # restart|announce|backup
    label = db.Column(db.String(120))
    cron = db.Column(db.String(120), nullable=False)
    message = db.Column(db.String(500))                     # announce text
    enabled = db.Column(db.Boolean, default=True)
    job_name = db.Column(db.String(120))                    # core ScheduledJob.name
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'server_id': self.server_id,
            'type': self.type,
            'label': self.label,
            'cron': self.cron,
            'message': self.message,
            'enabled': self.enabled,
            'job_name': self.job_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


def register(db):  # noqa: ARG001 — signature required by extension_lifecycle
    """Model-registration entry point.

    The models above are defined at import time (so their tables register on the
    metadata); ``extension_lifecycle.register_models`` calls this then runs
    ``create_all``. Nothing more is needed here — kept as the documented seam.
    """
    return [MinecraftServer, MinecraftBackup, MinecraftSchedule]
