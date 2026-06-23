# Runbook — narrativa LLM del reporter CTWA en AWS (opción D: DeepSeek directo)

El nodo narrativo de `ctwa-report` (la prosa interpretativa que acompaña la tabla de
unit-economics) usa un LLM. En **prod** lo resolvemos con la **opción D**: el vendor
`LiteLLMProxy` le pega **DIRECTO a DeepSeek** (`https://api.deepseek.com`, OpenAI-compatible),
**sin** un proxy LiteLLM intermedio ni contenedor extra. El modelo es **pura config**: cambiar de
proveedor/modelo es editar un parámetro SSM, sin tocar código ni redeploy de imagen.

> **Por qué D y no un proxy:** la caja de GraphAgents es un subsistema aparte (otra EC2). Un proxy
> central viviría en otra caja (cross-box, auth-less, regla SG, toca el backend always-on); un proxy
> dedicado sería un contenedor + keys + config a mantener. D no agrega infra: una key + una URL.
> La contra de D (sin failover deepseek→gemini) no importa: la narrativa **degrada sola** si el LLM
> falla — el reporte determinista (tabla + verdict) sale igual, con un marcador visible (ver L-26).

## Lo que hay que setear (SSM `/graphagents/`)

| Parámetro | Valor | Tipo | Secreto |
|---|---|---|---|
| `GRAPHAGENTS_LLM_API_KEY` | tu `DEEPSEEK_API_KEY` | SecureString | **sí** (fuera de banda) |
| `LITELLM_PROXY_URL` | `https://api.deepseek.com` | SecureString¹ | no |
| `GRAPHAGENTS_LLM_MODEL` | *(no setear)* | — | — |

¹ No es secreto, pero va por el mismo módulo `graphagents-secrets` (SecureString + placeholder) por
uniformidad. El render-script lo desencripta igual. `GRAPHAGENTS_LLM_MODEL` queda en su **default
de código** `deepseek-v4-flash` (= el id real que DeepSeek recibe; el mismo que mapea tu
`litellm_config.yaml`), así que **no hace falta declararlo**.

## Pasos

**1. Declarar las claves en Terraform** (ya está en el repo: `infra/terraform/platform/variables.tf`
→ `graphagents_secret_keys`). Aplicá la capa platform para crear los placeholders + el grant del
instance profile:

```bash
cd infra/terraform/platform && terraform apply   # crea /graphagents/GRAPHAGENTS_LLM_API_KEY + /graphagents/LITELLM_PROXY_URL (placeholder)
```

**2. Setear los valores REALES fuera de banda** (no entran a git/state):

```bash
aws ssm put-parameter --overwrite --type SecureString \
  --name /graphagents/GRAPHAGENTS_LLM_API_KEY --value "<tu DEEPSEEK_API_KEY>"

aws ssm put-parameter --overwrite --type SecureString \
  --name /graphagents/LITELLM_PROXY_URL --value "https://api.deepseek.com"
```

**3. Re-renderizar `.env` + reiniciar el servicio** en la caja (vía SSM Session Manager, o
disparando `graphagents-deploy.yml`):

```bash
# en /opt/graphagents/ de la caja GraphAgents:
sudo ./render-env-from-ssm.sh          # re-pulls /graphagents/* → .env (incluye las 2 nuevas)
sudo docker compose -f docker-compose.prod.yml up -d graphagents   # toma el nuevo .env
```

> El flujo: `/graphagents/*` (SSM) → `render-env-from-ssm.sh` → `.env` → `env_file:` del servicio
> `graphagents` → env del contenedor → `LiteLLMProxy` lee `LITELLM_PROXY_URL` + `GRAPHAGENTS_LLM_API_KEY`.

## Verificación EN VIVO (tests verdes ≠ feature viva)

Corré un workflow de GraphAgents y mirá el nodo `ctwa_report` en el explorer (`:8900`):

- **OK:** la pestaña *Output* del nodo muestra `narrative` con prosa real (cita los números del
  análisis) y **sin** `narrative_error`. El port `llm` se tinta azul en el canvas.
- **Degradado (revisar config):** `narrative` = `"[narrativa no disponible…]"` y `narrative_error`
  trae el detalle. Causas típicas:
  - `[Errno 99]` / connection → `LITELLM_PROXY_URL` mal seteada (o quedó en `localhost:4000`).
  - HTTP 401 → falta/incorrecta `GRAPHAGENTS_LLM_API_KEY` (Bearer).
  - HTTP 400 "model not found" → `GRAPHAGENTS_LLM_MODEL` no es un id válido de DeepSeek.
  - **El reporte determinista sale igual** en todos estos casos — el workflow no se cae.

Smoke local (opcional, con tu key en el env — **nunca** la imprimas):

```bash
cd GraphAgents && GRAPHAGENTS_LLM_API_KEY="$DEEPSEEK_API_KEY" \
  LITELLM_PROXY_URL="https://api.deepseek.com" \
  .venv/bin/python3 -c "from sdk.connectorkit.ports import LiteLLMProxy; \
print(LiteLLMProxy().complete(system='Resumí en una frase.', user='spend 100, MER 2.0'))"
```

## Cambiar de modelo/proveedor después (config-only)

Como D hace al modelo pura config, A/B-testear otro proveedor OpenAI-compatible es **un solo
parámetro**, sin tocar código:

- **Step 3.5 Flash** (StepFun, el piso de precio en 2026-06): `LITELLM_PROXY_URL=https://api.stepfun.ai`
  + `GRAPHAGENTS_LLM_MODEL=step-3.5-flash` + la key de StepFun en `GRAPHAGENTS_LLM_API_KEY`.
- **Volver a un proxy LiteLLM** (con failover): `LITELLM_PROXY_URL` al proxy + **vaciar**
  `GRAPHAGENTS_LLM_API_KEY` (sin Bearer = proxy abierto).

Después de cualquier cambio: `put-parameter` → `render-env-from-ssm.sh` → `up -d graphagents`.
