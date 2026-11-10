# ADR 2026-10-14 — Hubara Agent Gateway: MCP server de solo lectura sobre `/api/ads/*`

- **Status**: Proposed (implementado en rama; sin desplegar)
- **Date**: 2026-10-14
- **Context surfacing**: el plugin de DeepSeek Harness `@acktos/dsh-hubara` necesita los datos del menú Ads como fuente MCP (`source: mcp`), con la misma forma que la fuente REST/fixture.
- **Protected file touched**: ninguno. Paquete nuevo `agent_gateway/` en la raíz, fuera de `hubara_agency/` y del compose generado.
- **Relacionados**: `docs/adr/2026-10-14-agent-gateway-auth.md` (auth) · `agent_gateway/docs/INFORME-auth-mcp-cognito.md` (investigación con fuentes)

## Contexto

El dashboard consulta 11 rutas `GET /api/ads/*`. El plugin `@acktos/dsh-hubara` ya trae un cliente
MCP propio (`McpAdsSource`, `@modelcontextprotocol/sdk` 1.32.1, protocolo 2025-11-25). Su contrato
está fijado en `src/shared/resources.ts` y `src/shared/window.ts` del plugin:

- Cada tool `ads_get_*` devuelve en `structuredContent` el body exacto de la ruta GET.
- `ads_get_analysis_agents` es la única excepción: su body es un array y va envuelto en `{items}`.
- Un 404 se reporta como `isError` con «404/not_found»; el plugin lo toma como resultado de dominio.
- Cualquier otro error también va como `isError`, y el plugin lo toma como falla de infraestructura.

## Decisión

1. **Servicio aparte, no dentro de la API.** Vive en `agent_gateway/`, es un proyecto uv propio
   (Python 3.12, `mcp` 2.3.0) y tiene su propia imagen.
   - Habla con el backend **solo por HTTP GET**.
   - No importa nada de `hubara_agency`, así que no entra en P-9/P-23/P-28: no es un plugin ni
     toca `src/`. Eso aísla el radio de explosión.
   - Escala y se despliega aparte.
   - El backend sigue siendo la única fuente de verdad, incluido `OrderFacts` (gotcha 13).
2. **SDK oficial de Python, `Server` de bajo nivel.**
   - En `mcp` 2.x, `FastMCP` se renombró `MCPServer`.
   - Las 11 tools son *data-driven* (catálogo → ruta), así que `on_list_tools` / `on_call_tool`
     dan control exacto de `structuredContent`, `outputSchema` e `isError`. `MCPServer` reescribe
     la salida según el tipo de retorno.
   - El SDK TS se queda del lado del cliente, en el plugin.
3. **Transporte Streamable HTTP stateless con `json_response`.**
   - Sin sesiones, compatible con clientes 2025-11-25 (`initialize`) y con la spec 2026-07-28.
   - Protección de Host y Origin (DNS rebinding) configurada explícitamente; el SDK solo la
     autoactiva en localhost.
   - Límite de body (413), rate limit por principal (429) y timeout + tope de tamaño hacia el
     backend.
   - **Deny-by-default:** toda ruta exige token salvo `/healthz` y el PRM.
   - **Los argumentos se validan contra el `inputSchema` publicado:** un nombre desconocido o un
     valor que no es string se rechaza con `invalid_arguments`. Antes se ignoraba en silencio y, por
     ejemplo, `window: 7` devolvía la ventana por defecto.
   - **Nada imprevisto sale crudo:** una excepción no prevista se responde como `isError`
     `internal_error` con solo el tipo. El log lleva el tipo y dónde ocurrió, sin el mensaje de la
     excepción (puede traer URLs o datos).
4. **El contrato se prueba, no se supone.**
   - `outputSchema` se **genera** desde los contratos Zod del dashboard
     (`scripts/gen_output_schemas.mts`) como draft-07, en modo input y sin
     `additionalProperties: false`, para que un campo nuevo del backend no rompa al plugin.
   - Un test de drift falla si los Zod cambian y nadie regenera.
   - Si el backend devuelve un body fuera de contrato, el gateway responde `isError`
     `contract_violation`, no un `structuredContent` inválido.
   - Tests golden contra las 44 capturas reales de prod seudonimizadas (`clean/` del plugin): cada
     tool debe producir **exactamente** el request capturado y devolver **exactamente** el body.
   - E2E con el `McpAdsSource` real del plugin por HTTP: 12/12 vistas idénticas a `FixtureAdsSource`.
   - Las capturas **no se copian** a este repo (es público) y se leen por `HUBARA_ADS_FIXTURES`.
5. **Solo lectura por construcción.**
   - No hay tools de escritura, y el cliente HTTP solo hace `GET` a paths bajo `/api/ads/`.
   - Las rutas del catálogo y las que el backend autoriza al M2M son la misma lista: un test del
     gateway lee la regla del backend con `ast` y exige igualdad exacta (un solo scope, solo `GET`,
     los mismos patrones).
   - Los ids de los argumentos tienen allowlist `[A-Za-z0-9_.:-]`, sin `/` y sin solo puntos: un
     `..` normalizado por el cliente HTTP alcanzaría otra ruta.
   - Las tools llevan `readOnlyHint: true`, `destructiveHint: false` e `idempotentHint: true`.
     Son pistas; lo que realmente garantiza la lectura es lo anterior.
6. **PII enmascarada por defecto en el servidor.**
   - Cada campo de una conversación está **clasificado a mano** en `hubara_gateway/pii.py` como
     seguro o personal. Solo viajan los clasificados, y del nivel superior del body solo
     `campaign_id`, `conversations` y `orders_stale`.
   - Las listas **no** se derivan del schema generado (auditoría 2026-11-10): así salió sin tapar
     `neighborhood` cuando el dashboard lo agregó al contrato. Un campo del contrato sin clasificar
     hace fallar un test y, mientras tanto, no viaja.
   - Reemplaza `phone_number`, `name` e `id` (que contiene el teléfono) con el formato de
     `maskPhone` / `maskName` del plugin, y pone `city` y `neighborhood` en `null`.
   - El shape del contrato no cambia, y el resultado lleva `_meta["hubara/pii_masked"]`.
   - Hallazgo: el backend real emite 3 campos que **no** están en el Zod (`order_status`,
     `order_value_cop`, `source_id`). El plugin igual los descarta al parsear. Si hacen falta, se
     agregan al contrato del dashboard (L-10).
   - Sin enmascarar solo con el scope `hubara-mcp/ads.pii` **y** el grupo `admin` (ver el ADR de auth).
7. **Docker.**
   - `agent_gateway/Dockerfile` multi-stage (uv → `python:3.12-slim`), usuario 10001,
     `HEALTHCHECK` a `/healthz`.
   - `agent_gateway/docker-compose.yml` propio: loopback, `read_only`, `cap_drop: ALL`,
     `no-new-privileges`, `mem_limit: 256m`.
   - **No** entra en `docker-compose.base.yml` (drift P-20) ni en `infra/compose/docker-compose.prod.yml`.
   - Configuración solo por env; sin secretos en la imagen ni en el repo.

## Escrituras (fase posterior, solo diseño)

Pausar o activar campañas y aprobar corridas requiere todo esto, en un ADR propio:

- scope `hubara-mcp/ads.write` + grupo `admin` en el gateway, y `hubara-api/ads.write` M2M acotado a
  las 3 rutas POST;
- doble aprobación humana: (1) el gateway devuelve el plan sin ejecutar (`InputRequiredResult` /
  elicitation) y (2) `tools/pre-execute` de DSH bloquea `mcp__hubara__ads_*` de escritura
  (`WRITE_TOOLS_DEFAULT` del plugin);
- `Idempotency-Key` obligatorio.

## Consecuencias

- **No es todo el menú Ads.** El contrato del plugin tiene 11 lecturas. La vista «Dónde compran»
  (`GET /api/ads/purchase-places`, posterior a este diseño) no está expuesta: sumarla es una tool
  nueva en el plugin, en el catálogo y en la regla M2M del backend.

- **Despliegue a prod pendiente** (otro ADR u operación):
  - Caddy hoy hace `reverse-proxy` a un único upstream; publicar `/mcp` exige un Caddyfile con
    ruteo por path;
  - la caja de prod tiene 4 GB y va justa, aunque el gateway queda limitado a 256 MB;
  - hasta entonces, el gateway corre en local (Docker) contra el backend real.
- **Rate limit en memoria:** vale para una réplica. Con más réplicas, mover el contador a un store
  compartido.
- **Bug conocido:** `/api/ads/meta/status` devuelve 500 sin credenciales AWS (`meta_oauth.py:82`). El
  gateway lo reporta como `backend_error 500` (infra) y el plugin lo degrada. Se arregla en otra tarea.
