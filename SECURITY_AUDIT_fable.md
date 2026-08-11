# Auditoría de seguridad + plan de mitigación — AgencyHubara

> Fecha: 2026-07-21 · Alcance: monorepo completo (backend `hubara_agency/`, frontend `frontend_dashboard/`, infra `infra/`, GraphAgents, CI/CD).
> Método: 5 auditorías paralelas por superficie (auth API · secretos · frontend · infra/CI · plugins+LLM). Cada hallazgo está anclado a `archivo:línea` leído directamente.

---

## TL;DR

El problema de fondo es **un patrón "fail-open" por configuración**: hay código de seguridad correcto, pero se **auto-desactiva en silencio** cuando faltan variables de entorno que el deploy **nunca provisiona**. Se repite en las dos puertas de entrada del sistema:

1. **La API del dashboard queda 100% sin autenticación** en producción — `require_auth` es un no-op si faltan las vars de Cognito, y la IaC nunca las inyecta. Toda la data de clientes (teléfonos, conversaciones, fotos de comprobantes de pago, órdenes) es accesible sin credenciales sobre HTTPS público.
2. **El webhook de WhatsApp no verifica la firma HMAC** si falta `WHATSAPP_APP_SECRET` — que tampoco está en el set de SSM. Cualquiera puede inyectar mensajes falsos de clientes.

Todo lo demás es secundario a esos dos. **Lo bueno:** no hay secretos hardcodeados en el repo, la higiene de SSM es ejemplar, no hay sinks de XSS, y las sesiones están atadas server-side (un prompt injection **no** puede exfiltrar data de otro cliente). El sistema está bien construido; el fallo está en el **cierre de la configuración de producción**, no en el diseño.

> ⚠️ **Verificar antes de asumir lo peor:** la conclusión de "API abierta" se apoya en que la IaC comprometida no provisiona `COGNITO_*` ni `WHATSAPP_APP_SECRET`. Si un operador los seteó a mano por SSM, la auth sí está activa. **Comprobalo en 30 segundos** (ver [§ Verificación](#verificación-inmediata)).

---

## Estado de remediación (commits en `claude/security-analysis-mitigation-1a5ead`)

| ID | Sev | Estado | Commit / nota |
|----|-----|--------|---------------|
| SEC-01 | 🔴 | ✅ Resuelto (código) — falta provisioning + deploy del operador | `0983fc4` |
| SEC-02 | 🔴 | ✅ Resuelto (código) — falta provisioning + deploy | `0983fc4` |
| SEC-12 | 🟡 | ✅ Resuelto | `f864ad0` |
| SEC-14 | 🟢 | ✅ Mecanismo listo — operador setea el origen en SSM | `2864ee6` |
| SEC-15 | 🟢 | ✅ Resuelto (verify-token) | `d04987b` |
| SEC-04 | 🟠 | ⚠️ Parcial — UIs admin cerradas; SSH sigue abierto (CI usa SSH) | `eaee5e4` |
| SEC-08 | 🟡 | ✅ Resuelto | `503ca22` |
| SEC-05 | 🟠 | ✅ Cubierto por SEC-01 (los endpoints ya exigen auth); falta rol-operador (→SEC-11) | — |
| SEC-06 | 🟠 | ✅ Resuelto — ticket SSE firmado (backend + frontend) | `acda8cb` |
| SEC-13 | 🟢 | ✅ Resuelto — rate limiting opt-in (sin dependencias) | `82c13e1` |
| SEC-10 | 🟡 | ⚠️ Parcial — security headers/CSP en CloudFront; refresh token→Keystore pendiente (Android) | `d29223a` |
| SEC-07 | 🟡 | ✅ Resuelto (consistencia interna, coupon-ready; comparación vs catálogo diferida) | `4102760` |
| SEC-11 | 🟡 | ✅ Base — grupos Cognito `admin`/`operarios` (mismos permisos por ahora; split de rutas después) | `66e1f04` |
| SEC-03 | 🟠 | ⏳ Diferido — split de rol OIDC + mover deploys al rol angosto (riesgo: rompe CI, no testeable acá; deploy coordinado) | — |
| SEC-09 | 🟡 | ⏳ Diferido — vault a EBS dedicado + prevent_destroy (migración de datos, riesgo; hoy hay backup DLM diario) | — |

## Tabla de hallazgos

| ID | Sev | Título | Superficie |
|----|-----|--------|-----------|
| **SEC-01** | 🔴 Crítico | API del dashboard sin auth (Cognito fail-open, IaC no provisiona las vars) | Backend / Infra |
| **SEC-02** | 🔴 Crítico | Webhook WhatsApp sin verificación HMAC cuando falta `WHATSAPP_APP_SECRET` | Backend |
| **SEC-03** | 🟠 Alto | Rol OIDC con admin total de AWS confiable desde cualquier push a `main` | CI/CD |
| **SEC-04** | 🟠 Alto | `ssh_ingress_cidrs` default `0.0.0.0/0` → SSH + UIs admin al mundo | Infra |
| **SEC-05** | 🟠 Alto | Endpoints que gastan dinero / mutan órdenes accesibles sin auth (consecuencia de SEC-01) | Backend |
| **SEC-06** | 🟠 Alto | Access token de Cognito viaja en el query string del SSE (queda en logs) | Frontend / Backend |
| **SEC-07** | 🟡 Medio | `register_order` confía en los precios que manda el LLM (sin re-check server-side) | Backend / LLM |
| **SEC-08** | 🟡 Medio | GitHub Actions de terceros pineadas a tags mutables (manejan la SSH key de prod) | CI/CD |
| **SEC-09** | 🟡 Medio | DR del vault: sin volumen EBS dedicado / `prevent_destroy`; GraphAgents y SigNoz sin backup | Infra |
| **SEC-10** | 🟡 Medio | Almacenamiento de tokens: refresh en `localStorage` (mobile), tokens en `sessionStorage` (web), sin CSP, Dockerfile corre dev server | Frontend |
| **SEC-11** | 🟡 Medio | Vulnerabilidades de dependencias frontend (1 high en vite, cadena OTel en el bundle) | Frontend |
| **SEC-12** | 🟡 Medio | Path traversal vía `from_number` del webhook (encadenable con SEC-02) | Backend |
| **SEC-13** | 🟢 Bajo | Sin rate limiting en ninguna ruta | Backend |
| **SEC-14** | 🟢 Bajo | CORS `allow_origins=["*"]` | Backend |
| **SEC-15** | 🟢 Bajo | Defaults débiles: `WHATSAPP_VERIFY_TOKEN`, `ALLOW_USER_PASSWORD_AUTH`, passwords de DB dev, IAM `iam:*` sobre `*` | Infra / Backend |
| **SEC-16** | ℹ️ Nota | Los plugins NO son una frontera de seguridad en runtime (relevante si se cargan `.acktospkg` de terceros) | Arquitectura |

---

## Hallazgos en detalle

### 🔴 SEC-01 — La API del dashboard queda sin autenticación en producción

- **Dónde:** `hubara_agency/src/platform/auth.py:90-96` (`require_auth` hace `if not _cognito_configured(): return`) · `hubara_agency/src/platform/config.py:84-85` (`COGNITO_*` default `""`) · `infra/compose/render-env-from-ssm.sh:47-63` y `infra/terraform/platform/variables.tf:62-85` (el set de SSM **no** incluye `COGNITO_USER_POOL_ID` ni `COGNITO_APP_CLIENT_ID`).
- **Qué pasa:** `require_auth` está montado fail-closed en **todas** las rutas (`main.py:152-158`), pero se convierte en no-op global porque las dos vars de Cognito nunca llegan al contenedor de la API. Además hay **"login theater"**: el frontend prod *sí* está cableado con Cognito (`frontend-deploy.yml:55-74`), así que el operador entra por login y **cree** que está protegido, mientras la API valida nada. Confirma el `infra/PENDING_IMPLEMENTATION.md:60` ("hoy las rutas NO exigen auth").
- **Exploit:** `curl https://api.<tenant>.<dominio>/api/dashboard/sessions` → lista de todas las sesiones de WhatsApp con teléfonos, tags y órdenes. Sin token. `GET /api/dashboard/media/{session_id}/{filename}` → **fotos de comprobantes de pago** de los clientes. `GET /api/dashboard/sessions/{id}` → transcripción completa + perfil del cliente.
- **Fix:** (a) Agregar `COGNITO_USER_POOL_ID` + `COGNITO_APP_CLIENT_ID` al bloque del `render-env-from-ssm.sh` (son ids públicos, salen de los outputs del módulo `auth` de Terraform). (b) **Invertir el switch a fail-closed**: en prod, si faltan las vars → boot falla o 500 en rutas protegidas, con un opt-out explícito `AUTH_DISABLED=1` solo para dev. Nunca degradar a "abierto" por omisión.
- **Esfuerzo:** Bajo (config + ~15 líneas). **Es la corrección #1.**

### 🔴 SEC-02 — Webhook de WhatsApp sin verificación HMAC

- **Dónde:** `hubara_agency/src/plugins/chats/api/sales.py:83-94` (`if WHATSAPP_APP_SECRET: ... else: logger.warning(...)` y procesa igual) · `config.py:61` (default `""`) · `variables.tf:62-85` (no está en el set de SSM).
- **Qué pasa:** La verificación de `X-Hub-Signature-256` solo corre si el secret es truthy; si falta, loguea un warning y procesa el body. El código de verificación en sí es correcto (`webhook_security.py:95`, `hmac.compare_digest`), pero el caller es opt-out.
- **Exploit:** `POST /api/webhook` con un body forjado (`entry[].changes[].value.messages[]`) → inyectar mensajes falsos de clientes en la sesión de una víctima (el `session_id` sale directo del `from` del payload, `ingest_inbound_message.py:126`), disparar workflows LLM fantasma (quema tokens) y corromper métricas de costo/atribución CTWA.
- **Fix:** Agregar `WHATSAPP_APP_SECRET` al set de SSM y hacer que el webhook **rechace** (no warnee) cuando falta en prod.
- **Esfuerzo:** Bajo.

### 🟠 SEC-03 — Rol OIDC con admin total de AWS confiable desde `main`

- **Dónde:** `infra/terraform/platform/modules/github-oidc/main.tf:57-60` (trust incluye `repo:...:ref:refs/heads/main`) y `:92-100` (política `s3:*, cloudfront:*, cognito-idp:*, ssm:*, iam:*, ec2:*, ... sobre *`) · `variables.tf:22` (`github_branches = ["main"]`).
- **Qué pasa:** El comentario del módulo dice "solo lo asume el environment production con required reviewers", pero solo `terraform-apply.yml:26` setea `environment: production`. `terraform-plan.yml`, `backend-deploy.yml:73` y `graphagents-deploy.yml` asumen el **mismo rol admin** en cada push a `main`, **sin gate de environment**.
- **Exploit:** Cualquier commit que aterrice en `main` (o cualquier step/action comprometido dentro de esos jobs) obtiene **admin total de la cuenta AWS**, incluido `iam:*`. Blast radius = takeover de la cuenta.
- **Fix:** Sacar los `ref:refs/heads/*` del trust del rol amplio (dejar solo `environment:production`); dar a `backend-deploy`/`graphagents-deploy` el `deploy_role` angosto. Quitar `iam:*`/`*` de `tf_perms`.
- **Esfuerzo:** Medio.

### 🟠 SEC-04 — `ssh_ingress_cidrs` abierto al mundo

- **Dónde:** `infra/terraform/compute/variables.tf:24-28` (default `["0.0.0.0/0"]`) · `infra/terraform/compute/tenants.auto.tfvars` **no** lo overridea.
- **Qué pasa:** Ese CIDR no solo abre SSH `:22` en las tres cajas (`app-instance/main.tf:49-55`), sino también la **UI de SigNoz `:8080`** (`observability-instance/main.tf:46-52`), el **Explorer de GraphAgents `:8900`** y la **API admin de AgentSpan `:6767`** (`graphagents-instance/main.tf:57-72`) — todos con auth débil o nula por diseño.
- **Exploit:** Dashboards internos y el runtime admin de AgentSpan expuestos a internet; SSH bajo fuerza bruta / 0-day.
- **Fix:** Setear `ssh_ingress_cidrs` al CIDR del VPN/IP admin en los tfvars (o `[]` y usar SSM Session Manager, que el código ya soporta). Nunca dejar puertos admin en el default del mundo.
- **Esfuerzo:** Bajo.

### 🟠 SEC-05 — Endpoints de dinero / mutación de órdenes sin auth (consecuencia de SEC-01)

Con SEC-01 activo, estos quedan accesibles sin credenciales:
- `PATCH /api/orders/{id}/confirm-payment` (`plugins/orders/api/__init__.py:438`) → marcar cualquier orden como pagada sin prueba → dispara fulfillment/envío sin pago.
- `POST /api/marketing/campaigns/{id}/send` (`plugins/marketing/api/__init__.py:238`) → envíos masivos de WhatsApp facturados (~12.500 micros/msg) → spam a costa del tenant + daño de calidad del número.
- `POST /api/ads/meta/campaigns/{id}/status` (`plugins/ads/api/meta_oauth.py:190-208`) → pausar/activar campañas Meta con el system-user token → DoS de ingresos o gasto no autorizado.
- `POST /api/dashboard/sessions/{id}/intervene` + `.../messages` (`plugins/chats/api/handoff.py:241,473`) → secuestrar una conversación viva y enviar mensajes al cliente real como "operador humano" → canal de ingeniería social / suplantación de marca.
- **Fix:** Se resuelve con SEC-01. Adicional: tratar `confirm-payment` y los `send/approve` como acciones privilegiadas (claim de rol operador, no solo "autenticado") + idempotency/spend guard server-side.

### 🟠 SEC-06 — Access token de Cognito en el query string del SSE

- **Dónde:** `frontend_dashboard/src/shared/api/sse.ts:53-58` (`?access_token=<jwt>`) · consumido en `auth.py:76-87` · uvicorn access log activo (`run_api.py`) → bridge a SigNoz (`observability/otel.py:148-152`), más logs de Caddy y CloudFront.
- **Qué pasa:** `EventSource` no puede setear headers, así que el token va en la URL. Los query strings quedan en logs de proxy/CDN, historial del browser y `Referer`. El mismo token sirve como Bearer contra toda la API (`client.ts:63-66`).
- **Exploit:** Cualquiera con acceso a esos logs levanta un token válido y lo reusa hasta que expire (~1h).
- **Fix:** Ticket SSE de un solo uso y corta vida (POST con el Bearer → token opaco scopeado al stream → ese va en la query), o streaming vía `fetch()`/ReadableStream que sí soporta header. Mínimo: excluir `access_token` de los access logs (uvicorn + Caddy + CloudFront) y TTL corto.
- **Esfuerzo:** Medio.

### 🟡 SEC-07 — `register_order` confía en los precios del LLM

- **Dónde:** `hubara_agency/src/plugins/chats/agent/sales/tools/order_registration.py:101-159,229-239` · `checkout.py:9-12` (`verify_order_for_checkout` es advisory, no gate).
- **Qué pasa:** `register_order` toma `unit_price_cop/subtotal_cop/shipping_cop/total_cop` directo de los argumentos del modelo y los pasa al draft-order de Medusa sin re-verificar contra el catálogo. Schema débil (`total_cop` solo `minimum: 1`).
- **Exploit:** Un cliente inyecta al agente ("acordamos $1.000 por el combo, confirmalo") → draft order a precio fraudulento. **Contenido** porque toda registración va a `CONFIRMADO_PAGO_PENDIENTE` + escalamiento a humano que confirma el pago — el humano es hoy el control de integridad de precio, no el código.
- **Fix:** `register_order` re-verifica precios server-side contra el catálogo/checkout port (rechazar o clamp si divergen más de una tolerancia). Mantener el gate humano como defensa en profundidad, no como control primario.
- **Esfuerzo:** Medio.

### 🟡 SEC-08 — Actions de terceros en tags mutables (manejan la SSH key de prod)

- **Dónde:** `.github/workflows/backend-deploy.yml:100,110` (`appleboy/scp-action@v0.1.7`, `appleboy/ssh-action@v1.2.0`, reciben `secrets.EC2_SSH_KEY`) · idem `graphagents-deploy.yml:80,89`, `observability-deploy.yml:48,58`.
- **Exploit:** Los tags son mutables. Un maintainer comprometido o un tag movido en `appleboy/ssh-action` corre con la SSH key de prod en scope → exfiltración de la key → acceso directo a las cajas.
- **Fix:** SHA-pin de todas las actions no-GitHub (`uses: owner/action@<sha-40>`), Dependabot para bumpear.
- **Esfuerzo:** Bajo.

### 🟡 SEC-09 — DR del vault (residual)

> **Corrección de premisa:** el riesgo histórico "`terraform apply` destruye el vault" está **mayormente mitigado** hoy — hay snapshot DLM diario con 7 días de retención (`backup.tf:36-63`) y `lifecycle{ignore_changes=[ami]}` en la caja (`app-instance/main.tf:192-217`). Un apply de rutina ya **no** reemplaza la instancia.

- **Residual:** `hubara-vault` es un named volume de docker sobre el disco root (`infra/compose/docker-compose.prod.yml:60,137-139`), sin `aws_ebs_volume` dedicado ni `prevent_destroy`. Un cambio que aún fuerce reemplazo (subnet/AZ, o un `-replace` explícito) destruye el volumen vivo; la recuperación depende del último snapshot diario (hasta 24h de pérdida) vía restore **manual**. `postgres_data` de GraphAgents y la data de SigNoz **no** tienen tag `Backup` — sin respaldo.
- **Fix:** Mover el vault a un `aws_ebs_volume` dedicado con `prevent_destroy=true` + `aws_volume_attachment`, desacoplado del reemplazo de instancia. Scriptear/verificar el restore. Tag de backup a GraphAgents/SigNoz. Considerar RPO < 24h para estado conversacional.
- **Esfuerzo:** Medio.

### 🟡 SEC-10 — Almacenamiento de tokens + Dockerfile del frontend

- **Dónde:** `frontend_dashboard/src/shared/config/session-store.ts:40` (refresh token en `localStorage`, mobile) · `src/app/providers/index.tsx:27-37` (web sin `userStore` → oidc-client-ts default `sessionStorage`) · `frontend_dashboard/index.html` (sin CSP) · `frontend_dashboard/Dockerfile` (corre `npm run dev`, no build estático).
- **Qué pasa:** El refresh token (larga vida) en `localStorage` es exfiltrable por cualquier XSS en el WebView. Los tokens web en `sessionStorage` son XSS-readable. La "CSP estricta" que el código cita como mitigación **solo existe** en el Tauri (`src-tauri/tauri.conf.json:26`), no en el web. El Dockerfile del frontend sirve el **dev server** de Vite (source maps, HMR, cero headers de seguridad).
- **Mitigante real hoy:** no hay sinks de HTML injection (ver positivos), así que el riesgo de XSS es bajo — esto es defensa en profundidad.
- **Fix:** Refresh token al Android Keystore (plugin nativo Tauri). Servir el web como `vite build` estático detrás de nginx/CloudFront con CSP estricta (`default-src 'self'`, sin `unsafe-inline` en scripts), HSTS, `X-Content-Type-Options: nosniff`. Arreglar el Dockerfile para build+serve estático. Estrechar el `img-src http: https:` del Tauri.
- **Esfuerzo:** Medio.

### 🟡 SEC-11 — Vulnerabilidades de dependencias frontend

- **Dónde:** `frontend_dashboard/package.json`. `npm audit`: 1 **high** (`vite 8.0.0–8.0.15`, bypass de `server.fs.deny` — solo dev-server), moderates en `esbuild` (dev-server) y en la cadena `@opentelemetry/*` que **sí** viaja en el bundle de producción.
- **Fix:** `npm audit fix`; bumpear `vite`/`esbuild`; actualizar el set `@opentelemetry/*`. Confirmar que prod no corre el dev server (liga con SEC-10).
- **Esfuerzo:** Bajo.

### 🟡 SEC-12 — Path traversal vía `from_number` del webhook

- **Dónde:** `parsers.py:126-127` (`from_number` validado solo como `isinstance(str)`) → `state.py:78-79` (`self._vault_dir / session_id / "metadata.json"`) y `media/store.py:95` sin check de `..`. El guard correcto (`is_safe_segment`, `store.py:89`) existe pero se aplica solo en los endpoints del dashboard, no en el path derivado del webhook.
- **Exploit:** Solo alcanzable con SEC-02 activo (con HMAC, `from` lo controla Meta). Webhook forjado con `from = "../../../../tmp/evil"` → escritura de metadata/imagen fuera del vault.
- **Fix:** Validar `from_number` como teléfono (`^\d{6,15}$`) en el parse, y/o llamar `is_safe_segment(session_id)` dentro de `_path_for` y `_media_dir`.
- **Esfuerzo:** Bajo.

### 🟢 SEC-13 a SEC-15 — Hardening (bajo)

- **SEC-13** Sin rate limiting en ninguna ruta (`main.py:56`, solo CORS). Agregar `slowapi` o límites en Caddy, sobre todo en `send`/`approve`/`webhook`.
- **SEC-14** CORS `allow_origins=["*"]` (`main.py:56-62`). Con `allow_credentials=False` es el par seguro, pero con auth off amplifica el drive-by. Restringir al origin del CloudFront del tenant.
- **SEC-15** Defaults débiles: `WHATSAPP_VERIFY_TOKEN="my_secret_verify_token"` (`config.py:49`), `ALLOW_USER_PASSWORD_AUTH` en Cognito (`auth/main.tf:59`), passwords `temporal`/`changeme` en compose dev, IAM `iam:*` sobre `resources=["*"]`. Ninguno crítico aislado; hacer fail-closed los que aplican y estrechar el IAM.

### ℹ️ SEC-16 — Los plugins no son una frontera de seguridad en runtime

El aislamiento de plugins se enforcea **solo en lint/CI** (import-linter, ratchets P-28/P-29). No hay sandbox en runtime: todos los plugins comparten proceso y vault keyeado por `session_id`. Es aceptable bajo el modelo "los plugins son código propio revisado", pero **deja de serlo si alguna vez se cargan `.acktospkg` de terceros**. Tenerlo presente para el flujo de instalación de paquetes.

---

## Lo que ya está bien (verificado, no asumido)

- **Sin secretos hardcodeados en el repo.** Barridos de `EAA…`, `sk-…`, `AKIA…`, `ghp_…`, claves privadas → nada real. Todo config usa `${VAR}` o `os.environ`. Los únicos ids reales de Meta están en un `.env.example` (ids, no credenciales) → bajo.
- **Higiene de SSM ejemplar:** parámetros como `SecureString` con `ignore_changes=[value]`, render con `umask 077`, sin secretos en `user_data`/cloud-init.
- **Sin sinks de XSS:** cero `dangerouslySetInnerHTML`/`innerHTML`/`eval`; `react-markdown` sin `rehype-raw` (HTML crudo del LLM no se interpreta); texto de WhatsApp renderizado como child de React (auto-escapado). **No agregar `rehype-raw`.**
- **Sesiones atadas server-side:** cada tool con efecto keyea por `ctx.session_key` (= `wa_<from>`), nunca por argumento del LLM → **un prompt injection no puede leer/mutar data de otro cliente**. La exfiltración cross-cliente no es alcanzable por la capa de tools.
- **Sin `eval`/`exec`/`pickle`/`shell=True` sobre input externo;** todo YAML con `yaml.safe_load`; `importlib` solo sobre paths de manifests del repo.
- **Infra:** estado remoto encriptado + lockeado con DynamoDB; IMDSv2 requerido + EBS encriptado; S3 privado + OAC; CloudFront `redirect-to-https` TLSv1.2_2021; sin `pull_request_target`, sin `privileged`/`docker.sock`/host-network; auth a AWS por OIDC (sin keys estáticas).

---

## Plan de mitigación (por fases)

### Fase 0 — Contención inmediata (hoy / esta semana)
Objetivo: cerrar la puerta abierta. Todo bajo esfuerzo, alto impacto.

1. **SEC-01** — Provisionar `COGNITO_USER_POOL_ID` + `COGNITO_APP_CLIENT_ID` en el `.env` de la API (render-from-ssm) y **redeploy backend**. Verificar con `curl` (abajo).
2. **SEC-02** — Provisionar `WHATSAPP_APP_SECRET` en SSM + redeploy. Confirmar que el webhook rechaza firmas inválidas.
3. **SEC-04** — Setear `ssh_ingress_cidrs` al CIDR admin en `tenants.auto.tfvars`; `terraform apply`.
4. **SEC-01/SEC-02 durabilidad** — Invertir ambos switches a **fail-closed** (boot falla en prod si faltan las vars; opt-out explícito `AUTH_DISABLED=1`/`HUBARA_ENV=dev` para dev). Un test de arranque que lo asegure.

### Fase 1 — Alto (1–2 semanas)
5. **SEC-03** — Sacar los `ref:refs/heads/main` del trust del rol admin; deploys con rol angosto.
6. **SEC-05** — Rol "operador" para `confirm-payment` y los `send/approve` + idempotency/spend guard.
7. **SEC-06** — Ticket SSE de un solo uso; excluir `access_token` de los access logs (uvicorn/Caddy/CloudFront).
8. **SEC-08** — SHA-pin de las Actions de terceros que tocan la SSH key; Dependabot.

### Fase 2 — Medio (este mes)
9. **SEC-07** — Re-verificación de precios server-side en `register_order`.
10. **SEC-09** — Vault en EBS dedicado con `prevent_destroy`; backup a GraphAgents/SigNoz; verificar el restore.
11. **SEC-10** — Frontend prod como build estático con CSP/HSTS; refresh token al Keystore; arreglar el Dockerfile.
12. **SEC-11** — `npm audit fix` + bumps.
13. **SEC-12** — Validar `from_number`; aplicar `is_safe_segment` en los stores.

### Fase 3 — Hardening (backlog)
14. **SEC-13** rate limiting · **SEC-14** CORS restringido · **SEC-15** defaults fail-closed + IAM least-privilege · **SEC-16** política si se habilitan plugins de terceros.

---

## Verificación inmediata

Antes de tratar SEC-01/SEC-02 como incidente, confirmá el estado vivo:

```bash
# ¿La API responde 401 (auth ON) o 200 (auth OFF)?
curl -s -o /dev/null -w "%{http_code}\n" https://api.<tenant>.<dominio>/api/dashboard/sessions

# ¿Están los parámetros en SSM?  (en la caja o con perfil AWS)
aws ssm get-parameters-by-path --path /hubara/<tenant> --query "Parameters[].Name" --output text \
  | tr '\t' '\n' | grep -Ei 'COGNITO|APP_SECRET'
```

- `200` sin token en la primera, o los nombres ausentes en la segunda → **SEC-01/SEC-02 confirmados como incidente**: aplicar Fase 0 ya.
- `401` y los nombres presentes → auth ya activa; igual aplicar el fail-closed durable de Fase 0 punto 4.

---

## Premortem de la solución SEC-01/SEC-02 + auditoría del desarrollo

> "Es 3 semanas después y el fix de auth causó un incidente. ¿Qué pasó?"
> Premortem forward-looking + auditoría adversarial independiente del diff. Los
> 3 primeros hallazgos fueron **defectos introducidos por el propio fix** — ya
> corregidos (con TDD rojo→verde) en la misma branch. Documentados acá porque son
> lecciones reutilizables ("un fail-closed mal secuenciado es un outage; un
> placeholder conocido es una credencial").

### PM-1 🔴 — Outage autoinfligido en el merge (deploy-order lockout)
**Modo de fallo:** `backend-deploy` dispara con push a `main` tocando
`hubara_agency/**` **e** `infra/compose/**` → rinde `HUBARA_ENV=production` en la
caja. Pero `terraform-apply` (que crea `COGNITO_*` en SSM) es **manual**. Secuencia
al mergear: deploy auto → `.env` con producción armada → SSM sin `COGNITO_*` →
**API entera 503 + webhook 403**, sin ninguna guarda (solo prosa en el runbook).
**Fix aplicado:** preflight en `render-env-from-ssm.sh` (a.1) que **aborta el
deploy** (exit 1, contenedor viejo sigue vivo) si los 4 params de auth faltan o
son placeholder — decopla el arme-de-producción del `terraform apply` manual.
Verificado con harness bash (aborta en faltante/placeholder, pasa con reales).

### PM-2 🔴 — El placeholder de Terraform como credencial = bypass de Cognito
**Modo de fallo:** Terraform crea `HUBARA_SERVICE_TOKEN` con el valor
**conocido en el repo** `PLACEHOLDER_set_out_of_band`. Como el chequeo de
service-token corría *antes* que Cognito, si el operador aplicaba terraform pero
no sobrescribía el placeholder, `Bearer PLACEHOLDER_set_out_of_band` **bypaseaba
toda la auth** (y el sistema parecía sano). Peor que el hueco original.
**Fix aplicado:** `config.is_placeholder()` — el placeholder (y el vacío) cuentan
como AUSENTE en `_cognito_configured()`, en el service-token (`auth.py`) y en el
webhook (`sales.py`). Test rojo→verde: `test_placeholder_service_token_does_not_bypass`.

### PM-3 🟠 — Placeholder truthy del webhook → 403 en todo webhook real
**Modo de fallo:** `WHATSAPP_APP_SECRET=PLACEHOLDER...` es truthy → la rama
`if app_secret:` verificaba el HMAC de Meta **contra el placeholder** → 403 en
todo webhook real (reception muerta), y el `elif is_production()` quedaba
inalcanzable. **Fix aplicado:** `and not cfg.is_placeholder(app_secret)` → el
placeholder va al fail-closed intencional (403 con `logger.error` visible en
prod; procesa con warning en dev). Test: `test_placeholder_app_secret_treated_as_unconfigured_in_dev`.

### PM-4 🟡 — Regresión de callers internos (workers → API)
`order_sentinel` (20:00) y `post_sale_return` (21:00) mandan el
`HUBARA_SERVICE_TOKEN` solo si está seteado; con Cognito on y token vacío →
401 silencioso. **Mitigado por PM-1**: el preflight exige `HUBARA_SERVICE_TOKEN`
real antes de deployar, así que nunca queda vacío en prod. (Los callers que usan
`castkit.forward` propagan el `Authorization` entrante — no afectados.)

### PM-5 🟢 — Observabilidad
El 503 de auth-misconfig no logueaba nada. **Fix:** `logger.error("auth_misconfigured_in_prod")`
antes del 503; docstring stale en `main.py` corregido.
**Residual aceptado:** `HUBARA_ENV=production` es hardcodeado en el render (un
box no-prod que use el mismo script quedaría "producción"). Trivial hoy
(single-tenant) — el preflight igual lo hace fail-safe (abortaría sin params).

### PM-6 🟡 — Secreto-en-URL: el service token se aceptaba por query string
**Modo de fallo:** `_extract_token` acepta `?access_token=` (necesario para el
JWT de Cognito del SSE), pero el chequeo del service-token usaba esa misma
fuente → un `HUBARA_SERVICE_TOKEN` (secreto M2M de larga vida) podía viajar por
la URL y quedar en access logs / proxies / referrer. Los callers reales
(`order_sentinel`, `post_sale_return`) usan header, así que era latente.
**Fix aplicado:** `_extract_header_token()` — el service token se acepta SOLO por
`Authorization: Bearer`; el `?access_token=` queda restringido al JWT de Cognito
(corta vida). Test: `test_service_token_via_query_string_is_rejected`.

### Conformidad con protocolos de seguridad (post-fix)
✅ Secure-by-default / fail-closed · ✅ comparación en tiempo constante
(`secrets.compare_digest` service token, `hmac.compare_digest` webhook) · ✅ JWT
fuerte (RS256 + issuer + client_id + token_use + exp) · ✅ HMAC del webhook sobre
raw body · ✅ sin secretos en logs (el 503 loguea `path`, no query) · ✅ sin
secretos en URL (PM-6) · ✅ placeholder ≠ credencial (PM-2/3).
**Fuera del alcance de este fix (findings separados, no regresiones):** authZ /
IDOR intra-tenant (SEC-11 — este fix es authN, no valida ownership de objetos);
el GET `hub.verify_token` usa `==` y default débil (SEC-15).

### Auditoría del desarrollo (gate-reviewer independiente) — veredicto
`lint-imports` 6/0 · `ruff F821` limpio · `pytest -m architecture` 267 passed
(ratchet **P-28 intacto**: `import src.platform.config as cfg` mapea a la arista
congelada) · auth+webhook+platform 735 passed · SDK `cli check` OK (10 plugins C2).
Los tests nuevos afirman **status codes reales** (503/403) y fallan sin el fix —
ningún rojo por colección. Los 5 tests del service-token siguen verdes tras la
reestructuración. Sin regresiones.
