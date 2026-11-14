# ADR 2026-10-14 — Auth del Hubara Agent Gateway con Cognito (resource server MCP + M2M acotado)

- **Status**: Proposed (implementado en rama; Terraform SIN aplicar, backend SIN desplegar)
- **Date**: 2026-10-14
- **Decisiones del operador (2026-10-14)**:
  - el plugin entra con login de usuario PKCE;
  - el gateway llama al backend con un M2M propio, con un cambio mínimo en `require_auth`;
  - **migrar ya a managed login** y exigir `aud`;
  - PII enmascarada, visible sin máscara solo para admin con `ads.pii`.
- **Enmienda 2026-11-10 (auditoría)**: la migración a managed login pasa a ser **opt-in por tienda**
  (`managed_login`, apagado por defecto). Sigue siendo la decisión, pero ya no ocurre sola al aplicar
  este root por otro motivo. Ver «Orden de despliegue».
- **Protected file touched**: ninguno. `src/platform/auth.py` y `config.py` no están en `spinal-files.yaml`, pero es auth de plataforma: de ahí este ADR.
- **Investigación con fuentes**: `agent_gateway/docs/INFORME-auth-mcp-cognito.md`

## Contexto

- **Spec MCP 2026-07-28 §Authorization:**
  - el MCP server es un *resource server* OAuth 2.1;
  - publica Protected Resource Metadata (RFC 9728) y responde 401 con
    `WWW-Authenticate: Bearer resource_metadata=…, scope=…`;
  - MUST validar que el token fue emitido **para él** (audiencia, RFC 8707);
  - MUST NOT aceptar tokens ajenos ni reenviar el token del cliente a otra API (sin
    *token passthrough*).
- **Cognito:**
  - no tiene DCR, CIMD ni token exchange (RFC 8693);
  - sí tiene resource binding (`resource=` en `/oauth2/authorize` → `aud`) desde 2025-10-24,
    pero **solo con managed login** y nunca en `client_credentials`.
- **El pool de prod** (tier ESSENTIALS) tenía:
  - el dominio en Hosted UI clásica (v1);
  - ningún resource server;
  - un único app client (`dashboard-hubara`).
- **El backend hoy:**
  - `require_auth` acepta el JWT del cliente `dashboard` o `HUBARA_SERVICE_TOKEN`, un token
    «dios» sin scopes;
  - un token M2M de Cognito recibe 401.

## Decisión

### Cognito (Terraform `modules/auth` + `main.tf`)

| Pieza | Detalle |
|---|---|
| Resource server `hubara-mcp` | scopes `ads.read`, `ads.pii` |
| Resource server `hubara-api` | scope `ads.read` |
| App client `dsh-hubara-<tenant>` | **público**, `code` + PKCE, callback `http://localhost:53682/callback` (puerto fijo), scopes `openid hubara-mcp/ads.read hubara-mcp/ads.pii`, refresh 30 d, solo `ALLOW_REFRESH_TOKEN_AUTH` |
| App client `agent-gateway-<tenant>` | **confidencial**, `client_credentials`, scope `hubara-api/ads.read`, access 1 h |
| Dominio | Sigue en Hosted UI clásica salvo `managed_login = true` en la entrada de la tienda (`tenants.auto.tfvars`); entonces pasa a managed login v2. Estilos por cliente con `infra/scripts/cognito-managed-login-branding.sh` (el provider 5.x no tiene el recurso). Provider `~> 5.100` |
| SSM | `/hubara/<tenant>/COGNITO_M2M_CLIENT_IDS` (String → `.env` del backend). `/hubara/agent-gateway/<tenant>/BACKEND_M2M_CLIENT_SECRET` (SecureString, **fuera** de `/hubara/<tenant>/` para no entrar en el `.env` de todos los servicios) |

### Entrada: plugin → gateway

- El gateway es resource server.
  - Publica `/.well-known/oauth-protected-resource/mcp` con `authorization_servers = [issuer del
    pool]` y `scopes_supported`.
  - Sin token: 401 con `resource_metadata` + `scope` y sin `error=` (RFC 6750 §3.1).
  - Token inválido: 401 `invalid_token`.
  - Token sin `hubara-mcp/ads.read`: 403 `insufficient_scope`.
- Verificación del token (`hubara_gateway/auth.py`):
  - firma RS256 con el JWKS del pool;
  - `iss` exacto, `exp` y `token_use == access`;
  - `client_id` ∈ `GATEWAY_ALLOWED_CLIENT_IDS`;
  - **`aud` == `GATEWAY_RESOURCE_URL`** (`GATEWAY_REQUIRE_AUDIENCE=true`). Cognito solo emite ese
    `aud` con managed login. Mientras la tienda no lo active, el gateway solo puede correr con
    `GATEWAY_REQUIRE_AUDIENCE=false`, y eso se admite **únicamente** con un recurso `localhost`.
- Consecuencia: un token del dashboard no sirve en el gateway, y un token del gateway no sirve en
  el backend (allí se rechaza el `client_id`).
- El plugin hace el login con `authProvider` (`OAuthClientProvider`) del SDK TS, con `client_id`
  pre-registrado y `resource` = URL del gateway. Detalle en el handoff
  `salesProductsBot/docs/plans/PLAN-003-mcp-gateway-handoff.md`.

### Salida: gateway → backend (sin passthrough)

- El gateway obtiene su propio access token `client_credentials` (scope `hubara-api/ads.read`).
  - Lo cachea hasta `exp − 60 s`, con un solo pedido concurrente: unas 24 emisiones al día, ≈ USD
    1.6 al mes.
  - Ante un 401 del backend pide un token nuevo y reintenta, pero **una sola vez por minuto**:
    Cognito cobra cada emisión y un backend mal configurado no se arregla pidiendo otro.
  - Llama solo `GET /api/ads/*`.
  - La identidad del usuario viaja únicamente en `X-Hubara-On-Behalf-Of` (auditoría; **nunca**
    autoriza).
- Cambio mínimo en `require_auth` (paso 4):
  - un access token cuyo `client_id` ∈ `COGNITO_M2M_CLIENT_IDS` pasa **solo** si un scope de
    `_M2M_SCOPE_RULES` autoriza método + ruta;
  - `hubara-api/ads.read` autoriza `GET` sobre **exactamente las 11 rutas del catálogo del gateway**
    (regex de ruta completa). Quedan fuera `/meta/insights` (Graph en vivo), `/meta/analysis-input`
    (Medusa) y el SSE `/events`;
  - la ruta se toma de `scope["path"]`, lo que enruta Starlette, y no de `request.url.path`, que se
    arma con el header `Host` y se podía falsear con `Host: x/api/ads`. La revisión lo encontró con
    un probe;
  - el token M2M solo se acepta por header, nunca por `?access_token=`;
  - cualquier otra ruta o método da 403;
  - actor `m2m:<client_id>`;
  - con la variable vacía, el comportamiento queda idéntico al actual.
- El gateway **se niega a arrancar** si `HUBARA_SERVICE_TOKEN` está en su entorno.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| `HUBARA_SERVICE_TOKEN` | Token «dios» sin scopes ni expiración; prohibido por el pedido |
| Reenviar el token del usuario | Token passthrough: lo prohíbe la spec MCP |
| Token exchange (RFC 8693) | Cognito no lo soporta |
| On-behalf-of con Pre Token Generation V3 | Atribución criptográfica, pero suma Lambda, latencia y un punto de falla. Queda para fase 2 si la auditoría lo exige |
| Solo allowlist de `client_id`, sin `aud` | Más débil de lo que exige la spec. El operador eligió managed login |
| Fachada RFC 8414 / DCR en el gateway | El cliente TS del plugin tolera el discovery OIDC de Cognito, que no publica `code_challenge_methods_supported`. Queda para cuando un cliente MCP **estricto** lo necesite |

## Orden de despliegue (ninguno ejecutado; cada paso requiere OK)

Mergear este cambio **no toca el login**: solo agrega recursos (resource servers, dos app clients,
dos parámetros SSM). El paso a managed login es una fase aparte.

**Fase A — aditiva (cualquier `terraform apply` de `platform` la trae):**

1. `terraform apply` de `infra/terraform/platform`. Aplica a todos los tenants; no cambia nada
   existente. Revisar el plan antes: nunca se corrió contra prod.
2. Deploy del backend con `COGNITO_M2M_CLIENT_IDS`: `render-env-from-ssm.sh` lo trae de SSM.
3. Gateway local: `.env` con los ids del output `agent_gateway` y el secreto de SSM, y
   `GATEWAY_REQUIRE_AUDIENCE=false` (el recurso es `localhost`); luego `docker compose up`.
4. Plugin: implementar el handoff, configurar `source: mcp` y `mcpUrl`, y correr el e2e.

**Fase B — managed login, por tienda y a propósito (cambia la pantalla de login):**

5. En horario tranquilo: `managed_login = true` en la entrada de la tienda y `terraform apply`.
   Confirmar en el plan que el dominio se actualiza en el lugar y no se recrea.
6. Enseguida, `infra/scripts/cognito-managed-login-branding.sh <pool> <dashboard_client> <dsh_client>`.
7. Verificar el login del dashboard (pantalla nueva) y el de la app móvil (`USER_PASSWORD_AUTH`, que
   no debería cambiar).
8. Volver el gateway a `GATEWAY_REQUIRE_AUDIENCE=true`. Es requisito para publicarlo fuera de
   `localhost`.

## Riesgos

- **Que `/oauth2/token` rechace el `resource`** que manda el SDK TS: no está documentado. El plugin
  puede omitirlo (`validateResourceURL` → `undefined`), porque el `aud` ya lo fija *authorize* y el
  refresh lo conserva. Se valida en el primer login real.
- **Que managed login sin estilo** deje la página de login sin servir: lo cubre el paso 6. Entre el
  paso 5 y el 6 hay una ventana corta, y por eso la migración es opt-in y no viaja con un apply de
  rutina.
- **El secreto M2M da acceso a PII en claro.** El enmascarado vive solo en el gateway: quien tenga el
  secreto del cliente `agent-gateway` puede leer `/conversations` del backend con teléfonos y
  nombres. Se trata como cualquier secreto con acceso a datos de clientes (SSM SecureString, fuera
  del `.env` de la caja).
- **El secreto M2M queda en el state de Terraform** (atributo del app client y valor del SSM), a
  diferencia de los secretos que se cargan fuera de banda. Quien lee el state lo lee.
- **Rotación del secreto M2M:** Cognito admite hasta 2 secretos activos. La rotación queda como
  runbook futuro.
