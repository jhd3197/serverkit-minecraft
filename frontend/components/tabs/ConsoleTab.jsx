// Console tab (§3.2): live container log tail + an RCON command box with
// history and common-command chips, echoing commands and responses into the
// same stream. Logs are polled only while the tab is open (§3.5). Bedrock
// has no RCON — the console honestly degrades to log-only there.
import { useEffect, useRef, useState } from 'react';
import { CornerDownLeft } from 'lucide-react';

import { useToast } from 'serverkit-sdk';
import { Button, Input } from '../primitives.jsx';

import minecraftApi from '../../api.js';

const COMMAND_CHIPS = ['say ', 'weather clear', 'time set day', 'difficulty normal'];
const POLL_MS = 3000;

export default function ConsoleTab({ server }) {
    const toast = useToast();
    const [lines, setLines] = useState([]);
    const [command, setCommand] = useState('');
    const [history, setHistory] = useState([]);
    const [historyIndex, setHistoryIndex] = useState(-1);
    const [sending, setSending] = useState(false);
    const bodyRef = useRef(null);
    const stickToBottom = useRef(true);

    const canRcon = server.edition !== 'bedrock';

    // Log tail polling — only while this tab is mounted.
    useEffect(() => {
        let cancelled = false;
        const load = () => minecraftApi.logs(server.id, 300)
            .then((data) => {
                if (cancelled) return;
                const text = data.logs || '';
                setLines(text ? text.split('\n').filter((l) => l.length) : []);
            })
            .catch(() => { /* a stopped/creating server has no logs yet — keep the stream as-is */ });
        load();
        const timer = setInterval(load, POLL_MS);
        return () => { cancelled = true; clearInterval(timer); };
    }, [server.id]);

    // Auto-scroll unless the user scrolled up to read history.
    useEffect(() => {
        if (stickToBottom.current && bodyRef.current) {
            bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
        }
    }, [lines]);

    function handleScroll() {
        const el = bodyRef.current;
        if (!el) return;
        stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    }

    async function sendCommand(raw) {
        const cmd = (raw ?? command).trim();
        if (!cmd || sending) return;
        setSending(true);
        setCommand('');
        setHistory((h) => [...h, cmd]);
        setHistoryIndex(-1);
        setLines((prev) => [...prev, `> ${cmd}`]);
        try {
            const result = await minecraftApi.rcon(server.id, cmd);
            if (result.output) {
                setLines((prev) => [...prev, ...String(result.output).split('\n')]);
            }
        } catch (err) {
            setLines((prev) => [...prev, `Error: ${err.message || 'RCON failed'}`]);
            toast.error(err.message || 'RCON command failed');
        } finally {
            setSending(false);
            stickToBottom.current = true;
        }
    }

    function handleKeyDown(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            sendCommand();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (!history.length) return;
            const next = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1);
            setHistoryIndex(next);
            setCommand(history[next]);
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (historyIndex === -1) return;
            const next = historyIndex + 1;
            if (next >= history.length) {
                setHistoryIndex(-1);
                setCommand('');
            } else {
                setHistoryIndex(next);
                setCommand(history[next]);
            }
        }
    }

    return (
        <div className="mc-console">
            <div className="mc-console__body" ref={bodyRef} onScroll={handleScroll}>
                {lines.length === 0 ? (
                    <div className="mc-console__empty">
                        No log output yet — the server may still be generating its world.
                    </div>
                ) : (
                    lines.map((line, i) => (
                        <div key={i} className="mc-console__line">{line}</div>
                    ))
                )}
            </div>

            {canRcon ? (
                <div className="mc-console__composer">
                    <div className="mc-console__chips">
                        {COMMAND_CHIPS.map((chip) => (
                            <Button key={chip} variant="outline" size="sm"
                                    onClick={() => setCommand(chip)}>
                                {chip.trim()}
                            </Button>
                        ))}
                    </div>
                    <div className="mc-console__input-row">
                        <Input
                            value={command}
                            placeholder="RCON command — e.g. say hello, list, weather clear"
                            onChange={(e) => setCommand(e.target.value)}
                            onKeyDown={handleKeyDown}
                            disabled={sending}
                        />
                        <Button onClick={() => sendCommand()} disabled={sending || !command.trim()}>
                            <CornerDownLeft size={14} /> Send
                        </Button>
                    </div>
                </div>
            ) : (
                <div className="mc-console__note">
                    Bedrock&apos;s dedicated server has no RCON — the console shows the live
                    log only. Player commands run in-game.
                </div>
            )}
        </div>
    );
}
