"""Shared test support for the serverkit-minecraft suite.

Loads this repo's backend/ as the dashed package ``app.plugins.serverkit-
minecraft`` — the same way the panel loads an installed extension's backend
at boot — and provides the fakes (RCON, docker) every test module shares.

Test files symlink into the panel's backend/tests/; ``os.path.realpath`` on
the symlinked file resolves back to THIS repo, and this module is importable
because the test file puts its own resolved directory on sys.path. When the
files are copied instead of linked, point SERVERKIT_MINECRAFT_DIR at the repo.
"""
import importlib
import importlib.util
import os
import sys

SLUG = 'serverkit-minecraft'
# realpath so a symlinked copy inside <panel>/backend/tests still resolves
# back to this repository root. SERVERKIT_MINECRAFT_DIR overrides when copied
# (not linked).
EXT_DIR = os.environ.get(
    'SERVERKIT_MINECRAFT_DIR',
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
)

_PKG = f'app.plugins.{SLUG}'
_RCON_MODULE = None


def load_ext():
    """Register this repo's backend/ as ``app.plugins.<slug>`` and import its
    modules. Idempotent — every test module calls this at import time."""
    global _RCON_MODULE
    if _PKG not in sys.modules:
        backend_dir = os.path.join(EXT_DIR, 'backend')
        assert os.path.isdir(backend_dir), f'extension backend not found at {backend_dir}'
        spec = importlib.util.spec_from_file_location(
            _PKG,
            os.path.join(backend_dir, '__init__.py'),
            submodule_search_locations=[backend_dir],
        )
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = pkg
        spec.loader.exec_module(pkg)
    mods = {}
    for name in ('models', 'gamekit', 'server_service', 'minecraft'):
        mods[name] = importlib.import_module(f'{_PKG}.{name}')
    _RCON_MODULE = mods['gamekit'].rcon
    return mods


def ensure_plugin(app):
    """An installed-plugin row + the blueprint mounted, mirroring a real
    install closely enough for route tests."""
    from app import db
    from app.models.plugin import InstalledPlugin
    if not InstalledPlugin.query.filter_by(slug=SLUG).first():
        db.session.add(InstalledPlugin(
            name=SLUG, display_name='Minecraft Server', slug=SLUG, version='1.0.0',
            status=InstalledPlugin.STATUS_ACTIVE,
        ))
        db.session.commit()
    mods = load_ext()
    if 'minecraft' not in app.blueprints:
        app.register_blueprint(mods['minecraft'].minecraft_bp,
                               url_prefix='/api/v1/minecraft')


class FakeRcon:
    """Scripted RCON server: records commands, answers from a table."""

    def __init__(self, responses=None, fail=False):
        self.responses = responses or {}
        self.sent = []
        self.fail = fail
        self.closed = False

    def __enter__(self):
        if self.fail:
            raise _RCON_MODULE.RconError('connection refused')
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        self.closed = True

    def command(self, cmd):
        self.sent.append(cmd)
        return self.responses.get(cmd, '')


def fake_rcon(monkeypatch, svc, responses=None, fail=False):
    """Patch the service's RCON factory to return a scripted fake."""
    fake = FakeRcon(responses, fail=fail)
    monkeypatch.setattr(svc, '_rcon_client', lambda server: fake)
    return fake


def make_server(models, **overrides):
    """Persist a MinecraftServer row with sane defaults."""
    from app import db
    defaults = dict(
        name='testserver', edition='java', flavor='paper', version='1.21.4',
        world_name='world', memory='2G', port=25565, rcon_port=25575,
        rcon_password='pw', container_name='serverkit-mc-testserver',
        status='running', stop_requested=False, eula_accepted=True,
    )
    defaults.update(overrides)
    server = models.MinecraftServer(**defaults)
    db.session.add(server)
    db.session.commit()
    return server


def patch_docker(monkeypatch, svc, **methods):
    """Patch DockerService classmethods; each value is a plain callable."""
    for name, fn in methods.items():
        monkeypatch.setattr(svc.DockerService, name, staticmethod(fn))
