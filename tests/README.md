# Tests

These tests exercise the extension's backend (gamekit framework, manifest
shape, create/delete/lifecycle flows, players, settings, backups, schedules,
event scan → notify wiring, blueprint routes) but need the **panel's Flask
app and pytest fixtures** (`app`, `client`, `auth_headers`), so they run from
inside a ServerKit checkout rather than standalone:

```bash
# Symlink (or copy) the test files into the panel's test suite:
ln -s "$(pwd)"/test_*.py /path/to/ServerKit/backend/tests/

# Then run them from the panel backend:
cd /path/to/ServerKit/backend
pytest tests/test_gamekit_minecraft.py tests/test_minecraft_extension_install.py \
       tests/test_minecraft_server_flow.py tests/test_minecraft_care.py
```

Via symlink each test resolves this repo's root with `os.path.realpath` (and
pulls in the shared `_mc_support.py` from this directory — no need to link
it). If you copy the files instead, point them at the repo:

```bash
SERVERKIT_MINECRAFT_DIR=/path/to/serverkit-minecraft pytest tests/test_*minecraft*
```

Nothing here needs a Docker daemon or a live Minecraft server: RCON is a
scripted fake socket, docker/firewall calls are monkeypatched, and the
compose/backup/config logic is pure-Python and tested directly.
