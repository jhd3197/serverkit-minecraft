"""gamekit — the reusable game-server framework (plan 53 D1).

Four adapters that give ServerKit (which manages containers from *outside*) the
equivalents of what an in-process game plugin gets for free:

    rcon         — minimal Source RCON client (console, players, lifecycle)
    log_events   — container-log → normalized events (notifications, players)
    config_form  — server.properties + sidecar metadata → grouped form
    save_backup  — save-aware hot-backup sequence with retention
    compose      — wizard spec → itzg image compose document (D2/D3/D5)
    players      — RCON list/whitelist/banlist parsers + graceful stop sequence

Deliberately dependency-free (stdlib only) and ServerKit-agnostic, so it unit
tests standalone and extracts to a shared package once a second game exists
(the two-real-consumers rule). Until then it lives inside serverkit-minecraft.
"""
from . import rcon, log_events, config_form, save_backup, compose, players  # noqa: F401

__all__ = ['rcon', 'log_events', 'config_form', 'save_backup', 'compose', 'players']
