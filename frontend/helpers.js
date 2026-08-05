// Small shared helpers for the Minecraft pages.

// "Java · Paper" / "Bedrock" — the one-line edition+flavor summary used by
// the list rows and the detail topbar.
export function editionLabel(server) {
    if (server.edition === 'bedrock') return 'Bedrock';
    const flavor = server.flavor || 'vanilla';
    return `Java · ${flavor.charAt(0).toUpperCase()}${flavor.slice(1)}`;
}

// Status → Pill tone, shared by the list and the detail pages.
export const STATUS_KIND = {
    running: 'green',
    creating: 'cyan',
    stopped: 'gray',
    crashed: 'red',
    error: 'red',
};
