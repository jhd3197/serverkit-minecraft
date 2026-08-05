// Schedules tab (§3.2): restart / announcement / backup schedules on core
// cron rails. Restarts broadcast the in-game countdown before the graceful
// restart (server-side); announcements need RCON so they're Java-only.
// Next/last run come from the core ScheduledJob rows.
import { useCallback, useEffect, useState } from 'react';
import { CalendarClock, Plus, Trash2 } from 'lucide-react';

import { DataTable, Pill, SchedulePicker, useToast } from 'serverkit-sdk';
import {
    Button, Input, Card, CardContent, CardHeader, CardTitle,
    Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../primitives.jsx';
import { timeAgo } from '../../utils/format.js';

import minecraftApi from '../../api.js';

const TYPE_KIND = { restart: 'amber', announce: 'cyan', backup: 'violet' };
const DEFAULT_CRONS = { restart: '0 4 * * *', announce: '0 */2 * * *', backup: '0 */6 * * *' };

function futureTime(iso) {
    if (!iso) return '—';
    const seconds = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
    if (seconds < 0) return 'due';
    if (seconds < 3600) return `in ${Math.max(1, Math.round(seconds / 60))}m`;
    if (seconds < 86400) return `in ${Math.round(seconds / 3600)}h`;
    return `in ${Math.round(seconds / 86400)}d`;
}

export default function SchedulesTab({ server }) {
    const toast = useToast();
    const [schedules, setSchedules] = useState(null);
    const [type, setType] = useState('restart');
    const [cron, setCron] = useState(DEFAULT_CRONS.restart);
    const [message, setMessage] = useState('');
    const [busy, setBusy] = useState(false);

    const isJava = server.edition !== 'bedrock';

    const refresh = useCallback(() => {
        minecraftApi.schedules(server.id)
            .then((data) => setSchedules(data.schedules || []))
            .catch((err) => toast.error(err.message || 'Failed to load schedules'));
    }, [server.id, toast]);

    useEffect(() => { refresh(); }, [refresh]);

    function handleTypeChange(next) {
        setType(next);
        setCron(DEFAULT_CRONS[next]);
    }

    async function handleCreate(e) {
        e.preventDefault();
        setBusy(true);
        try {
            await minecraftApi.createSchedule(server.id, {
                type, cron, message: type === 'announce' ? message : undefined,
            });
            toast.success('Schedule created');
            setMessage('');
            refresh();
        } catch (err) {
            toast.error(err.message || 'Failed to create schedule');
        } finally {
            setBusy(false);
        }
    }

    async function handleToggle(schedule) {
        try {
            await minecraftApi.updateSchedule(server.id, schedule.id,
                { enabled: !schedule.enabled });
            refresh();
        } catch (err) {
            toast.error(err.message || 'Failed to update schedule');
        }
    }

    async function handleDelete(schedule) {
        try {
            await minecraftApi.deleteSchedule(server.id, schedule.id);
            toast.success('Schedule deleted');
            refresh();
        } catch (err) {
            toast.error(err.message || 'Failed to delete schedule');
        }
    }

    const columns = [
        {
            key: 'type',
            header: 'Type',
            render: (s) => <Pill kind={TYPE_KIND[s.type] || 'gray'} dot={false}>{s.type}</Pill>,
        },
        {
            key: 'cron',
            header: 'Schedule',
            render: (s) => <code className="mc-address">{s.cron}</code>,
        },
        {
            key: 'message',
            header: 'Message',
            render: (s) => s.message || '—',
        },
        {
            key: 'next_run_at',
            header: 'Next run',
            render: (s) => (s.enabled ? futureTime(s.next_run_at) : '—'),
        },
        {
            key: 'last_run_at',
            header: 'Last run',
            render: (s) => (s.last_run_at ? timeAgo(s.last_run_at) : 'never'),
        },
        {
            key: 'actions',
            header: '',
            render: (s) => (
                <div className="mc-players__actions">
                    <Button variant="ghost" size="sm" onClick={() => handleToggle(s)}>
                        {s.enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => handleDelete(s)}>
                        <Trash2 size={14} /> Delete
                    </Button>
                </div>
            ),
        },
    ];

    return (
        <div className="mc-schedules">
            <Card>
                <CardHeader>
                    <CardTitle><CalendarClock size={16} /> New schedule</CardTitle>
                </CardHeader>
                <CardContent>
                    <form className="mc-schedules__form" onSubmit={handleCreate}>
                        <div className="mc-schedules__form-row">
                            <Select value={type} onValueChange={handleTypeChange}>
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="restart">Restart (with in-game countdown)</SelectItem>
                                    <SelectItem value="announce" disabled={!isJava}>
                                        Announcement (say …)
                                    </SelectItem>
                                    <SelectItem value="backup">World backup</SelectItem>
                                </SelectContent>
                            </Select>
                            {type === 'announce' && (
                                <Input value={message} placeholder="Message to broadcast"
                                       onChange={(e) => setMessage(e.target.value)} />
                            )}
                            <Button type="submit" disabled={busy || (type === 'announce' && !message.trim())}>
                                <Plus size={14} /> Add schedule
                            </Button>
                        </div>
                        <SchedulePicker value={cron} onChange={setCron} compact />
                        {type === 'restart' && (
                            <p className="mc-schedules__hint">
                                Restarts broadcast “Server restarting in …” in-game a minute
                                ahead, save the world, then restart — never a bare kill.
                            </p>
                        )}
                        {!isJava && (
                            <p className="mc-schedules__hint">
                                Bedrock has no RCON, so announcements are unavailable and
                                restarts skip the in-game countdown.
                            </p>
                        )}
                    </form>
                </CardContent>
            </Card>

            <DataTable
                columns={columns}
                data={schedules || []}
                keyField="id"
                loading={schedules === null}
                emptyTitle="No schedules"
                emptyMessage="A nightly restart keeps modded servers healthy; a backup schedule keeps the world safe."
            />
        </div>
    );
}
