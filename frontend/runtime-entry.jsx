// Runtime-ESM entry for ServerKit's no-rebuild loader (same pattern as
// serverkit-analytics / serverkit-gpu). CSS is imported as a STRING (?inline)
// and injected once at module load, so the single dist/index.mjs the panel
// blob-imports carries its own styles. Shared libs (react, react-router-dom,
// serverkit-sdk) are externalized by vite.config and resolved to the panel's
// singletons via its import map; lucide-react and the Radix primitives are
// bundled.
import css from './styles/minecraft.css?inline';

if (typeof document !== 'undefined' && !document.getElementById('serverkit-minecraft-styles')) {
    const style = document.createElement('style');
    style.id = 'serverkit-minecraft-styles';
    style.textContent = css;
    document.head.appendChild(style);
}

// Named exports match the `component` values in plugin.json's route
// contributions; resolveComponent(slug, name) picks them up at runtime.
// Deliberately NO default export (PluginLoader auto-renders those globally).
export { MinecraftExtension } from './components/MinecraftExtension.jsx';
