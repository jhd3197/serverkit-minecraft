"""Container-log event parser (gamekit adapter #2).

Turns raw Minecraft server log lines into normalized events that feed the
notifications bus (join/leave/started/stopped/crashed) and the Players cache.
Pure ``re`` — no ServerKit imports — so it unit-tests against fixture logs and
extracts cleanly with the rest of ``gamekit``.

itzg/minecraft-server log lines look like:

    [12:34:56] [Server thread/INFO]: Steve joined the game
    [12:34:56] [Server thread/INFO]: Steve left the game
    [12:34:56] [Server thread/INFO]: Done (12.345s)! For help, type "help"
    [12:34:56] [Server thread/INFO]: <Steve> hello world
    [12:34:56] [Server thread/INFO]: Stopping server

Bedrock (itzg/minecraft-bedrock-server) has no RCON; its lines differ, e.g.:

    [INFO] Player connected: Steve, xuid: 123
    [INFO] Player disconnected: Steve, xuid: 123
    [INFO] Server started.
"""
import re

# (event, compiled regex, group name for the player if any)
_RULES = [
    ('player_join',    re.compile(r'\]:\s+(?P<player>\w+) joined the game'), 'player'),
    ('player_leave',   re.compile(r'\]:\s+(?P<player>\w+) left the game'), 'player'),
    # Bedrock edition
    ('player_join',    re.compile(r'Player connected:\s+(?P<player>[^,]+)'), 'player'),
    ('player_leave',   re.compile(r'Player disconnected:\s+(?P<player>[^,]+)'), 'player'),
    ('server_started', re.compile(r'\]:\s+Done \((?P<elapsed>[\d.]+)s\)!'), None),
    ('server_started', re.compile(r'Server started\.'), None),
    ('server_stopping', re.compile(r'\]:\s+Stopping server'), None),
    ('chat',           re.compile(r'\]:\s+<(?P<player>\w+)>\s+(?P<message>.*)$'), 'player'),
    ('crash',          re.compile(r'This crash report has been saved', re.IGNORECASE), None),
    ('crash',          re.compile(r'\]:\s+Exception (?:stopping|in server tick)', re.IGNORECASE), None),
]


def parse_line(line):
    """Return a normalized event dict for *line*, or None if nothing matches.

    Event shape: ``{'event': <type>, 'player'?: str, 'message'?: str,
    'elapsed'?: str, 'raw': line}``. First matching rule wins, so player
    join/leave (more specific) are tried before the generic chat rule.
    """
    if not line:
        return None
    text = line.rstrip('\n')
    for event, rx, _player_group in _RULES:
        m = rx.search(text)
        if not m:
            continue
        out = {'event': event, 'raw': text}
        gd = m.groupdict()
        if gd.get('player'):
            out['player'] = gd['player'].strip()
        if gd.get('message') is not None:
            out['message'] = gd['message']
        if gd.get('elapsed'):
            out['elapsed'] = gd['elapsed']
        return out
    return None


def parse_lines(lines):
    """Parse an iterable of log lines into a list of events (Nones dropped)."""
    out = []
    for line in lines:
        ev = parse_line(line)
        if ev is not None:
            out.append(ev)
    return out
