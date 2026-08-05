// Thin API wrapper for the extension's /api/v1/minecraft surface.
// Uses the SDK's ApiClient (the panel's singleton) so JWT refresh, workspace
// headers and error normalization all apply unchanged.
import { api } from 'serverkit-sdk';

const BASE = '/minecraft';

const minecraftApi = {
    // servers
    list: () => api.request(BASE),
    create: (spec) => api.request(BASE, { method: 'POST', body: spec }),
    portCheck: (port, edition) =>
        api.request(`${BASE}/port-check?port=${encodeURIComponent(port)}&edition=${edition}`),
    get: (id) => api.request(`${BASE}/${id}`),
    remove: (id, removeVolume) =>
        api.request(`${BASE}/${id}?remove_volume=${removeVolume ? 1 : 0}`, { method: 'DELETE' }),

    // lifecycle
    start: (id) => api.request(`${BASE}/${id}/start`, { method: 'POST' }),
    stop: (id) => api.request(`${BASE}/${id}/stop`, { method: 'POST' }),
    restart: (id) => api.request(`${BASE}/${id}/restart`, { method: 'POST' }),

    // runtime surfaces
    overview: (id) => api.request(`${BASE}/${id}/overview`),
    logs: (id, tail = 200) => api.request(`${BASE}/${id}/logs?tail=${tail}`),
    rcon: (id, command) => api.request(`${BASE}/${id}/rcon`, { method: 'POST', body: { command } }),

    // players
    players: (id) => api.request(`${BASE}/${id}/players`),
    kick: (id, player, reason) =>
        api.request(`${BASE}/${id}/players/kick`, { method: 'POST', body: { player, reason } }),
    ban: (id, player, reason) =>
        api.request(`${BASE}/${id}/players/ban`, { method: 'POST', body: { player, reason } }),
    pardon: (id, player) =>
        api.request(`${BASE}/${id}/players/pardon`, { method: 'POST', body: { player } }),
    op: (id, player) =>
        api.request(`${BASE}/${id}/players/op`, { method: 'POST', body: { player } }),
    deop: (id, player) =>
        api.request(`${BASE}/${id}/players/deop`, { method: 'POST', body: { player } }),
    bans: (id) => api.request(`${BASE}/${id}/players/bans`),
    ops: (id) => api.request(`${BASE}/${id}/players/ops`),
    whitelist: (id) => api.request(`${BASE}/${id}/whitelist`),
    updateWhitelist: (id, action, player) =>
        api.request(`${BASE}/${id}/whitelist`, { method: 'POST', body: { action, player } }),

    // backups
    backups: (id) => api.request(`${BASE}/${id}/backups`),
    createBackup: (id) => api.request(`${BASE}/${id}/backups`, { method: 'POST' }),
    updateBackupConfig: (id, config) =>
        api.request(`${BASE}/${id}/backups/config`, { method: 'PUT', body: config }),
    restoreBackup: (id, backupId) =>
        api.request(`${BASE}/${id}/backups/${backupId}/restore`, { method: 'POST' }),
    deleteBackup: (id, backupId) =>
        api.request(`${BASE}/${id}/backups/${backupId}`, { method: 'DELETE' }),

    // settings (server.properties grouped form)
    settings: (id) => api.request(`${BASE}/${id}/settings`),
    updateSettings: (id, changes) =>
        api.request(`${BASE}/${id}/settings`, { method: 'PUT', body: { changes } }),

    // schedules (core cron rails)
    schedules: (id) => api.request(`${BASE}/${id}/schedules`),
    createSchedule: (id, schedule) =>
        api.request(`${BASE}/${id}/schedules`, { method: 'POST', body: schedule }),
    updateSchedule: (id, scheduleId, patch) =>
        api.request(`${BASE}/${id}/schedules/${scheduleId}`, { method: 'PUT', body: patch }),
    deleteSchedule: (id, scheduleId) =>
        api.request(`${BASE}/${id}/schedules/${scheduleId}`, { method: 'DELETE' }),
};

export default minecraftApi;
