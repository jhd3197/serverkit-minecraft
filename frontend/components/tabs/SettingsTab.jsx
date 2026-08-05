// Settings tab (§3.2): server.properties rendered as a grouped form from the
// backend's form model (gamekit config_form + sidecar metadata — labels,
// descriptions, groups, restart-required badges). Saving writes the file;
// when a restart-required key changed, a banner offers the graceful restart.
import { useEffect, useMemo, useState } from 'react';
import { RotateCcw, Save } from 'lucide-react';

import { Pill, useToast } from 'serverkit-sdk';
import {
    Button, Input, Checkbox, Spinner,
    Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../primitives.jsx';

import minecraftApi from '../../api.js';

function FieldControl({ field, value, onChange }) {
    if (field.type === 'boolean') {
        return (
            <Checkbox checked={value === true}
                      onCheckedChange={(c) => onChange(field.key, c === true)} />
        );
    }
    if (field.options) {
        return (
            <Select value={String(value)} onValueChange={(v) => onChange(field.key, v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                    {field.options.map((opt) => (
                        <SelectItem key={opt} value={String(opt)}>{opt}</SelectItem>
                    ))}
                </SelectContent>
            </Select>
        );
    }
    return (
        <Input type={field.type === 'integer' ? 'number' : 'text'}
               value={value ?? ''}
               onChange={(e) => onChange(
                   field.key,
                   field.type === 'integer' ? e.target.value.replace(/[^\d-]/g, '') : e.target.value)} />
    );
}

export default function SettingsTab({ server }) {
    const toast = useToast();
    const [form, setForm] = useState(null);
    const [error, setError] = useState(null);
    const [edits, setEdits] = useState({});
    const [saving, setSaving] = useState(false);
    const [restartNeeded, setRestartNeeded] = useState(null); // string[] | null
    const [restarting, setRestarting] = useState(false);

    useEffect(() => {
        minecraftApi.settings(server.id)
            .then((data) => setForm(data.form))
            .catch((err) => setError(err.message || 'Failed to load settings'));
    }, [server.id]);

    const dirtyKeys = useMemo(() => Object.keys(edits), [edits]);

    function handleChange(key, value) {
        setEdits((prev) => ({ ...prev, [key]: value }));
    }

    function fieldValue(field) {
        return field.key in edits ? edits[field.key] : field.value;
    }

    async function handleSave() {
        setSaving(true);
        try {
            const result = await minecraftApi.updateSettings(server.id, edits);
            setEdits({});
            toast.success('Settings saved');
            if (result.restart_required) {
                setRestartNeeded(result.restart_keys || []);
            } else {
                setRestartNeeded(null);
            }
            // Re-read so the form shows the file's actual state.
            const fresh = await minecraftApi.settings(server.id);
            setForm(fresh.form);
        } catch (err) {
            toast.error(err.message || 'Failed to save settings');
        } finally {
            setSaving(false);
        }
    }

    async function handleRestart() {
        setRestarting(true);
        try {
            await minecraftApi.restart(server.id);
            toast.success('Server restarted — new settings are live');
            setRestartNeeded(null);
        } catch (err) {
            toast.error(err.message || 'Failed to restart server');
        } finally {
            setRestarting(false);
        }
    }

    if (error) {
        return (
            <div className="mc-settings">
                <p className="mc-settings__notice">{error}</p>
            </div>
        );
    }
    if (!form) {
        return <div className="mc-page--loading"><Spinner size="lg" /></div>;
    }

    return (
        <div className="mc-settings">
            {restartNeeded && (
                <div className="mc-settings__restart-banner">
                    <span>
                        These changes need a restart to take effect
                        {restartNeeded.length > 0 && `: ${restartNeeded.join(', ')}`}
                    </span>
                    <Button size="sm" onClick={handleRestart} disabled={restarting}>
                        <RotateCcw size={14} /> {restarting ? 'Restarting…' : 'Restart now'}
                    </Button>
                </div>
            )}

            {form.groups.map((group) => (
                <section key={group.id} className="mc-settings__group">
                    <h3 className="mc-settings__group-title">{group.label}</h3>
                    <div className="mc-settings__fields">
                        {group.fields.map((field) => (
                            <div key={field.key} className="mc-settings__field">
                                <div className="mc-settings__field-labels">
                                    <span className="mc-settings__field-label">
                                        {field.label}
                                        {field.restart_required && (
                                            <Pill kind="amber" dot={false}>restart</Pill>
                                        )}
                                    </span>
                                    {field.description && (
                                        <span className="mc-settings__field-desc">
                                            {field.description}
                                        </span>
                                    )}
                                </div>
                                <div className="mc-settings__field-control">
                                    <FieldControl field={field}
                                                  value={fieldValue(field)}
                                                  onChange={handleChange} />
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            ))}

            <div className="mc-settings__actions">
                <Button onClick={handleSave} disabled={saving || dirtyKeys.length === 0}>
                    <Save size={14} /> {saving ? 'Saving…' : `Save changes${dirtyKeys.length ? ` (${dirtyKeys.length})` : ''}`}
                </Button>
            </div>
        </div>
    );
}
