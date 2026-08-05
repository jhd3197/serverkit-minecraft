// Server list — Marketplace-style summary of every Minecraft server on this
// box: name, edition/flavor, live status, players online, share address.
// Live data (status/players/address) is filled in by a per-server overview
// pass after the cheap row list lands; failures degrade to '—' so one dead
// server never blanks the page.
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Plus } from 'lucide-react';

import { PageTopbar, DataTable, Pill, useToast } from 'serverkit-sdk';
import { Button } from './primitives.jsx';
import { timeAgo } from '../utils/format.js';

import minecraftApi from '../api.js';
import { editionLabel, STATUS_KIND } from '../helpers.js';

export default function ServerList() {
    const navigate = useNavigate();
    const toast = useToast();
    const [servers, setServers] = useState(null);
    // id → { status, players, address } live overlay from the overview route.
    const [live, setLive] = useState({});

    useEffect(() => {
        let cancelled = false;
        minecraftApi.list()
            .then((data) => {
                if (cancelled) return;
                const rows = data.servers || [];
                setServers(rows);
                // Second pass: live status/players/address per server.
                return Promise.allSettled(rows.map((s) => minecraftApi.overview(s.id)))
                    .then((results) => {
                        if (cancelled) return;
                        const overlay = {};
                        results.forEach((r, i) => {
                            if (r.status === 'fulfilled') {
                                overlay[rows[i].id] = r.value;
                            }
                        });
                        setLive(overlay);
                    });
            })
            .catch((err) => {
                if (!cancelled) {
                    toast.error(err.message || 'Failed to load servers');
                    setServers([]);
                }
            });
        return () => { cancelled = true; };
    }, [toast]);

    const columns = [
        {
            key: 'name',
            header: 'Server',
            sortable: true,
            render: (s) => (
                <div className="mc-list__name">
                    <span className="mc-list__name-text">{s.name}</span>
                    <span className="mc-list__world">world: {s.world_name}</span>
                </div>
            ),
        },
        {
            key: 'edition',
            header: 'Edition',
            sortValue: (s) => editionLabel(s),
            render: (s) => editionLabel(s),
        },
        {
            key: 'status',
            header: 'Status',
            sortValue: (s) => live[s.id]?.status || s.status,
            render: (s) => {
                const status = live[s.id]?.status || s.status;
                return <Pill kind={STATUS_KIND[status] || 'gray'}>{status}</Pill>;
            },
        },
        {
            key: 'players',
            header: 'Players',
            render: (s) => {
                const players = live[s.id]?.players;
                if (!players || players.online == null) return '—';
                return `${players.online}${players.max != null ? ` / ${players.max}` : ''}`;
            },
        },
        {
            key: 'address',
            header: 'Address',
            render: (s) => live[s.id]?.address
                ? <code className="mc-address">{live[s.id].address}</code>
                : `:${s.port}`,
        },
        {
            key: 'created_at',
            header: 'Created',
            sortable: true,
            render: (s) => timeAgo(s.created_at),
        },
    ];

    return (
        <div className="mc-page">
            <PageTopbar
                icon={<Box size={18} />}
                title="Minecraft"
                meta="Game servers for your friends"
                actions={(
                    <Button onClick={() => navigate('/minecraft/new')}>
                        <Plus size={15} /> New server
                    </Button>
                )}
            />
            <DataTable
                columns={columns}
                data={servers || []}
                keyField="id"
                loading={servers === null}
                emptyTitle="No Minecraft servers yet"
                emptyMessage="Create one and your friends can join in a few minutes."
                onRowClick={(s) => navigate(`/minecraft/${s.id}`)}
            />
        </div>
    );
}
