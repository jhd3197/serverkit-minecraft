// Minecraft UI — one splat route (`minecraft/*`) owns the whole surface: the
// list, the one-screen create wizard, and the server detail tab group
// (Overview / Console / Players / Settings / Backups / Schedules — routed
// through the `:tab` segment in ServerDetail.jsx).
//
// Runtime-ESM build: react-router-dom resolves to the panel's singleton via
// the import map; PageTopbar/DataTable/etc. come from 'serverkit-sdk'; the
// rest are local primitives.
import { Routes, Route, Navigate } from 'react-router-dom';

import ServerList from './ServerList.jsx';
import CreateWizard from './CreateWizard.jsx';
import ServerDetail from './ServerDetail.jsx';

export function MinecraftExtension() {
    return (
        <Routes>
            <Route index element={<ServerList />} />
            <Route path="new" element={<CreateWizard />} />
            <Route path=":id" element={<Navigate to="overview" replace />} />
            <Route path=":id/:tab" element={<ServerDetail />} />
        </Routes>
    );
}

// No default export on purpose: PluginLoader legacy-auto-renders any plugin
// default export globally — a sub-router mounted that way runs outside its
// route and swallows the current location. The route contribution resolves
// the NAMED export via resolveComponent.
