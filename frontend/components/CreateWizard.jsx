// Create wizard (plan 53 §3.1) — one screen, sensible defaults:
// edition → flavor (Java only) → version → world name + seed → memory
// (default 2G, warns below 1G) → port (default 25565/19132, availability
// checked against the backend) → EULA checkbox (D3: never pre-accepted).
// Successful create lands on the Deploy Console's live logs (D4).
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box } from 'lucide-react';

import { PageTopbar, useToast } from 'serverkit-sdk';
import {
    Button, Input, Checkbox, FormField, FormRow,
    Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from './primitives.jsx';

import minecraftApi from '../api.js';

const EULA_URL = 'https://aka.ms/MinecraftEULA';
const FLAVORS = ['vanilla', 'paper', 'fabric', 'forge'];
const DEFAULT_PORTS = { java: 25565, bedrock: 19132 };

// Client-side mirror of gamekit.compose memory parsing, for instant feedback.
// The backend re-validates authoritatively.
function memoryToBytes(value) {
    const m = /^\s*(\d+)\s*([bkmg])?\s*$/i.exec(String(value || ''));
    if (!m) return null;
    const factor = { B: 1, K: 1024, M: 1024 ** 2, G: 1024 ** 3 }[(m[2] || 'G').toUpperCase()];
    return parseInt(m[1], 10) * factor;
}

export default function CreateWizard() {
    const navigate = useNavigate();
    const toast = useToast();

    const [name, setName] = useState('');
    const [edition, setEdition] = useState('java');
    const [flavor, setFlavor] = useState('vanilla');
    const [version, setVersion] = useState('');
    const [worldName, setWorldName] = useState('world');
    const [seed, setSeed] = useState('');
    const [memory, setMemory] = useState('2G');
    const [port, setPort] = useState(String(DEFAULT_PORTS.java));
    const [eula, setEula] = useState(false);
    const [portStatus, setPortStatus] = useState(null); // null | {available, suggestion}
    const [submitting, setSubmitting] = useState(false);

    // Switching editions resets the port default and re-checks availability.
    useEffect(() => {
        setPort(String(DEFAULT_PORTS[edition]));
    }, [edition]);

    useEffect(() => {
        const portNum = parseInt(port, 10);
        if (!portNum) {
            setPortStatus(null);
            return undefined;
        }
        const timer = setTimeout(() => {
            minecraftApi.portCheck(portNum, edition)
                .then(setPortStatus)
                .catch(() => setPortStatus(null));
        }, 400);
        return () => clearTimeout(timer);
    }, [port, edition]);

    const memoryBytes = useMemo(() => memoryToBytes(memory), [memory]);
    const lowMemory = memoryBytes !== null && memoryBytes < 1024 ** 3;

    const canSubmit = name.trim() && eula && !submitting
        && (!portStatus || portStatus.available);

    async function handleSubmit(e) {
        e.preventDefault();
        if (!canSubmit) return;
        setSubmitting(true);
        try {
            const result = await minecraftApi.create({
                name: name.trim(),
                edition,
                flavor,
                version: version.trim() || 'latest',
                world_name: worldName.trim() || 'world',
                seed: seed.trim(),
                memory,
                port: parseInt(port, 10),
                eula_accepted: eula,
            });
            toast.success(`Server "${result.server.name}" is being created`);
            // D4 — watch the image pull + world generation on the Deploy Console.
            navigate(result.deploy_url || `/deployments/${result.job_id}`);
        } catch (err) {
            toast.error(err.message || 'Failed to create server');
            setSubmitting(false);
        }
    }

    return (
        <div className="mc-page">
            <PageTopbar icon={<Box size={18} />} title="New Minecraft server" />
            <form className="mc-wizard" onSubmit={handleSubmit}>
                <FormRow>
                    <FormField label="Server name" htmlFor="mc-name" required
                               hint="Lowercase letters, digits, dashes — also the world folder name.">
                        <Input id="mc-name" value={name} placeholder="my-server"
                               onChange={(e) => setName(e.target.value)} />
                    </FormField>
                    <FormField label="Edition" htmlFor="mc-edition">
                        <Select value={edition} onValueChange={setEdition}>
                            <SelectTrigger id="mc-edition"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="java">Java Edition</SelectItem>
                                <SelectItem value="bedrock">Bedrock Edition</SelectItem>
                            </SelectContent>
                        </Select>
                    </FormField>
                </FormRow>

                <FormRow>
                    <FormField label="Flavor" htmlFor="mc-flavor"
                               hint={edition === 'bedrock'
                                   ? 'Bedrock runs the vanilla dedicated server.'
                                   : 'Paper is a good default for plugins.'}>
                        <Select value={flavor} onValueChange={setFlavor}
                                disabled={edition === 'bedrock'}>
                            <SelectTrigger id="mc-flavor"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                {FLAVORS.map((f) => (
                                    <SelectItem key={f} value={f}>
                                        {f.charAt(0).toUpperCase() + f.slice(1)}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </FormField>
                    <FormField label="Version" htmlFor="mc-version"
                               hint="Blank = latest. Pin e.g. 1.21.4 to hold a version.">
                        <Input id="mc-version" value={version} placeholder="latest"
                               onChange={(e) => setVersion(e.target.value)} />
                    </FormField>
                </FormRow>

                <FormRow>
                    <FormField label="World name" htmlFor="mc-world">
                        <Input id="mc-world" value={worldName}
                               onChange={(e) => setWorldName(e.target.value)} />
                    </FormField>
                    <FormField label="Seed" htmlFor="mc-seed" hint="Optional.">
                        <Input id="mc-seed" value={seed}
                               onChange={(e) => setSeed(e.target.value)} />
                    </FormField>
                </FormRow>

                <FormRow>
                    <FormField label="Memory limit" htmlFor="mc-memory"
                               hint={lowMemory
                                   ? 'Below 1G the server may struggle to generate terrain.'
                                   : 'JVM heap (Java) / container cap (Bedrock).'}
                               error={memoryBytes === null ? "Use a value like '512M' or '2G'." : null}>
                        <Input id="mc-memory" value={memory}
                               onChange={(e) => setMemory(e.target.value)} />
                    </FormField>
                    <FormField label="Port" htmlFor="mc-port"
                               hint={edition === 'bedrock' ? 'UDP 19132 is the Bedrock default.' : 'TCP 25565 is the Java default.'}
                               error={portStatus && !portStatus.available
                                   ? `Port ${port} is already in use${portStatus.suggestion ? ` — try ${portStatus.suggestion}` : ''}.`
                                   : null}>
                        <Input id="mc-port" type="number" value={port}
                               onChange={(e) => setPort(e.target.value)} />
                    </FormField>
                </FormRow>

                <div className="mc-wizard__eula">
                    <Checkbox id="mc-eula" checked={eula}
                              onCheckedChange={(checked) => setEula(checked === true)} />
                    <label htmlFor="mc-eula" className="mc-wizard__eula-label">
                        I accept the{' '}
                        <a href={EULA_URL} target="_blank" rel="noreferrer">Minecraft EULA</a>
                        {' '}(required to run a server — we never accept it for you)
                    </label>
                </div>

                <div className="mc-wizard__actions">
                    <Button type="button" variant="ghost" onClick={() => navigate('/minecraft')}>
                        Cancel
                    </Button>
                    <Button type="submit" disabled={!canSubmit}>
                        {submitting ? 'Creating…' : 'Create server'}
                    </Button>
                </div>
            </form>
        </div>
    );
}
