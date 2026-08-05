"""Player-management parsing + lifecycle sequences (gamekit adapters).

Two small, pure pieces the Players tab and the lifecycle routes share:

- Parsers for the Java edition's RCON text output (``list``,
  ``whitelist list``, ``banlist``). The output is meant for humans and has
  changed shape across versions, so the parsers are deliberately lenient and
  unit-tested against representative fixtures.
- The graceful stop/restart command sequence (plan 53 §3.4): an in-game
  broadcast warning, then ``save-all flush``, THEN the container stop — the
  part naive panels get wrong. Pure: takes any object with ``.command(str)``.

Bedrock has no RCON in the default image path, so none of this applies there
(the routes answer 400; the console degrades to log-only — plan 53 Phase 4
documents the asymmetry honestly).
"""
import re

# Java edition account names: 3-16 chars, letters/digits/underscore.
PLAYER_NAME_RE = re.compile(r'^[A-Za-z0-9_]{3,16}$')

_LIST_RE = re.compile(
    r'There are (?P<online>\d+)(?: of a max of |/)(?P<max>\d+) players online\s*:?\s*(?P<names>.*)',
    re.IGNORECASE)
_WHITELIST_RE = re.compile(
    r'There (?:are|is) (?:\d+|no) whitelisted players?\s*:?\s*(?P<names>.*)',
    re.IGNORECASE)
_BAN_RE = re.compile(r'^(?P<player>\S+) was banned', re.IGNORECASE)


def valid_player_name(name):
    return bool(name and PLAYER_NAME_RE.match(name))


def _split_names(text):
    return [n.strip() for n in (text or '').split(',') if n.strip()]


def parse_list_output(text):
    """Parse RCON ``list`` output → {online, max, players}.

    Handles both eras: "There are 2 of a max of 20 players online: Steve, Alex"
    (modern) and "There are 2/20 players online: …" (pre-1.16-ish).
    """
    m = _LIST_RE.search(text or '')
    if not m:
        return {'online': 0, 'max': None, 'players': []}
    return {
        'online': int(m.group('online')),
        'max': int(m.group('max')),
        'players': _split_names(m.group('names')),
    }


def parse_whitelist_output(text):
    """Parse RCON ``whitelist list`` output → [names]."""
    m = _WHITELIST_RE.search(text or '')
    if not m:
        return []
    return _split_names(m.group('names'))


def parse_banlist_output(text):
    """Parse RCON ``banlist`` output → [names].

    Vanilla renders one line per ban ("Steve was banned by Server: …"); newer
    builds answer with a comma list after a colon. Handle both.
    """
    text = text or ''
    names = [m.group('player') for line in text.splitlines()
             if (m := _BAN_RE.search(line.strip()))]
    if names:
        return names
    if ':' in text:
        return _split_names(text.split(':', 1)[1])
    return []


# --------------------------------------------------------------------------- #
# Graceful lifecycle (§3.4): broadcast → save-all flush → (caller stops)
# --------------------------------------------------------------------------- #

_WARNINGS = {
    'stop': 'say Server is stopping in 10 seconds — your progress is being saved',
    'restart': 'say Server is restarting in 10 seconds — your progress is being saved',
}


def quiesce_commands(action='stop'):
    """The ordered RCON commands for a graceful *action* ('stop'|'restart').

    Broadcast first (players deserve the warning before the save hitch), then
    flush the world. The caller stops/restarts the container afterwards — the
    itzg image handles SIGTERM gracefully, but we save explicitly first.
    """
    if action not in _WARNINGS:
        raise ValueError(f"action must be one of: {', '.join(sorted(_WARNINGS))}")
    return [_WARNINGS[action], 'save-all flush']


def quiesce(rcon, action='stop'):
    """Run the graceful sequence against an RCON-like object; returns the
    issued commands. Best-effort semantics are the caller's choice — this just
    runs the sequence in order."""
    issued = []
    for cmd in quiesce_commands(action):
        rcon.command(cmd)
        issued.append(cmd)
    return issued


# --------------------------------------------------------------------------- #
# Scheduled-restart countdown (§3.2): "say Server restarting in 5…1 min"
# --------------------------------------------------------------------------- #

def _human_seconds(seconds):
    minutes = seconds // 60
    if minutes and seconds % 60 == 0:
        return f'{minutes} minute' + ('s' if minutes != 1 else '')
    return f'{seconds} seconds'


def countdown_broadcasts(total_seconds=60):
    """Ordered ``(offset_seconds, say-command)`` restart countdown plan.

    Marks land at 5 min / 1 min / 10 s before the restart when the window
    allows; a short window just gets its own marks (e.g. 60 → warnings at
    offset 0 and offset 50). Empty window → no broadcasts. Pure, so tests
    assert the plan without sleeping.
    """
    total = max(0, int(total_seconds))
    if total == 0:
        return []
    marks = [m for m in (300, 60, 10) if m <= total]
    if total not in marks:
        marks.insert(0, total)
    return [
        (total - m, f'say Server restarting in {_human_seconds(m)} — your progress is being saved')
        for m in marks
    ]
