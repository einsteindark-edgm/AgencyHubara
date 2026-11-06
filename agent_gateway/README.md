# Hubara Agent Gateway

MCP server de **solo lectura** que expone los mismos datos que el menú **Ads** del dashboard
(`GET /api/ads/*`). Es la fuente `source: mcp` del plugin de DeepSeek Harness `@acktos/dsh-hubara`.

- Decisiones: `docs/adr/2026-10-14-agent-gateway.md` (servicio) y `docs/adr/2026-10-14-agent-gateway-auth.md` (auth).
- Investigación con fuentes: [`docs/INFORME-auth-mcp-cognito.md`](docs/INFORME-auth-mcp-cognito.md).
- Requisitos del lado del plugin: `salesProductsBot/docs/plans/PLAN-003-mcp-gateway-handoff.md`.

```
dsh-hubara ──Bearer (Cognito, aud=URL del gateway, hubara-mcp/ads.read)──▶ gateway /mcp
gateway ──Bearer M2M propio (hubara-api/ads.read) + X-Hubara-On-Behalf-Of──▶ backend GET /api/ads/*
```

## Tools

| Tool | Ruta | Argumentos |
|---|---|---|
| `ads_get_campaigns` | `/api/ads/campaigns` | `window` |
| `ads_get_adsets` | `/api/ads/campaigns/{campaign_id}/adsets` | `campaign_id`, `window` |
| `ads_get_ads` | `/api/ads/campaigns/{campaign_id}/adsets/{adset_id}/ads` | `campaign_id`, `adset_id`, `window` |
| `ads_get_conversations` | `/api/ads/campaigns/{campaign_id}/conversations` | `campaign_id`, `adset_id?`, `window` |
| `ads_get_daily` | `/api/ads/campaigns/{campaign_id}/daily` | `campaign_id`, `adset_id?`, `window` |
| `ads_get_creative` | `/api/ads/ads/{ad_id}/creative` | `ad_id` |
| `ads_get_agent_referrals` | `/api/ads/agent-referrals` | `window` |
| `ads_get_meta_status` | `/api/ads/meta/status` | — |
| `ads_get_analysis_agents` | `/api/ads/analysis/agents` (array → `{items}`) | — |
| `ads_get_analysis_runs` | `/api/ads/analysis/runs` | `campaign_id?`, `limit?` (20) |
| `ads_get_analysis_run` | `/api/ads/analysis/runs/{run_id}` | `run_id` |

- **`window`**: `7d | 30d | 90d | total | YYYY-MM-DD..YYYY-MM-DD` (America/Bogota), por defecto `30d`.
  - Un preset se traduce a `?days=N` y un rango a `?from=&to=`.
  - `total` no manda parámetros, salvo en daily, donde se traduce a `days=90`.
- **Resultado exitoso:** `structuredContent` = body exacto del backend, más el mismo JSON en texto. Cada
  tool publica un `outputSchema` generado de los Zod del dashboard.
- **Errores:**
  - un 404 del backend → `isError` con `404 not_found: …`;
  - cualquier otro → `isError` sin «404»/«not found» (`backend_error <status>`, `backend_unavailable`,
    `invalid_arguments`, `contract_violation`).
- **PII de conversaciones:** se enmascara por defecto (`_meta["hubara/pii_masked"]`). Se ve sin máscara
  solo con scope `hubara-mcp/ads.pii` **y** grupo `admin`.

## Probar

```bash
uv --directory agent_gateway sync
uv --directory agent_gateway run pytest      # 241 tests
```

- **Tests de contrato:** leen las capturas de prod seudonimizadas del repo del plugin (`HUBARA_ADS_FIXTURES`,
  por defecto `../deepseekHarness/AcktosAgency/salesProductsBot/fixtures/ads/prod-2026-10-09/clean`).
  - Sin ellas, se saltan.
  - **No** se copian acá porque este repo es público.
  - Nunca se lee `raw/`.
- **`outputSchema`:** si cambian los contratos Zod del dashboard, hay que regenerarlos. El test
  `test_committed_schemas_match_the_zod_contracts` avisa:

  ```bash
  cd frontend_dashboard && ./node_modules/.bin/tsx ../agent_gateway/scripts/gen_output_schemas.mts
  ```

### E2E con el cliente real del plugin (sin Cognito ni backend)

Levanta el gateway **real** con un emisor JWT local y un backend que reproduce los fixtures:

```bash
cd agent_gateway && PYTHONPATH=. uv run python e2e/serve_fixtures.py /tmp/gw-tokens.json
```

Después, en otra terminal, corre el `McpAdsSource` del plugin contra `FixtureAdsSource`:

```bash
cd agent_gateway && PLUGIN_DIR=/Users/edgm/Documents/Projects/deepseekHarness/AcktosAgency/salesProductsBot/packages/dsh-hubara GATEWAY_URL=http://localhost:8765/mcp TOKEN=$(jq -r .admin /tmp/gw-tokens.json) node --experimental-transform-types --no-warnings e2e/plugin_parity.mts
```

Con `.admin`, todas las vistas dan `OK`. Con `.user`, `conversations` difiere solo en `contact`
(doble enmascarado; el arreglo es del plugin, ver PLAN-003 §4).

## Levantar en Docker contra el backend real

1. **Requisito (una vez, con OK del operador):**
   - `terraform apply` de `infra/terraform/platform` (crea resource servers, app clients y managed login);
   - `infra/scripts/cognito-managed-login-branding.sh`;
   - deploy del backend con `COGNITO_M2M_CLIENT_IDS`.
   - Orden completo en el ADR de auth.
2. **Configuración:**

   ```bash
   cp agent_gateway/.env.example agent_gateway/.env
   ```

   Completar `GATEWAY_ALLOWED_CLIENT_IDS` y `BACKEND_M2M_CLIENT_ID` con
   `terraform -chdir=infra/terraform/platform output agent_gateway`. El secreto se trae de SSM:

   ```bash
   aws ssm get-parameter --with-decryption --name /hubara/agent-gateway/<tenant>/BACKEND_M2M_CLIENT_SECRET --query Parameter.Value --output text
   ```

   Pegarlo en `BACKEND_M2M_CLIENT_SECRET` (el `.env` está en `.gitignore`).
3. **Arranque:**

   ```bash
   cd agent_gateway && docker compose up -d --build
   ```

   Verificar con `curl -s localhost:8765/healthz` y
   `curl -s localhost:8765/.well-known/oauth-protected-resource/mcp`.
4. **Plugin:** `source: mcp`, `mcpUrl: http://localhost:8765/mcp`, más el login OAuth de PLAN-003.

La imagen corre como usuario 10001 y trae `HEALTHCHECK`. El compose la levanta en loopback, con
filesystem de solo lectura, `cap_drop: ALL`, `no-new-privileges` y 256 MB de memoria. Toda la
configuración llega por env (`.env.example`), sin secretos en la imagen.

| Variable | Obligatoria | Qué es |
|---|---|---|
| `GATEWAY_RESOURCE_URL` | sí | URL canónica del recurso (= `aud` del token). https o http://localhost |
| `COGNITO_USER_POOL_ID` | sí | Pool (`COGNITO_REGION`, por defecto `us-east-1`) |
| `GATEWAY_ALLOWED_CLIENT_IDS` | sí | App client(s) del plugin (`dsh-hubara-<tenant>`) |
| `BACKEND_URL` | sí | `https://<domain>` del tenant (`infra/terraform/compute/tenants.auto.tfvars`) |
| `COGNITO_DOMAIN` o `BACKEND_M2M_TOKEN_URL` | sí | Token endpoint para el M2M |
| `BACKEND_M2M_CLIENT_ID` / `BACKEND_M2M_CLIENT_SECRET` | sí | M2M propio del gateway (secreto desde SSM) |
| `GATEWAY_ALLOWED_HOSTS` / `GATEWAY_ALLOWED_ORIGINS` | no | DNS rebinding (por defecto: el host del recurso, sin Origins) |
| `GATEWAY_REQUIRE_AUDIENCE` | no | `true` (exige `aud`) |
| `GATEWAY_RATE_LIMIT_PER_MINUTE`, `GATEWAY_MAX_BODY_BYTES`, `BACKEND_TIMEOUT_S`, `BACKEND_MAX_RESPONSE_BYTES` | no | Límites |

Si `HUBARA_SERVICE_TOKEN` está en el entorno, el gateway **se niega a arrancar**.
