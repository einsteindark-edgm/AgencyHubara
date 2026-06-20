# Preparación de implementación — los 3 pendientes (Temporal · JWT · Dominios)

Spec para el trabajo de **código** que falta para que la infra quede 100% funcional.
NO es el runbook operativo (eso es [`DEPLOY_RUNBOOK.md`](DEPLOY_RUNBOOK.md) §Pendientes);
acá está **qué archivo se toca, qué cambia, qué env, y cómo se verifica**. Cada
sección es un mini-tech-refinement listo para implementar (TDD: test rojo primero).

Estado a hoy: la infra (Terraform/SSM/Cognito/EC2) ya **provee** lo que estos
cambios necesitan; falta el lado código.

---

## §1 — Temporal: migrar de mTLS a API key (Task #1)

**Por qué:** `INFRASTRUCTURE.md §6` decidió API key (sin certs que rotar). La prod
compose y SSM ya pasan `TEMPORAL_ADDRESS/NAMESPACE/API_KEY`, pero el código todavía
conecta por mTLS → esos 3 env no tienen efecto.

**Estado actual (verificado):**
- `src/platform/config.py:9-12` → `TEMPORAL_URL`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TLS_CERT_PATH`, `TEMPORAL_TLS_KEY_PATH`.
- `src/platform/temporal/client.py:get_temporal_client()` → arma `TLSConfig(client_cert, client_private_key)` y hace `Client.connect(TEMPORAL_URL, namespace=..., tls=tls_config, ...)`.

**⚠️ Mismatch de nombres a resolver:** el código lee `TEMPORAL_URL`, pero el SSM
param + el doc §6 usan `TEMPORAL_ADDRESS`. La implementación debe leer
`TEMPORAL_ADDRESS` con fallback a `TEMPORAL_URL` (local).

**Cambios:**
1. `config.py` — agregar:
   ```python
   TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", os.getenv("TEMPORAL_URL", "localhost:7233"))
   TEMPORAL_API_KEY = os.getenv("TEMPORAL_API_KEY", "")
   ```
2. `temporal/client.py` — preferir API key cuando está, mantener mTLS/insecure como fallback:
   ```python
   if TEMPORAL_API_KEY:
       # Temporal Cloud con API key: tls=True (no un TLSConfig), namespace = "<ns>.<account_id>".
       return await Client.connect(
           TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE,
           api_key=TEMPORAL_API_KEY, tls=True,
           interceptors=[TracingInterceptor()],
       )
   # ...el bloque mTLS/insecure actual queda como fallback (dev local).
   ```

**Env (prod, desde SSM `/hubara/<tenant>/`):** `TEMPORAL_ADDRESS` (`<region>.aws.api.temporal.io:7233`),
`TEMPORAL_NAMESPACE` (`<ns>.<account_id>`), `TEMPORAL_API_KEY`.

**Gotchas:** (a) el namespace para API key es `namespace.account_id`, NO solo el nombre.
(b) `tls=True` es un bool acá, no el objeto `TLSConfig`. (c) si la API key vence, el
SDK permite refrescarla; para v1 una estática alcanza.

**Acceptance / test (rojo primero):** con `TEMPORAL_API_KEY` seteado, `get_temporal_client()`
llama `Client.connect` con `api_key=...` y `tls=True` (mockear `Client.connect`).
Con la key vacía, conserva el comportamiento actual (local sigue andando).

---

## §2 — JWT: login del dashboard + validación en FastAPI (Task #2)

**Por qué:** hoy las rutas NO exigen auth. Cognito ya existe (pool + app client por
tenant); falta consumirlo.

**Estado actual (verificado):**
- `src/main.py:48` crea el `app`; `:54` CORS con `allow_origins=["*"]`, `allow_credentials=False`; los routers de plugins se montan en `_register_router` (`include_router`, `:142`).
- Front: `src/shared/config/env.ts` solo tiene `VITE_API_URL` + `VITE_OTEL_EXPORTER_URL` — **cero Cognito**.
- Terraform ya expone los ids: `terraform output build_config` → `user_pool_id`, `app_client_id`, `region`, `cognito_domain` por tenant.

**Diseño backend (dependencia de auth, no middleware global ciego):**
- Validar el JWT de Cognito contra el **JWKS** del pool:
  `https://cognito-idp.<region>.amazonaws.com/<pool_id>/.well-known/jwks.json`
  (chequear firma + `iss` + `client_id`/`aud` + `exp`). Cachear el JWKS.
- Lib sugerida: `pyjwt[crypto]` (o `python-jose[cryptography]`).
- **NUANCE CRÍTICO — los webhooks NO llevan JWT de Cognito:** `POST /api/chats/inbound`
  (Meta) y los webhooks de Medusa los llama un tercero, no el dashboard. Tienen su
  propia auth (Meta `X-Hub-Signature-256`, Medusa shared secret). Si ponés una
  dependency global, **rompés los webhooks**.
  → Aplicá el JWT **solo a las rutas del dashboard**. Opciones:
    - (a) `dependencies=[Depends(require_auth)]` por-router en `_register_router`,
      distinguiendo routers "dashboard" vs "webhook" (ej. un flag en el manifest).
    - (b) Dependency global + **allowlist explícita** de paths públicos (los webhooks).
  Recomendado: (a) explícito, o (b) con la lista de webhooks bien acotada y testeada.
- **CORS:** cerrar `allow_origins` de `["*"]` a los orígenes del dashboard (dominio
  CloudFront del tenant).

**Diseño frontend (FSD):**
- `react-oidc-context` + `oidc-client-ts`. Provider en `app/providers/` (composition root).
- Nuevos env: `VITE_COGNITO_AUTHORITY` (= issuer del pool), `VITE_COGNITO_CLIENT_ID`,
  `VITE_COGNITO_REDIRECT_URI`. Vienen de `build_config` (cablear en `frontend-deploy.yml`).
- Flujo Authorization Code + PKCE → al volver, guardar el token; el cliente de
  `shared/api` adjunta `Authorization: Bearer <access_token>` en cada fetch.

**Env nuevos:**
- Backend (SSM String, no secreto): `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `AWS_REGION` (o `COGNITO_ISSUER`).
- Front (build): `VITE_COGNITO_AUTHORITY`, `VITE_COGNITO_CLIENT_ID`, `VITE_COGNITO_REDIRECT_URI`.

**Acceptance / tests:**
- Backend: request al dashboard SIN token válido → `401`; con token válido → pasa;
  token expirado/firma inválida → `401`. **Webhook (`/api/chats/inbound`) sin token → sigue funcionando.**
- Front: sin sesión redirige al hosted UI de Cognito; vuelve con token; las llamadas
  a la API llevan el Bearer.

**Infra ya lista:** los callback/logout URLs de Cognito (`auth` module) hoy apuntan a
placeholders + `localhost:5174`; actualizalos al dominio real del dashboard (§3).

---

## §3 — Dominios propios + HTTPS (Task #3)

**Por qué:** por default CloudFront sirve por `*.cloudfront.net` y Caddy saca TLS del
dominio que le pasa `CADDY_DOMAIN`. Para dominios propios hay que cablear DNS + ACM.

**Es casi todo config (poco/nada de código). Pasos:**
1. **API (Caddy, ya funciona con dominio):** registro DNS `A` `api.<tenant>...` → la
   **EIP** de la caja (`terraform output app_hosts`). `CADDY_DOMAIN` (en `box.env`,
   viene del tfvar `domain` del compute) ya hace que Caddy emita el cert. Sin cambio de código.
2. **Dashboard (CloudFront + ACM):**
   - Pedí un cert **ACM en us-east-1** (obligatorio para CloudFront) para `dashboard.<tenant>...` y validalo por DNS.
   - Seteá en `terraform/platform/tenants.auto.tfvars`: `acm_certificate_arn = "<arn>"` y `domain_aliases = ["dashboard.<tenant>..."]` (el módulo `frontend` ya los soporta). `terraform apply`.
   - DNS: `CNAME` `dashboard.<tenant>...` → el dominio de CloudFront (`terraform output frontend`).
3. **Cognito callbacks:** actualizá `callback_urls`/`logout_urls` del tenant (en
   `platform/tenants.auto.tfvars`) de los placeholders al dominio real → `apply`.
4. **Front build:** `VITE_API_URL` ya apunta al dominio de la API (en la config del
   tenant); `VITE_COGNITO_REDIRECT_URI` (§2) debe usar el dominio real del dashboard.

**Acceptance:** `https://dashboard.<tenant>...` sirve la SPA con cert válido; el login
de Cognito redirige bien al dominio real; `https://api.<tenant>...` responde con TLS.

---

## Orden sugerido de implementación

1. **§1 Temporal** — desbloquea que los workers hablen con Temporal Cloud (sin esto no hay prod real). Chico y aislado.
2. **§2 JWT** — el más grande (back + front). Hacelo con los webhooks bien excluidos.
3. **§3 Dominios** — último; es pulido (config + DNS) una vez que back/front andan.

Cada uno es una HU para el pipeline (`archon workflow run hu-hubara-pipeline ...`) o
un cambio directo con el harness `hubara-dev` (TDD rojo→verde).
