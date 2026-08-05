"""Prove the serverkit-minecraft extension wires up (plan 53).

The extension lives in its own repository and ships as a runtime-ESM bundle;
these tests run inside a ServerKit checkout (symlinked — see tests/README.md)
and verify the manifest passes the panel's validator, its job/handler/entry
references resolve against backend/, its ext_serverkit_minecraft_* tables
register on the metadata, and its blueprint responds when mounted.
"""
import importlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

from app import db
from app.services import plugin_service

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import _mc_support  # noqa: E402

SLUG = _mc_support.SLUG
EXT_DIR = _mc_support.EXT_DIR

_M = _mc_support.load_ext()
models_mod = _M['models']


@pytest.fixture
def mc(app, client):
    _mc_support.ensure_plugin(app)
    return SimpleNamespace(svc=_M['server_service'], models=models_mod,
                           gamekit=_M['gamekit'], client=client)


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #

def _manifest():
    with open(os.path.join(EXT_DIR, 'plugin.json'), encoding='utf-8') as f:
        return json.load(f)


def test_manifest_passes_validator():
    m = _manifest()
    assert plugin_service._validate_manifest(m) is True
    assert m['name'] == SLUG
    assert m['entry_point'] == 'minecraft:minecraft_bp'
    assert m['url_prefix'] == '/api/v1/minecraft'
    assert m['models'] == 'models:register'
    assert m['category'] == 'games'
    nav = m['contributions']['nav'][0]
    assert nav['route'] == '/minecraft' and nav['id'] == 'minecraft'


def test_manifest_permissions_known():
    from app.plugins_sdk import permissions as sdk_perms
    m = _manifest()
    assert sdk_perms.unknown_permissions(m['permissions']) == []
    assert set(m['permissions']) == {'docker', 'filesystem', 'network'}


def test_manifest_jobs_and_schedules_pair_up():
    m = _manifest()
    job_kinds = {j['kind'] for j in m['jobs']}
    sched_kinds = {s['kind'] for s in m['schedules']}
    assert sched_kinds <= job_kinds
    assert {'minecraft.schedule', 'minecraft.event_scan'} == job_kinds


def test_job_handler_refs_resolve():
    for job in _manifest()['jobs']:
        module_name, func_name = job['handler'].split(':')
        mod = importlib.import_module(f'app.plugins.{SLUG}.{module_name}')
        assert callable(getattr(mod, func_name, None)), job['handler']


def test_entry_point_resolves_to_blueprint():
    bp = getattr(_M['minecraft'], 'minecraft_bp', None)
    assert bp is not None and bp.name == 'minecraft'


def test_manifest_declares_bridge_and_sdk():
    m = _manifest()
    # sdk_version drives the runtime SDK gate; it must satisfy the panel's SDK.
    from app.utils.sdk import SDK_VERSION, sdk_version_satisfies
    assert m['sdk_version']
    assert sdk_version_satisfies(m['sdk_version'], SDK_VERSION)
    assert m['min_panel_version']
    # Runtime-ESM bundle, loaded by the no-rebuild frontend loader.
    assert m['frontend_entry'] == 'dist/index.mjs'
    routes = m['contributions']['routes']
    assert {'path': 'minecraft/*', 'component': 'MinecraftExtension'} in routes


def test_frontend_entry_exports_route_component():
    with open(os.path.join(EXT_DIR, 'frontend', 'runtime-entry.jsx'),
              encoding='utf-8') as f:
        src = f.read()
    assert 'MinecraftExtension' in src
    # No default export — PluginLoader auto-renders plugin default exports.
    assert 'export default' not in src


# --------------------------------------------------------------------------- #
# models + blueprint
# --------------------------------------------------------------------------- #

def test_models_registered_on_metadata(app):
    tables = set(db.inspect(db.engine).get_table_names())
    assert 'ext_serverkit_minecraft_servers' in tables
    assert 'ext_serverkit_minecraft_backups' in tables
    assert 'ext_serverkit_minecraft_schedules' in tables


def test_list_route_responds(mc, auth_headers):
    resp = mc.client.get('/api/v1/minecraft', headers=auth_headers)
    assert resp.status_code == 200, resp.status_code
    assert resp.get_json() == {'servers': []}
