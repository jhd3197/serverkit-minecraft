// Overview tab (§3.2): KPI tiles (status, players, memory, CPU, uptime), the
// "Share with friends" card with a copy button + per-edition client
// instructions, and the next-scheduled-restart countdown.
import { useEffect, useState } from 'react';
import { Copy, Check, Cpu, MemoryStick, Timer, Users } from 'lucide-react';

import { KpiBand, MetricCard, Pill, useToast } from 'serverkit-sdk';
import { Button, Card, CardContent, CardHeader, CardTitle } from '../primitives.jsx';
import { useClipboard } from '../../hooks/useClipboard.js';
import { formatBytes, formatDuration } from '../../utils/format.js';

import minecraftApi from '../../api.js';
import { STATUS_KIND } from '../../helpers.js';

function formatCountdown(iso) {
    const seconds = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
    if (seconds <= 0) return 'due now';
    return `in ${formatDuration(seconds)}`;
}

export default function OverviewTab({ server }) {
    const toast = useToast();
    const { copy, copied } = useClipboard({ successMessage: 'Address copied — send it to your friends' });
    const [overview, setOverview] = useState(null);

    useEffect(() => {
        let cancelled = false;
        const load = () => minecraftApi.overview(server.id)
            .then((data) => { if (!cancelled) setOverview(data); })
            .catch((err) => { if (!cancelled) toast.error(err.message || 'Failed to load overview'); });
        load();
        // Light refresh while the tab is open (§3.5: nobody looking, no polling).
        const timer = setInterval(load, 15000);
        return () => { cancelled = true; clearInterval(timer); };
    }, [server.id, toast]);

    const status = overview?.status || server.status;
    const players = overview?.players || {};
    const stats = overview?.stats || null;
    const address = overview?.address;

    return (
        <div className="mc-overview">
            <KpiBand>
                <MetricCard
                    tone="green"
                    label="Status"
                    value={<Pill kind={STATUS_KIND[status] || 'gray'}>{status}</Pill>}
                />
                <MetricCard
                    icon={<Users size={16} />}
                    tone="cyan"
                    label="Players online"
                    value={players.online != null ? players.online : '—'}
                    unit={players.max != null ? `/ ${players.max}` : undefined}
                />
                <MetricCard
                    icon={<MemoryStick size={16} />}
                    tone="violet"
                    label="Memory"
                    value={stats?.mem_usage_bytes != null ? formatBytes(stats.mem_usage_bytes) : '—'}
                    unit={stats?.mem_limit_bytes != null ? `/ ${formatBytes(stats.mem_limit_bytes)}` : server.memory}
                />
                <MetricCard
                    icon={<Cpu size={16} />}
                    tone="amber"
                    label="CPU"
                    value={stats?.cpu_percent != null ? `${stats.cpu_percent.toFixed(1)}%` : '—'}
                />
                <MetricCard
                    icon={<Timer size={16} />}
                    tone="accent"
                    label="Uptime"
                    value={overview?.uptime_seconds != null ? formatDuration(overview.uptime_seconds) : '—'}
                    secondary
                />
            </KpiBand>

            <div className="mc-overview__grid">
                <Card className="mc-share">
                    <CardHeader>
                        <CardTitle>Share with friends</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {address ? (
                            <>
                                <div className="mc-share__address-row">
                                    <code className="mc-address mc-address--lg">{address}</code>
                                    <Button variant="outline" size="sm"
                                            onClick={() => copy(address)}>
                                        {copied ? <Check size={14} /> : <Copy size={14} />}
                                        {copied ? 'Copied' : 'Copy'}
                                    </Button>
                                </div>
                                {server.edition === 'bedrock' ? (
                                    <ol className="mc-share__steps">
                                        <li>Open Minecraft (Bedrock) → Play → Servers.</li>
                                        <li>Scroll down → Add Server.</li>
                                        <li>Enter the address and port separately, then join.</li>
                                    </ol>
                                ) : (
                                    <ol className="mc-share__steps">
                                        <li>Open Minecraft (Java) → Multiplayer → Add Server.</li>
                                        <li>Paste the address into Server Address.</li>
                                        <li>Join Server — you&apos;re in.</li>
                                    </ol>
                                )}
                            </>
                        ) : (
                            <p className="mc-share__hint">
                                Set your server&apos;s public IP in Settings to show a shareable
                                address here. Friends connect on port {server.port}.
                            </p>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Server details</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <dl className="mc-facts">
                            <div className="mc-facts__row">
                                <dt>Version</dt><dd>{server.version || 'latest'}</dd>
                            </div>
                            <div className="mc-facts__row">
                                <dt>World</dt><dd>{server.world_name}</dd>
                            </div>
                            {server.seed && (
                                <div className="mc-facts__row">
                                    <dt>Seed</dt><dd><code>{server.seed}</code></dd>
                                </div>
                            )}
                            <div className="mc-facts__row">
                                <dt>Memory limit</dt><dd>{server.memory}</dd>
                            </div>
                            <div className="mc-facts__row">
                                <dt>Next scheduled restart</dt>
                                <dd>
                                    {overview?.next_restart_at
                                        ? formatCountdown(overview.next_restart_at)
                                        : 'None scheduled'}
                                </dd>
                            </div>
                        </dl>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
