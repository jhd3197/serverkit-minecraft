"""server.properties as a form (gamekit adapter #3).

The file is the source of truth; a JSON sidecar (``server_properties_meta.json``)
adds grouping/labels/descriptions/restart-required so the panel renders a real
grouped form instead of a raw key=value textarea. Pure stdlib — parse, merge with
metadata into a form model, and write changes back preserving unknown keys,
comments, and order.
"""


def parse_properties(text):
    """Parse ``key=value`` lines into an ordered dict (comments/blanks ignored).

    Java properties: ``#`` or ``!`` starts a comment; keys and values are split
    on the first ``=``. Values keep their raw string form (the form layer coerces
    per field type).
    """
    out = {}
    for line in (text or '').splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ('#', '!'):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        out[key.strip()] = value.strip()
    return out


def _coerce(value, field_type):
    if field_type == 'boolean':
        return str(value).lower() in ('true', '1', 'yes', 'on')
    if field_type == 'integer':
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def build_form(text, meta):
    """Merge parsed properties with sidecar metadata into grouped form fields.

    ``meta`` is ``{'groups': [{id,label,order}], 'fields': {key: {label,
    description, group, type, restart_required, options?}}}``. Every property in
    the file appears; unknown keys land in an implicit ``other`` group so nothing
    is hidden. Returns ``{'groups': [{id, label, fields: [...]}]}`` ordered by
    each group's ``order``.
    """
    props = parse_properties(text)
    meta = meta or {}
    field_meta = meta.get('fields', {}) or {}
    group_defs = {g['id']: g for g in (meta.get('groups') or [])}

    grouped = {}
    for key, raw_value in props.items():
        fm = field_meta.get(key, {})
        gid = fm.get('group', 'other')
        field = {
            'key': key,
            'label': fm.get('label', key),
            'description': fm.get('description', ''),
            'type': fm.get('type', 'string'),
            'value': _coerce(raw_value, fm.get('type', 'string')),
            'restart_required': bool(fm.get('restart_required', False)),
        }
        if fm.get('options'):
            field['options'] = fm['options']
        grouped.setdefault(gid, []).append(field)

    def group_order(gid):
        return group_defs.get(gid, {}).get('order', 999)

    groups = []
    for gid in sorted(grouped, key=group_order):
        groups.append({
            'id': gid,
            'label': group_defs.get(gid, {}).get('label', gid.replace('_', ' ').title()),
            'fields': grouped[gid],
        })
    return {'groups': groups}


def apply_changes(text, changes):
    """Return new file text with *changes* applied, preserving everything else.

    Updates the value of any existing key in place (keeping comments, blank lines
    and ordering); appends keys that weren't present. Booleans render as
    ``true``/``false`` (Minecraft's expected form).
    """
    changes = {k: v for k, v in (changes or {}).items()}

    def render(v):
        if isinstance(v, bool):
            return 'true' if v else 'false'
        return str(v)

    lines = (text or '').splitlines()
    seen = set()
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped[0] not in ('#', '!') and '=' in line:
            key = line.partition('=')[0].strip()
            if key in changes:
                out_lines.append(f'{key}={render(changes[key])}')
                seen.add(key)
                continue
        out_lines.append(line)

    for key, value in changes.items():
        if key not in seen:
            out_lines.append(f'{key}={render(value)}')

    trailing_newline = (text or '').endswith('\n')
    result = '\n'.join(out_lines)
    if trailing_newline and not result.endswith('\n'):
        result += '\n'
    return result
