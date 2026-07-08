/** Los 3 patrones de manifest reconocidos, compartidos por diagnostics,
 * codelens y decorations — una sola fuente para no divergir entre features. */
export const GA_MANIFEST_RE = /manifests[\\/].*\.(agent|taskgraph)\.yaml$/;
export const GA_TOOL_RE = /tools[\\/][^\\/]+[\\/]tool\.yaml$/;
export const HUB_PLUGIN_RE = /frontend_dashboard[\\/]src[\\/]plugins[\\/][^\\/]+[\\/]plugin\.yaml$/;
