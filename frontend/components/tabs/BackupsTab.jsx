// Backups tab (§3.2): manual "Back up now" (the hot-backup sequence happens
// server-side), retention + skip-when-empty options, restore with a
// stop-first confirm, and delete. Scheduled backups are created from the
// Schedules tab and show up here with kind 'scheduled'.
import { useCallback, useEffect, useState } from 'react';
import { Archive, RotateCcw, Trash2 } from 'lucide-react';

import { DataTable, Pill, useToast } from 'serverkit-sdk';
import {
    Button, Input, Checkbox, Card, CardContent, CardHeader, CardTitle,
} from '../primitives.jsx';
import { formatBytes, timeAgo } from '../../utils/format.js';

import minecraftApi from '../../api.js';

export default function BackupsTab({ server }) {
    const toast = useToast();
    const [backups, setBackups] = useState(null);
    const [retention, setRetention] = useState(server.backup_retention ?? 5);
    const [skipEmpty, setSkipEmpty] = useState(server.backup_skip_empty !== false);
    const [busy, setBusy] = useState('');
    const [restoreTarget, setRestoreTarget] = useState(null);

    const refresh = useCallback(() => {
        minecraftApi.backups(server.id)
            .then((data) => setBackups(data.backups || []))
            .catch((err) => toast.error(err.message || 'Failed to load backups'));
    }, [server.id, toast]);

    useEffect(() => { refresh(); }, [refresh]);

    async function handleBackupNow() {
        setBusy('backup');
        try {
            const result = await minecraftApi.createBackup(server.id);
            if (result.skipped) {
                toast.info('Backup skipped — the world is still empty');
            } else {
                toast.success(`Backup created: ${result.backup.name}`);
            }
        } catch (err) {
            toast.error(err.message || 'Backup failed');
        } finally {
            setBusy('');
            refresh();
        }
    }

    async function handleSaveConfig() {
        setBusy('config');
        try {
            await minecraftApi.updateBackupConfig(server.id, {
                retention: parseInt(retention, 10),
                skip_when_empty: skipEmpty,
            });
            toast.success('Backup options saved');
        } catch (err) {
            toast.error(err.message || 'Failed to save options');
        } finally {
            setBusy('');
        }
    }

    async function handleRestore() {
        setBusy('restore');
        try {
            const result = await minecraftApi.restoreBackup(server.id, restoreTarget.id);
            toast.success(`World restored from ${restoreTarget.name}`
                          + (result.restarted ? ' — server is starting' : ''));
            setRestoreTarget(null);
        } catch (err) {
            toast.error(err.message || 'Restore failed');
        } finally {
            setBusy('');
        }
    }

    async function handleDelete(backup) {
        setBusy(`delete-${backup.id}`);
        try {
            await minecraftApi.deleteBackup(server.id, backup.id);
            toast.success(`Deleted ${backup.name}`);
        } catch (err) {
            toast.error(err.message || 'Delete failed');
        } finally {
            setBusy('');
            refresh();
        }
    }

    const columns = [
        {
            key: 'name',
            header: 'Backup',
            sortable: true,
            render: (b) => <code className="mc-address">{b.name}</code>,
        },
        {
            key: 'size_bytes',
            header: 'Size',
            sortable: true,
            render: (b) => formatBytes(b.size_bytes),
        },
        {
            key: 'kind',
            header: 'Kind',
            render: (b) => (
                <Pill kind={b.kind === 'scheduled' ? 'cyan' : 'gray'} dot={false}>{b.kind}</Pill>
            ),
        },
        {
            key: 'created_at',
            header: 'Created',
            sortable: true,
            render: (b) => timeAgo(b.created_at),
        },
        {
            key: 'actions',
            header: '',
            render: (b) => (
                <div className="mc-players__actions">
                    <Button variant="ghost" size="sm" disabled={!!busy}
                            onClick={() => setRestoreTarget(b)}>
                        <RotateCcw size={14} /> Restore
                    </Button>
                    <Button variant="danger" size="sm" disabled={!!busy}
                            onClick={() => handleDelete(b)}>
                        <Trash2 size={14} /> Delete
                    </Button>
                </div>
            ),
        },
    ];

    return (
        <div className="mc-backups">
            <Card>
                <CardHeader><CardTitle>World backups</CardTitle></CardHeader>
                <CardContent>
                    <div className="mc-backups__controls">
                        <Button onClick={handleBackupNow} disabled={!!busy}>
                            <Archive size={14} />
                            {busy === 'backup' ? 'Backing up…' : 'Back up now'}
                        </Button>
                        <div className="mc-backups__option">
                            <label htmlFor="mc-retention">Keep</label>
                            <Input id="mc-retention" type="number" min="0" max="100"
                                   value={retention}
                                   onChange={(e) => setRetention(e.target.value)} />
                            <span>newest backups</span>
                        </div>
                        <div className="mc-backups__option">
                            <Checkbox id="mc-skip-empty" checked={skipEmpty}
                                      onCheckedChange={(c) => setSkipEmpty(c === true)} />
                            <label htmlFor="mc-skip-empty">Skip when the world is empty</label>
                        </div>
                        <Button variant="outline" size="sm" onClick={handleSaveConfig}
                                disabled={!!busy}>
                            Save options
                        </Button>
                    </div>
                </CardContent>
            </Card>

            <DataTable
                columns={columns}
                data={backups || []}
                keyField="id"
                loading={backups === null}
                emptyTitle="No backups yet"
                emptyMessage="Back up now, or add a backup schedule from the Schedules tab."
            />

            {restoreTarget && (
                <div className="modal-overlay" onClick={() => setRestoreTarget(null)}>
                    <div className="modal" onClick={(e) => e.stopPropagation()}>
                        <div className="modal-header">
                            <h3>Restore {restoreTarget.name}?</h3>
                        </div>
                        <div className="modal-body">
                            <p>
                                The server will stop (warning your players first), the current
                                world will be <strong>replaced</strong> by this backup, and the
                                server will start again. Anything built since this backup is lost.
                            </p>
                        </div>
                        <div className="modal-actions">
                            <Button variant="ghost" onClick={() => setRestoreTarget(null)}>
                                Cancel
                            </Button>
                            <Button variant="danger" disabled={busy === 'restore'}
                                    onClick={handleRestore}>
                                {busy === 'restore' ? 'Restoring…' : 'Stop & restore'}
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
