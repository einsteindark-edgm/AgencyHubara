# {{repo_name}} — pasos post-forge ({{company}})

> Generado por forge desde el motor hubara (`{{engine_sha}}`). Este runbook
> reemplaza a los docs de infra del proyecto madre (describían la infra viva
> de Hubara y no aplican acá).
>
> **Orquestador**: estos pasos también existen como STEPS con estado — desde
> el repo madre: `python3 forge/migrate.py status {{slug}} --dest <esta carpeta>`
> (o Acktos Studio → Forge Console → «Migración paso a paso»). Los steps AUTO
> (Supabase / Railway-Medusa / seed / Temporal) hablan solo con APIs de
> terceros; los GUIADOS imprimen los comandos AWS/terraform exactos apuntando a
> ESTE clon — el orquestador jamás ejecuta comandos AWS, así que no puede tocar
> hubara.

## F0 — El concepto de la tienda (ya viene del bundle del cliente)

Lo que forge instaló desde `forge/clients/{{slug}}/`:

- [ ] Voces de los agentes: `hubara_agency/src/plugins/chats/agent/{sales,remarketing}/workspace/`,
      `hubara_agency/src/plugins/mba/agents/sales/` (MBA) y
      `hubara_agency/src/plugins/eta/agent/eta/workspace/` (avisos de pedidos).
- [ ] Dominio de la tienda para el motor de decisiones:
      `hubara_agency/src/plugins/chats/shared/decisions/bundles/{ventas,{{store_bundle}}}/domain.yaml`.
      El clon corre `{{store_bundle}}` (la misma inteligencia que la tienda madre).
      Certificar: `cd hubara_agency && uv run python -m src.sdk.cli decisions check`

- [ ] Política comercial (de `client.yaml → commerce`): tarifas de envío y zona local,
      mínimo de contra entrega, llave Nequi/Bre-B, recargos del link de pago, prefijo de
      SKU, dominio y colecciones del catálogo. Viven en
      `infra/terraform/platform/tenants.auto.tfvars → store` (→ SSM → `.env`, módulo
      `store-config`); cambiarlas = PR + `terraform apply` de platform + Backend deploy.
      Sin `payment_nequi_number` el bot no ofrece pago anticipado.

Límites conocidos del motor (asume una tienda en Colombia, COP, con variantes tipo
Hubara) — revisar antes del go-live si la tienda no encaja:

- [ ] Variantes por **aroma/color** (tools de ventas, `mobile_rules.py`, app y dashboard).
- [ ] La capacidad **`portavelas`** del paquete (quitarla en una versión nueva del paquete
      si la tienda no tiene accesorio equivalente) y la pregunta de **`zona_de_envio`**,
      que habla de Bogotá (la zona local del código sí es la de `commerce`).
- [ ] El mapa de categorías de Google (`platform/meta_catalog/mapper.py`) y los emojis de
      variantes (`platform/whatsapp/variant_emoji.py`): una categoría que no conocen sale sin
      categoría de Google (Meta la acepta) y una variante, con el emoji genérico.

## F2 — Bootstrap AWS (una vez, local con creds admin)

> **Cuenta compartida con el proyecto madre.** Las cajas de {{company}} solo leen
> sus parámetros SSM y solo le mandan comandos a su caja GraphAgents, y el rol de
> solo lectura del CI solo lee sus árboles (`{{ssm_prefix}}/*`, `/{{slug}}-graphagents/*`,
> `/{{slug}}-lab/*`). El rol de ESCRITURA de Terraform (`{{prefix}}-gha-terraform`,
> environment `production` con reviewers) sigue siendo amplio en la cuenta: para
> un aislamiento total, una cuenta AWS propia por cliente.

- [ ] `python3 infra/scripts/aws_bootstrap.py state` → bucket `{{prefix}}-tfstate` + tabla `{{prefix}}-tflock`
- [ ] `ssh-keygen -t ed25519 -f ~/.ssh/{{slug}}_ops -C "{{slug}}-ops"` → pública a `infra/terraform/compute/tenants.auto.tfvars`
- [ ] Environment `production` en GitHub con required reviewers

## F3 — Platform + secretos

- [ ] `cd infra/terraform/platform && cp envs/real.s3.tfbackend.example envs/real.s3.tfbackend`
      → `terraform init -backend-config=envs/real.s3.tfbackend && terraform apply`
      (el OIDC provider ya existe en la cuenta: `project.auto.tfvars` trae
      `create_github_oidc_provider = false`) → S3/CloudFront/Cognito/SSM
      placeholders `{{ssm_prefix}}/{{slug}}/*`
- [ ] GitHub del repo (DESPUÉS del apply: lee sus outputs):
      `python3 infra/scripts/aws_bootstrap.py github --repo {{repo}} --platform-dir infra/terraform/platform --ssh-key-file ~/.ssh/{{slug}}_ops`
      (vars AWS_* + TF_STATE_* y el secret `EC2_SSH_KEY`). Opcional: repo var `ALARM_EMAIL`.
- [ ] Usuario operador Cognito (`admin-create-user`)
- [ ] Secretos del tenant (la plantilla trae TODAS las llaves con su explicación; git ignora la copia):
      ```
      cp infra/scripts/secrets.example.env infra/scripts/secrets.{{slug}}.env
      python3 infra/scripts/aws_bootstrap.py secrets --tenant {{slug}} --file infra/scripts/secrets.{{slug}}.env
      python3 infra/scripts/aws_bootstrap.py verify --tenant {{slug}}
      ```
      Sin estos el deploy ABORTA (render-env-from-ssm.sh): `WHATSAPP_APP_SECRET`,
      `HUBARA_SERVICE_TOKEN` (`openssl rand -hex 32`) y `COGNITO_*` (los crea el apply).
      Keys NUEVAS de este cliente: DEEPSEEK_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY (Jev),
      GHCR_PULL_TOKEN (PAT fine-grained solo al package `{{image}}`), WHATSAPP_VERIFY_TOKEN real.
- [ ] Política comercial de {{company}}: ya está en `tenants.auto.tfvars → store`
      (forge la escribió desde `client.yaml → commerce`, incluida la llave Nequi
      `store = { payment_nequi_number = "…" }`); revisarla y aplicarla con el apply de arriba.

## F4 — Medusa propio (Railway) — steps S2–S4

- [ ] Proyecto Railway nuevo (Medusa v2 + Postgres Supabase) · `medusa db:migrate` · admin + token
- [ ] Seed: región {{currency}}/{{country}}, sales channel, shipping options → anotar los IDs
- [ ] Catálogo de {{company}} · SSM: MEDUSA_BASE_URL + MEDUSA_ADMIN_TOKEN

## F5 — Meta / WhatsApp (long pole — arrancar YA)

- [ ] Completar `infra/whatsapp-provisioning/tenants/{{slug}}.env` (desde el .example)
- [ ] `python3 whatsapp_provision.py discover|plan|apply|templates|flows|capi` → número, catálogo, templates, flow shipping (flow_id NUEVO), dataset CAPI
- [ ] `python3 whatsapp_provision.py ads-token` → seed `{{ssm_prefix}}/{{slug}}/meta/oauth` (sin esto el dashboard dice "Meta no conectado")
- [ ] SSM: WHATSAPP_*, META_CATALOG_ID, META_SYSTEM_USER_TOKEN, META_CAPI_DATASET_ID, META_FLOW_ID_SHIPPING, META_APP_*

## F5b — GraphAgents del cliente (viaja en el clon, identidad propia)

- [ ] La caja nace con el apply de compute (F7) — tag `Role=graphagents-{{slug}}`,
      pay-per-use/autostop igual que el patrón del motor
- [ ] Secretos en `/{{slug}}-graphagents/*` (el apply de platform crea los placeholders),
      uno por llave:
      `aws ssm put-parameter --overwrite --type SecureString --name /{{slug}}-graphagents/<LLAVE> --value '…'`
      — AGENTSPAN_MASTER_KEY (`openssl rand -base64 32`), POSTGRES_PASSWORD,
      META_ACCESS_TOKEN + META_AD_ACCOUNT_ID del BM de {{company}}, GHCR_PULL_TOKEN
      del package `{{slug}}-graphagents`, GRAPHAGENTS_LLM_API_KEY, LITELLM_PROXY_URL
- [ ] Primer deploy: workflow `graphagents-deploy` del repo (imagen `{{slug}}-graphagents`)
- [ ] Smoke: `python3 infra/scripts/graphagents_ctl.py status` (filtra por el tag propio)

## F6 — Temporal Cloud — step S6

- [ ] Namespace `{{slug}}` + service account + API key propia
- [ ] SSM: TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE (`{{slug}}.<acct>`) / TEMPORAL_API_KEY

## F7 — Compute + primer deploy

- [ ] `cd infra/terraform/compute && cp envs/real.s3.tfbackend.example envs/real.s3.tfbackend`
      → init + apply → caja + EIP → poner `domain = "<ip-con-guiones>.sslip.io"` en
      tenants.auto.tfvars + `api_url`/callbacks en platform → re-apply de ambos
- [ ] Push a main → backend-deploy + frontend-deploy
- [ ] Registrar webhook en la App Meta (CALLBACK_URL + verify token) y suscribir el WABA
- [ ] Primer sync del catálogo: dashboard → Catálogo → **Sync** (el refresco es SOLO manual;
      sin el primer sync, «Ver catálogo» escala a humano)
- [ ] Schedules que NO se auto-crean (contra el namespace nuevo):
      `cd hubara_agency && uv run python scripts/create_reengagement_schedule.py`
      y `cd hubara_agency && uv run python scripts/create_order_sentinel_schedule.py`

## F7b — Marketing (plugin `marketing`, nace habilitado)

- [ ] El worker `worker-marketing-campaigns` ya viene en el compose de prod.
- [ ] Aprobar en Meta la plantilla `campaign_promo_marketing` (step S5 → `templates`).

## F7c — Meta Business Agent (plugin `mba`, nace habilitado)

- [ ] Revisar `hubara_agency/src/plugins/mba/agents/sales/` (agent.yaml + skills): es la voz de {{company}} en MBA; resolver los placeholders `<FLOW_ID>`, `<TELEFONO_ASESOR>`, `<WEB_...>`, `<INSTAGRAM_...>`.
- [ ] Teléfonos de prueba: `mba.customer_allowlist` en `infra/terraform/platform/tenants.auto.tfvars` (E.164) → apply.
- [ ] `HUBARA_MBA_API_KEY` en SSM (`openssl rand -hex 32`); hasta entonces `/api/mba/tools/*` responde 503 (fail-closed) y no afecta nada más. El MISMO valor va en `auth_config.api_key` al registrar el connector en Meta.
- [ ] Onboardear el número en la Platform de Meta: la sección "Meta Business Agent" del dashboard muestra los requests exactos.

## F7d — App Operador (Android nativa, `android_operator/`)

- [ ] Identidad propia: `applicationId = "{{android_app_id}}"` (ya aplicado por forge).
- [ ] Proyecto Firebase de {{company}} → registrar la app Android `{{android_app_id}}` →
      `FCM_SERVICE_ACCOUNT_JSON` (cuenta de servicio) y `FIREBASE_ANDROID_CONFIG_JSON`
      (google-services.json) en `secrets.{{slug}}.env` → `aws_bootstrap.py secrets` (F3).
      Probar con «Probar avisos» en la app. Sin ellos la app avisa cada 15 min (sin push).
- [ ] Política de privacidad: completar `android_operator/release/politica-de-privacidad.html`
      con los datos legales de {{company}}, publicarla y poner
      `mobile = { privacy_url = "https://…" }` en platform tenants.auto.tfvars (Google Play la exige).
- [ ] Build: `-Phubara.configUrl=https://<cloudfront>/mobile/config.json` (o en
      `android_operator/gradle.properties`) + llave de subida PROPIA (`hubara.upload.*`
      en `~/.gradle/gradle.properties`) → `bundleRelease` → cuenta de Google Play de {{company}}.
- [ ] Regenerar las capturas de la ficha (`android_operator/release/play-store/generar_graficos.py`).
- [ ] CI `qa-emulador`: publica capturas vía raw.githubusercontent — en un repo privado no se
      ven en el comentario del PR y los minutos se cobran; decidir si queda como check requerido.

## F7e — Bot nuevo (motor de decisiones + Jev)

- [ ] El paquete activo lo nombra Terraform: `lab.decisions_bundle = "{{store_bundle}}"`
      (platform tenants.auto.tfvars). Los techos en canary no encienden nada.
- [ ] El bot nuevo se controla SOLO por comando (nace en off):
      `infra/scripts/bot_control.sh estado` · `infra/scripts/bot_control.sh --por <quien> workflow canary` ·
      `… numeros agregar wa_57…` · `… prueba-jev si`. El dashboard es de solo lectura.
- [ ] `lab.internal_numbers`: teléfonos del equipo de {{company}} (sus conversaciones no entran al banco).

## F7f — Laboratorio de conversaciones (opcional)

- [ ] Local: `docs/laboratorio/README.md`. En AWS: `lab.enabled = true` en compute tenants.auto.tfvars +
      apply + `/{{slug}}-lab/{DEEPSEEK,GEMINI,OPENROUTER}_API_KEY_LAB` y `/{{slug}}-lab/GHCR_PULL_TOKEN`
      (llaves NUEVAS, nunca las de producción).

## F8 — Verificación E2E

- [ ] HTTPS ok · webhook verificado · conversación real (saluda como {{company}}, NUNCA "Hubara")
- [ ] Pago anticipado: el bot da la llave de {{company}} (o no ofrece pago anticipado si no hay llave)
- [ ] Draft order en el Medusa NUEVO · dashboard con Cognito · CAPI acepta LeadSubmitted
- [ ] 5 schedules vivos en Temporal UI (reengagement, order sentinel, reconcile, sales_eval,
      post_sale_return) · 10 workers polleando · primer snapshot DLM + restore test
- [ ] CI del repo: GH secrets `DEEPSEEK_API_KEY` y `GEMINI_API_KEY` si se usa `golden-eval`
- [ ] `python3 forge/forge.py verify <esta carpeta> --client {{slug}}` (del repo madre) → sin residuales
