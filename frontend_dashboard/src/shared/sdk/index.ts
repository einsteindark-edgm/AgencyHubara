/**
 * Hubara SDK (frontend) — la superficie pública del shell para plugins.
 *
 * Espejo TS de `hubara_agency/src/sdk/` (ADR-2026-06-12). Un plugin frontend
 * importa de `@/shared/sdk` (y de sí mismo vía `@plugins/<id>/frontend/...`);
 * el resto de `@/shared/*` sigue disponible pero la superficie de plataforma
 * canónica es ESTA — lo que está acá tiene contrato de estabilidad y check.
 *
 * Qué expone:
 * - PluginHost (F5): el ÚNICO canal shell↔plugin. Los Pages NO reciben props;
 *   usan `usePluginHost()` + `useSelection("<plugin-id>")`.
 * - apiClient / ApiError: el ÚNICO lugar con `fetch`. Todo boundary HTTP se
 *   parsea con Zod en `entities/<e>/contracts.ts` del plugin.
 * - subscribeSse: suscripción server-sent-events con el mismo contrato.
 *
 * Regla de oro: ningún export nuevo sin (a) su check en los arch-tests,
 * (b) su uso en el template del CLI, (c) su doc en docs/_sdk/.
 */
export {
  PluginHostProvider,
  usePluginHost,
  useSelection,
} from "../lib/plugin-host";
export type { PluginHostState } from "../lib/plugin-host";

export { apiClient, ApiError } from "../api/client";
export type { ApiRequestInit } from "../api/client";

export { subscribeSse } from "../api/sse";
export type { SseHandlers, SseSubscription } from "../api/sse";
