// Formatting helpers — local copies of the host's utils/formatBytes.js and
// utils/time.js (host internals are unreachable from a runtime-ESM bundle).
// Behavior matches the host exactly.

const DECIMAL_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB'];
const IEC_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB'];

export function formatBytes(bytes, options = {}) {
    const {
        decimals = 1,
        suffix = true,
        iec = false,
        defaultValue = '-',
    } = options;

    if (bytes === null || bytes === undefined || bytes === '') return defaultValue;

    const value = typeof bytes === 'string' ? Number(bytes) : bytes;
    if (!Number.isFinite(value)) return defaultValue;
    if (value === 0) return suffix ? '0 B' : '0';

    const units = iec ? IEC_UNITS : DECIMAL_UNITS;
    const negative = value < 0;
    const abs = Math.abs(value);

    const exponent = Math.min(
        Math.floor(Math.log(abs) / Math.log(1024)),
        units.length - 1
    );
    const scaled = abs / 1024 ** exponent;

    const places = exponent === 0 ? 0 : decimals;
    let formatted = scaled.toFixed(places);
    if (formatted.includes('.')) {
        formatted = formatted.replace(/\.?0+$/, '');
    }

    const sign = negative ? '-' : '';
    return suffix ? `${sign}${formatted} ${units[exponent]}` : `${sign}${formatted}`;
}

// Compact relative time, e.g. "just now", "4m", "3h", "2d", else a date.
export function timeAgo(iso) {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return '';
    const seconds = Math.floor((Date.now() - then) / 1000);
    if (seconds < 45) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d`;
    return new Date(then).toLocaleDateString();
}

// Humanize a duration in seconds, e.g. "45s", "3m 20s".
export function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '-';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const min = Math.floor(seconds / 60);
    const sec = Math.round(seconds % 60);
    return `${min}m ${sec}s`;
}
