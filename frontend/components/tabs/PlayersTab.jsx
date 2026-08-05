// Players tab (§3.2): online list with kick/ban/op actions, whitelist manager
// (toggle + add/remove), ops list with deop, ban list with pardon. All RCON
// backed, so it's Java-only — Bedrock gets an honest notice instead.
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, ShieldOff, ShieldPlus, UserMinus, UserX } from 'lucide-react';

import { DataTable, Pill, useToast } from 'serverkit-sdk';
import {
    Button, Input, Card, CardContent, CardHeader, CardTitle,
} from '../primitives.jsx';

import minecraftApi from '../../api.js';

export default function PlayersTab({ server }) {
    const toast = useToast();
    const [players, setPlayers] = useState(null);   // {online, max, players[]}
    const [whitelist, setWhitelist] = useState(null); // {enabled, players[]}
    const [ops, setOps] = useState([]);
    const [bans, setBans] = useState([]);
    const [wlName, setWlName] = useState('');
    const [busy, setBusy] = useState(false);

    const isJava = server.edition !== 'bedrock';

    const refresh = useCallback(() => {
        if (!isJava) return;
        minecraftApi.players(server.id).then(setPlayers).catch(() => setPlayers(null));
        minecraftApi.whitelist(server.id).then(setWhitelist).catch(() => setWhitelist(null));
        minecraftApi.ops(server.id).then((d) => setOps(d.ops || [])).catch(() => setOps([]));
        minecraftApi.bans(server.id).then((d) => setBans(d.bans || [])).catch(() => setBans([]));
    }, [server.id, isJava]);

    useEffect(() => { refresh(); }, [refresh]);

    async function act(promise, successMessage) {
        setBusy(true);
        try {
            await promise;
            if (successMessage) toast.success(successMessage);
        } catch (err) {
            toast.error(err.message || 'Action failed');
        } finally {
            setBusy(false);
            refresh();
        }
    }

    if (!isJava) {
        return (
            <div className="mc-players">
                <Card>
                    <CardContent>
                        <p className="mc-players__bedrock-note">
                            Player management needs RCON, which Bedrock&apos;s dedicated server
                            doesn&apos;t provide. Manage players in-game, or watch joins and
                            parts in the Console tab.
                        </p>
                    </CardContent>
                </Card>
            </div>
        );
    }

    const onlineColumns = [
        {
            key: 'name',
            header: `Online players (${players?.online ?? 0}${players?.max != null ? ` / ${players.max}` : ''})`,
            render: (name) => <span className="mc-players__name">{name}</span>,
        },
        {
            key: 'actions',
            header: '',
            render: (name) => (
                <div className="mc-players__actions">
                    <Button variant="ghost" size="sm" disabled={busy}
                            onClick={() => act(minecraftApi.op(server.id, name), `${name} is now an operator`)}>
                        <ShieldPlus size={14} /> Op
                    </Button>
                    <Button variant="ghost" size="sm" disabled={busy}
                            onClick={() => act(minecraftApi.kick(server.id, name), `Kicked ${name}`)}>
                        <UserMinus size={14} /> Kick
                    </Button>
                    <Button variant="danger" size="sm" disabled={busy}
                            onClick={() => act(minecraftApi.ban(server.id, name), `Banned ${name}`)}>
                        <UserX size={14} /> Ban
                    </Button>
                </div>
            ),
        },
    ];

    return (
        <div className="mc-players">
            <div className="mc-players__toolbar">
                <Button variant="ghost" size="sm" onClick={refresh} disabled={busy}>
                    <RefreshCw size={14} /> Refresh
                </Button>
            </div>

            <DataTable
                columns={onlineColumns}
                data={players?.players || []}
                keyField={(name) => name}
                sortable={false}
                emptyTitle="Nobody online"
                emptyMessage="Share the address from the Overview tab to get your friends in."
            />

            <div className="mc-players__grid">
                <Card>
                    <CardHeader>
                        <CardTitle>
                            Whitelist{' '}
                            {whitelist?.enabled != null && (
                                <Pill kind={whitelist.enabled ? 'green' : 'gray'}>
                                    {whitelist.enabled ? 'on' : 'off'}
                                </Pill>
                            )}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="mc-players__row">
                            <Button variant="outline" size="sm" disabled={busy}
                                    onClick={() => act(
                                        minecraftApi.updateWhitelist(
                                            server.id, whitelist?.enabled ? 'disable' : 'enable'),
                                        whitelist?.enabled ? 'Whitelist disabled' : 'Whitelist enabled')}>
                                Turn {whitelist?.enabled ? 'off' : 'on'}
                            </Button>
                            <Input value={wlName} placeholder="Player name"
                                   onChange={(e) => setWlName(e.target.value)} />
                            <Button size="sm" disabled={busy || !wlName.trim()}
                                    onClick={() => act(
                                        minecraftApi.updateWhitelist(server.id, 'add', wlName.trim()),
                                        `Whitelisted ${wlName.trim()}`).then(() => setWlName(''))}>
                                Add
                            </Button>
                        </div>
                        <ul className="mc-players__list">
                            {(whitelist?.players || []).map((name) => (
                                <li key={name}>
                                    <span>{name}</span>
                                    <Button variant="ghost" size="sm" disabled={busy}
                                            onClick={() => act(
                                                minecraftApi.updateWhitelist(server.id, 'remove', name),
                                                `Removed ${name} from whitelist`)}>
                                        Remove
                                    </Button>
                                </li>
                            ))}
                            {whitelist && whitelist.players?.length === 0 && (
                                <li className="mc-players__empty">No whitelisted players.</li>
                            )}
                        </ul>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader><CardTitle>Operators</CardTitle></CardHeader>
                    <CardContent>
                        <ul className="mc-players__list">
                            {ops.map((name) => (
                                <li key={name}>
                                    <span>{name}</span>
                                    <Button variant="ghost" size="sm" disabled={busy}
                                            onClick={() => act(minecraftApi.deop(server.id, name),
                                                `${name} is no longer an operator`)}>
                                        <ShieldOff size={14} /> Deop
                                    </Button>
                                </li>
                            ))}
                            {ops.length === 0 && (
                                <li className="mc-players__empty">No operators.</li>
                            )}
                        </ul>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader><CardTitle>Banned players</CardTitle></CardHeader>
                    <CardContent>
                        <ul className="mc-players__list">
                            {bans.map((name) => (
                                <li key={name}>
                                    <span>{name}</span>
                                    <Button variant="ghost" size="sm" disabled={busy}
                                            onClick={() => act(minecraftApi.pardon(server.id, name),
                                                `Pardoned ${name}`)}>
                                        Pardon
                                    </Button>
                                </li>
                            ))}
                            {bans.length === 0 && (
                                <li className="mc-players__empty">No banned players.</li>
                            )}
                        </ul>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
