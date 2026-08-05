"""Save-aware world backup (gamekit adapter #4).

The naive ``docker cp`` while the server writes = a torn, unusable world. The
correct Minecraft hot-backup sequence quiesces the world first:

    save-off            → stop the server writing the world
    save-all flush      → flush pending chunks to disk
    (zip the world)
    save-on             → resume normal saving  (ALWAYS, even on failure)

RCON is injected (any object with ``.command(str)``) so this is unit-testable
without a live server, and reusable by the plan-52 backup-target provider when
that hook lands (plan 53 D7). Pure stdlib otherwise.
"""
import os
import zipfile


def world_is_empty(world_dir):
    if not os.path.isdir(world_dir):
        return True
    for _root, _dirs, files in os.walk(world_dir):
        if files:
            return False
    return True


def _zip_dir(src_dir, archive_path):
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                full = os.path.join(root, name)
                arc = os.path.relpath(full, src_dir)
                zf.write(full, arc)


def zip_dir(src_dir, archive_path):
    """Public wrapper around the zip step (container flows stage a copy first,
    then zip it after the quiesce window has closed)."""
    _zip_dir(src_dir, archive_path)


# The quiesce window: stop the server writing the world, flush pending chunks,
# do the copy/zip work, then ALWAYS resume saving — even on failure.
QUIESCE_PRE = ('save-off', 'save-all flush')
QUIESCE_POST = ('save-on',)


def run_quiesced(rcon, work):
    """Run *work()* inside the quiesce window; returns the RCON commands issued.

    ``rcon`` may be None (stopped server / Bedrock — a cold copy is already
    consistent). The post-commands run even when *work()* raises, so the
    server is never left with saving disabled.
    """
    issued = []
    if rcon is not None:
        for cmd in QUIESCE_PRE:
            rcon.command(cmd)
            issued.append(cmd)
    try:
        work()
    finally:
        if rcon is not None:
            for cmd in QUIESCE_POST:
                rcon.command(cmd)
                issued.append(cmd)
    return issued


def apply_retention(dest_dir, keep):
    """Keep the newest *keep* ``.zip`` archives in *dest_dir*, delete the rest.

    Returns the list of pruned filenames. Ordered by (mtime, name) so the
    self-describing date-stamped names break mtime ties deterministically.
    """
    if not keep or keep < 1:
        return []
    archives = [f for f in os.listdir(dest_dir) if f.endswith('.zip')]
    archives.sort(key=lambda f: (os.path.getmtime(os.path.join(dest_dir, f)), f))
    to_prune = archives[:-keep] if len(archives) > keep else []
    for f in to_prune:
        try:
            os.remove(os.path.join(dest_dir, f))
        except OSError:
            pass
    return to_prune


def hot_backup(world_dir, dest_dir, archive_name, *, rcon=None,
               skip_when_empty=True, retention=None):
    """Quiesce → zip → resume. The correct Minecraft hot-backup sequence.

    ``rcon`` (optional): an object with ``.command(str)``. When present, issues
    ``save-off`` + ``save-all flush`` before zipping and ``save-on`` after —
    always, even if zipping fails. ``skip_when_empty`` returns a skipped
    descriptor for an empty/missing world (nothing worth an archive).
    ``retention`` prunes older archives afterwards. Returns a descriptor dict.
    """
    if skip_when_empty and world_is_empty(world_dir):
        return {'success': True, 'skipped': True, 'reason': 'world empty', 'commands': []}

    os.makedirs(dest_dir, exist_ok=True)
    if not archive_name.endswith('.zip'):
        archive_name += '.zip'
    archive_path = os.path.join(dest_dir, archive_name)

    commands = run_quiesced(rcon, lambda: _zip_dir(world_dir, archive_path))

    result = {
        'success': True, 'skipped': False, 'path': archive_path,
        'size_bytes': os.path.getsize(archive_path), 'commands': commands,
    }
    if retention:
        result['pruned'] = apply_retention(dest_dir, retention)
    return result


def archive_name(world_name, version=None, when=None):
    """Self-describing archive name, e.g. ``world_v1.21_2026-07-22_1200.zip``.

    ``when`` is an optional ``datetime`` (caller-supplied so this stays pure and
    deterministic in tests); omitted → just the world + version stem.
    """
    parts = [world_name or 'world']
    if version:
        parts.append(f'v{version}')
    if when is not None:
        parts.append(when.strftime('%Y-%m-%d_%H%M'))
    return '_'.join(parts) + '.zip'
