"""Compose generation for Minecraft servers (gamekit adapter #5).

Turns the create-wizard spec (plan 53 §3.1) into a docker-compose document for
the standard community images (D2 — we configure through their documented env
surface, never fork images):

    itzg/minecraft-server          Java: vanilla/Paper/Fabric/Forge via TYPE
    itzg/minecraft-bedrock-server  Bedrock (no RCON — documented asymmetry)

Locked-decision mapping:
    D2  world lives on a named volume so upgrades never touch saves
    D3  EULA is the user's click — validate_spec() refuses a spec whose
        eula_accepted isn't True; nothing here pre-accepts
    D5  the raw game port publishes directly (25565/TCP Java, 19132/UDP
        Bedrock); RCON (25575) publishes to 127.0.0.1 only — the panel talks
        to it server-side

Pure stdlib and Docker-free: build_compose() returns an ordered dict (easy to
assert in tests); the extension backend renders it to YAML. Port/memory/spec
validation lives here too so the wizard, the API, and the tests share one
source of truth.
"""
import re

JAVA_IMAGE = 'itzg/minecraft-server'
BEDROCK_IMAGE = 'itzg/minecraft-bedrock-server'

EDITIONS = ('java', 'bedrock')
FLAVORS = ('vanilla', 'paper', 'fabric', 'forge')   # Java only (TYPE env)

DEFAULT_MEMORY = '2G'
LOW_MEMORY_BYTES = 1024 ** 3                       # warn below 1G (§3.1)

DEFAULT_GAME_PORT = {'java': 25565, 'bedrock': 19132}
GAME_PORT_PROTOCOL = {'java': 'tcp', 'bedrock': 'udp'}
DEFAULT_RCON_PORT = 25575

_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,47}$')
_MEMORY_RE = re.compile(r'^(\d+)\s*([bkmg])?$', re.IGNORECASE)

MIN_PORT = 1024
MAX_PORT = 65535


def normalize_memory(value):
    """Normalize a memory limit to the image's form ('512M', '2G').

    Accepts '2g', '2G', '2048m', or a bare number (gigabytes, the wizard's
    mental model). Raises ValueError on anything else.
    """
    text = str(value or '').strip()
    m = _MEMORY_RE.match(text)
    if not m:
        raise ValueError(f"Memory must look like '512M' or '2G', got {value!r}")
    amount, unit = m.group(1), (m.group(2) or 'G').upper()
    if int(amount) <= 0:
        raise ValueError('Memory must be positive')
    return f'{amount}{unit}'


def memory_to_bytes(value):
    """Byte size of a normalized memory string (for the below-1G warning)."""
    normalized = normalize_memory(value)
    amount, unit = int(normalized[:-1]), normalized[-1]
    factor = {'B': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}[unit]
    return amount * factor


def is_low_memory(value):
    """True when the limit is below the 1G comfort floor (§3.1 warning)."""
    try:
        return memory_to_bytes(value) < LOW_MEMORY_BYTES
    except ValueError:
        return False


def default_port(edition):
    return DEFAULT_GAME_PORT.get(edition)


def port_protocol(edition):
    return GAME_PORT_PROTOCOL.get(edition, 'tcp')


def container_name(name):
    return f'serverkit-mc-{name}'


def volume_name(name):
    return f'{container_name(name)}-data'


def next_free_port(preferred, taken):
    """First port at/above *preferred* not in *taken* (a set of ints).

    Used to auto-pick RCON loopback ports (25575, 25576, …) and to suggest an
    alternative when the wizard's game port is occupied. Pure — the caller
    gathers `taken` from the DB / docker / a bind probe.
    """
    port = int(preferred)
    while port in taken and port < MAX_PORT:
        port += 1
    return port


def validate_spec(spec):
    """Validate a create-wizard spec. Returns a list of human-readable errors
    (empty = valid). This is the D3 enforcement point: no EULA, no server.
    """
    errors = []
    spec = spec or {}

    name = str(spec.get('name') or '').strip()
    if not name:
        errors.append('Server name is required')
    elif not _NAME_RE.match(name):
        errors.append('Server name must be lowercase letters, digits, dashes or '
                      'underscores (3-48 chars, starting with a letter or digit)')

    edition = spec.get('edition') or 'java'
    if edition not in EDITIONS:
        errors.append(f"Edition must be one of: {', '.join(EDITIONS)}")

    flavor = (spec.get('flavor') or 'vanilla').lower()
    if edition == 'bedrock':
        if flavor != 'vanilla':
            errors.append('Flavors (Paper/Fabric/Forge) are Java-edition only')
    elif flavor not in FLAVORS:
        errors.append(f"Flavor must be one of: {', '.join(FLAVORS)}")

    try:
        normalize_memory(spec.get('memory') or DEFAULT_MEMORY)
    except ValueError as e:
        errors.append(str(e))

    for key, label in (('port', 'Game port'),):
        try:
            port = int(spec.get(key) or default_port(edition) or 0)
            if not MIN_PORT <= port <= MAX_PORT:
                errors.append(f'{label} must be between {MIN_PORT} and {MAX_PORT}')
        except (TypeError, ValueError):
            errors.append(f'{label} must be a number')

    if edition == 'java' and spec.get('rcon_port') is not None:
        try:
            rcon_port = int(spec.get('rcon_port'))
            if not MIN_PORT <= rcon_port <= MAX_PORT:
                errors.append(f'RCON port must be between {MIN_PORT} and {MAX_PORT}')
        except (TypeError, ValueError):
            errors.append('RCON port must be a number')

    # D3 — the EULA is the user's click, never pre-accepted.
    if spec.get('eula_accepted') is not True:
        errors.append('The Minecraft EULA must be accepted to run a server')

    return errors


def _java_environment(spec):
    env = {
        'EULA': 'TRUE',                          # validated user click (D3)
        'TYPE': (spec.get('flavor') or 'vanilla').upper(),
        'VERSION': _image_version(spec.get('version')),
        'MEMORY': normalize_memory(spec.get('memory') or DEFAULT_MEMORY),
        'LEVEL': spec.get('world_name') or 'world',
        'ENABLE_RCON': 'true',
        'RCON_PASSWORD': spec.get('rcon_password') or '',
    }
    if spec.get('seed'):
        env['SEED'] = str(spec['seed'])
    return env


def _bedrock_environment(spec):
    env = {
        'EULA': 'TRUE',
        'VERSION': _image_version(spec.get('version')),
        'LEVEL_NAME': spec.get('world_name') or 'world',
    }
    if spec.get('seed'):
        env['LEVEL_SEED'] = str(spec['seed'])
    return env


def _image_version(version):
    """The images spell "latest" as LATEST; pass pinned versions through."""
    text = str(version or '').strip()
    return 'LATEST' if not text or text.lower() == 'latest' else text


def build_compose(spec):
    """Build the compose document (dict) for a validated spec.

    Shape (both editions): one `minecraft` service, the world on a named
    volume (D2), `restart: unless-stopped` for crash recovery (§3.4), a
    generous stop grace period so the image's graceful-SIGTERM handler can
    finish saving. Java publishes the game port as TCP plus RCON on
    loopback only (D5); Bedrock publishes the game port as UDP and caps the
    container memory (the Bedrock image has no heap env like Java's MEMORY).
    """
    edition = spec.get('edition') or 'java'
    name = spec['name']
    volume = volume_name(name)
    port = int(spec.get('port') or default_port(edition))

    if edition == 'bedrock':
        service = {
            'image': BEDROCK_IMAGE,
            'container_name': container_name(name),
            'restart': 'unless-stopped',
            'stop_grace_period': '60s',
            'tty': True,
            'stdin_open': True,
            'environment': _bedrock_environment(spec),
            'mem_limit': normalize_memory(spec.get('memory') or DEFAULT_MEMORY).lower(),
            'ports': [f'{port}:19132/udp'],
            'volumes': [f'{volume}:/data'],
        }
    else:
        rcon_port = int(spec.get('rcon_port') or DEFAULT_RCON_PORT)
        service = {
            'image': JAVA_IMAGE,
            'container_name': container_name(name),
            'restart': 'unless-stopped',
            'stop_grace_period': '60s',
            'tty': True,
            'stdin_open': True,
            'environment': _java_environment(spec),
            'ports': [
                f'{port}:25565/tcp',
                # RCON is loopback-only (D5): reachable from the panel on the
                # host, never from the network.
                f'127.0.0.1:{rcon_port}:25575',
            ],
            'volumes': [f'{volume}:/data'],
        }

    return {
        'services': {'minecraft': service},
        'volumes': {volume: {}},
    }
