// Server detail — the tab group of plan 53 §3.2. Tabs route through the
// `:tab` segment; new tabs are DETAIL_TABS entries + a tab component.
import { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { Box, Play, RotateCcw, Square, Trash2 } from 'lucide-react';

import { PageTopbar, Pill, useToast } from 'serverkit-sdk';
import { Button, Checkbox, Spinner } from './primitives.jsx';

import minecraftApi from '../api.js';
import { editionLabel, STATUS_KIND } from '../helpers.js';
import OverviewTab from './tabs/OverviewTab.jsx';
import ConsoleTab from './tabs/ConsoleTab.jsx';
import PlayersTab from './tabs/PlayersTab.jsx';
import SettingsTab from './tabs/SettingsTab.jsx';
import BackupsTab from './tabs/BackupsTab.jsx';
import SchedulesTab from './tabs/SchedulesTab.jsx';

const DETAIL_TABS = [
    { id: 'overview', label: 'Overview', component: OverviewTab },
    { id: 'console', label: 'Console', component: ConsoleTab },
    { id: 'players', label: 'Players', component: PlayersTab },
    { id: 'settings', label: 'Settings', component: SettingsTab },
    { id: 'backups', label: 'Backups', component: BackupsTab },
    { id: 'schedules', label: 'Schedules', component: SchedulesTab },
];

export default function ServerDetail() {
    const { id, tab } = useParams();
    const navigate = useNavigate();
    const toast = useToast();
    const [server, setServer] = useState(null);
    const [notFound, setNotFound] = useState(false);
    const [busy, setBusy] = useState('');
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [removeVolume, setRemoveVolume] = useState(false);

    const refresh = useCallback(() => {
        minecraftApi.get(id)
            .then(setServer)
            .catch((err) => {
                if (err.status === 404) setNotFound(true);
                else toast.error(err.message || 'Failed to load server');
            });
    }, [id, toast]);

    useEffect(() => { refresh(); }, [refresh]);

    async function lifecycle(action) {
        setBusy(action);
        try {
            const result = await minecraftApi[action](id);
            if (result.status) setServer((s) => ({ ...s, status: result.status }));
            toast.success(`Server ${action === 'restart' ? 'restarted' : `${action}ed`}`);
        } catch (err) {
            toast.error(err.message || `Failed to ${action} server`);
        } finally {
            setBusy('');
            refresh();
        }
    }

    async function handleDelete() {
        setBusy('delete');
        try {
            await minecraftApi.remove(id, removeVolume);
            toast.success(`Server "${server.name}" deleted`);
            navigate('/minecraft');
        } catch (err) {
            toast.error(err.message || 'Failed to delete server');
            setBusy('');
        }
    }

    if (notFound) return <Navigate to="/minecraft" replace />;
    if (!server) {
        return <div className="mc-page mc-page--loading"><Spinner size="lg" /></div>;
    }

    const activeTab = DETAIL_TABS.find((t) => t.id === tab);
    if (!activeTab) return <Navigate to={`/minecraft/${id}/overview`} replace />;
    const TabComponent = activeTab.component;

    const running = server.status === 'running';
    const topbarTabs = DETAIL_TABS.map((t) => ({
        to: `/minecraft/${id}/${t.id}`, label: t.label,
    }));

    return (
        <div className="mc-page">
            <PageTopbar
                icon={<Box size={18} />}
                title={server.name}
                meta={(
                    <span className="mc-detail__meta">
                        {editionLabel(server)}
                        {' · '}
                        <Pill kind={STATUS_KIND[server.status] || 'gray'}>{server.status}</Pill>
                    </span>
                )}
                tabs={topbarTabs}
                actions={(
                    <>
                        {running ? (
                            <Button variant="ghost" size="sm" disabled={!!busy}
                                    onClick={() => lifecycle('stop')}>
                                <Square size={14} /> Stop
                            </Button>
                        ) : (
                            <Button variant="ghost" size="sm" disabled={!!busy}
                                    onClick={() => lifecycle('start')}>
                                <Play size={14} /> Start
                            </Button>
                        )}
                        <Button variant="ghost" size="sm" disabled={!!busy || !running}
                                onClick={() => lifecycle('restart')}>
                            <RotateCcw size={14} /> Restart
                        </Button>
                        <Button variant="danger" size="sm" disabled={!!busy}
                                onClick={() => setConfirmDelete(true)}>
                            <Trash2 size={14} /> Delete
                        </Button>
                    </>
                )}
            />

            <TabComponent server={server} onRefresh={refresh} />

            {confirmDelete && (
                <div className="modal-overlay" onClick={() => setConfirmDelete(false)}>
                    <div className="modal" onClick={(e) => e.stopPropagation()}>
                        <div className="modal-header">
                            <h3>Delete {server.name}?</h3>
                        </div>
                        <div className="modal-body">
                            <p>
                                This stops the container and closes the firewall port.
                                The world lives on a separate volume.
                            </p>
                            <div className="mc-delete__volume">
                                <Checkbox id="mc-delete-volume" checked={removeVolume}
                                          onCheckedChange={(c) => setRemoveVolume(c === true)} />
                                <label htmlFor="mc-delete-volume">
                                    Also delete the world volume — this is unrecoverable
                                </label>
                            </div>
                        </div>
                        <div className="modal-actions">
                            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
                                Cancel
                            </Button>
                            <Button variant="danger" disabled={busy === 'delete'}
                                    onClick={handleDelete}>
                                {busy === 'delete' ? 'Deleting…' : 'Delete server'}
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
