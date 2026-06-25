# Servicios AWS activos + cómo cambiar los tiempos de los schedulers

> **Verificado en vivo el 2026-06-23** (región **us-east-1**). IDs reales abajo.
> Complementa a `infra/INFRASTRUCTURE.md` (decisión de infra) y a
> `hubara_agency/docs/scheduler-config-aws.md` (el mecanismo de fondo: por qué
> funciona sin redeploy).

---

## 1. Servicios de AWS que quedaron activos

Todo en **us-east-1**. Hay **dos tenants** provisionados: **hubara** (vivo y en
uso) y **vincenzo** (infra creada, app todavía con URLs placeholder).

### 1.1 Cómputo — EC2
| Recurso | ID / Nombre | Tipo | Notas |
|---|---|---|---|
| Caja **app** (backend) | `i-07ebe6296b9e50865` · `agencyhubara-hubara-app` | `t3.medium` | **Siempre encendida.** 9 contenedores: api + caddy + litellm + 6 workers. |
| Caja **GraphAgents** | `i-0fcf7e7ffed39820d` · `agencyhubara-graphagents` | `t3.large` | **Pay-per-use** (autostop por idle). Si la ves encendida sin uso, apagala. |
| **Elastic IP** | `eipalloc-03724215c63c8e1aa` → **98.88.237.207** | — | Atada a la caja app. URL prod: `https://98-88-237-207.sslip.io` |

### 1.2 Edge/CDN (CloudFront) + Storage (S3)
| Tenant | CloudFront | Bucket S3 |
|---|---|---|
| **hubara** | `ELS1OP88LMO7M` → `d1hvhzkh01tri0.cloudfront.net` | `agencyhubara-hubara-frontend` |
| **vincenzo** | `E1RYQQ6BRH0UGJ` → `d2n1dbc9k2oro1.cloudfront.net` | `agencyhubara-vincenzo-frontend` |
| *(estado de Terraform)* | — | `agencyhubara-tfstate-525237381234` |

### 1.3 Auth — Cognito
| Tenant | User Pool | Notas |
|---|---|---|
| **hubara** | `us-east-1_tj9egBufy` · `agencyhubara-hubara` | App client `2da3f0nd91vq947d19r7ofkj6m`; hosted UI `agencyhubara-hubara` |
| **vincenzo** | `us-east-1_0Jkh63kBE` · `agencyhubara-vincenzo` | Provisionado |

### 1.4 Config + Secretos — SSM Parameter Store
- `/hubara/hubara/*` — config + secretos del tenant hubara (incluye `/scheduler/*`, ver §2).
- Tipos: `String` (config, p.ej. los schedulers) + `SecureString` (secretos, cifrados con KMS AWS-managed `alias/aws/ssm`).
- La caja app los lee con su **instance profile** (`ssm:GetParametersByPath`).

### 1.5 State lock — DynamoDB
- `agencyhubara-tflock` — lock del state de Terraform (evita applies concurrentes).

### 1.6 IAM + Networking (plumbing de Terraform)
- **IAM**: instance profiles (caja app + graphagents), rol amplio read-state (CI), rol angosto de deploy (CI) y **OIDC provider de GitHub** (CI sin llaves estáticas).
- **VPC** + subnets + security groups + Internet Gateway.

### 1.7 ⚠️ NO son AWS (externos, pero parte del stack)
- **Temporal Cloud** (SaaS) — namespace `hubara.ri1ti`, vía API key. **Acá viven los Schedules** que tunean estas variables.
- **GHCR** (GitHub Container Registry) — imágenes `agencyhubara` (backend) + `graphagents`.

### 1.8 💰 Qué cuesta (orden de magnitud)
- **EC2 app `t3.medium`** — el costo fijo principal (24/7).
- **EC2 graphagents `t3.large`** — solo mientras corre (autostop). Encendida sin uso = plata tirada → apagala.
- CloudFront + S3 + Cognito + SSM (Standard) + DynamoDB → centavos / free tier a este volumen.
- **EIP** — gratis mientras esté atada a una instancia **corriendo** (si la instancia se apaga, la EIP suelta empieza a cobrar).

---

## 2. Las variables de scheduler (estado actual en SSM)

Path en SSM: `/hubara/hubara/scheduler/<VARIABLE>` · tipo `String`.

| Variable | Valor actual | Qué controla | Worker que la lee |
|---|---|---|---|
| `ORDER_RECONCILE_INTERVAL_MINUTES` | `5` | cada cuántos min reconcilia órdenes | `worker-orders-reconcile` |
| `SALES_EVAL_SCHEDULE_ENABLED` | `true` | prende/apaga la eval diaria (`false` BORRA el Schedule) | `worker-chats-sales_eval` |
| `SALES_EVAL_SCHEDULE_CRON` | `0 23 * * *` | a qué hora corre la eval diaria (23:00 Bogotá) | `worker-chats-sales_eval` |
| `GOLDEN_EVAL_SCHEDULE_ENABLED` | `false` | opt-in del golden set | `worker-chats-sales_eval` |
| `GOLDEN_EVAL_SCHEDULE_CRON` | `0 6 * * *` | hora del golden set (si está enabled) | `worker-chats-sales_eval` |
| `WATCHDOG_ENABLED` | `false` | feature flag del envío del watchdog de remarketing | `worker-chats-remarketing` |
| `WATCHDOG_PRE_EXPIRY_MINUTES` | `30` | min antes de cerrar la ventana 24h de WhatsApp | `worker-chats-remarketing` |
| `WATCHDOG_QUIET_HOURS_START` | `8` | hora local desde la que se permite enviar | `worker-chats-remarketing` |
| `WATCHDOG_QUIET_HOURS_END` | `22` | hora local hasta la que se permite enviar | `worker-chats-remarketing` |

Schedules en Temporal Cloud (namespace `hubara.ri1ti`): `order-reconciliation-schedule`,
`sales-eval-schedule`, `golden-eval-schedule`.

---

## 3. Cómo cambiar un tiempo — flujo de 2 pasos (sin redeploy)

### Paso 1 — cambiar el valor en AWS (SSM)
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/<VARIABLE> --value "<NUEVO_VALOR>"
```

### Paso 2 — aplicar en la caja (re-render + reiniciar SOLO el worker afectado)
```bash
ssh -i ~/.ssh/hubara_ops ec2-user@98.88.237.207
cd /opt/hubara
./render-env-from-ssm.sh        # baja SSM → .env (reusa la imagen actual; NO redeploya)
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate <WORKER>
```

**Mapa variable → worker a reiniciar:**

| Variable(s) cambiada(s) | Worker a reiniciar |
|---|---|
| `ORDER_RECONCILE_INTERVAL_MINUTES` | `worker-orders-reconcile` |
| `SALES_EVAL_*` · `GOLDEN_EVAL_*` | `worker-chats-sales_eval` |
| `WATCHDOG_*` | `worker-chats-remarketing` |

Al rebootear, el worker lee la env nueva y **converge** el Schedule en Temporal
Cloud. Cero rebuild de imagen, cero redeploy del resto.

> **Nota watchdog:** los `WATCHDOG_*` aplican a episodios **nuevos**. Los watchdogs
> ya programados conservan el timer con el que nacieron (comportamiento esperado).

---

## 4. Días, horas, minutos, segundos — qué granularidad soporta cada knob

Hay **dos mecanismos** distintos según la variable:

### A) Intervalo — solo `ORDER_RECONCILE_INTERVAL_MINUTES`
Es un entero **en minutos**. Para otras unidades, multiplicás:

| Querés correrlo... | Valor |
|---|---|
| cada 30 **segundos** | ❌ no soportado (mínimo 1 minuto) |
| cada 5 **minutos** | `5` |
| cada 30 **minutos** | `30` |
| cada 1 **hora** | `60` |
| cada 6 **horas** | `360` |
| cada 1 **día** | `1440` |
| cada 7 **días** | `10080` |

### B) Cron — `SALES_EVAL_SCHEDULE_CRON` y `GOLDEN_EVAL_SCHEDULE_CRON`
5 campos, en **zona horaria America/Bogota** (la fija el código, no la tocás):

```
 ┌──────── minuto        (0-59)
 │ ┌────── hora          (0-23)
 │ │ ┌──── día del mes   (1-31)
 │ │ │ ┌── mes           (1-12)
 │ │ │ │ ┌ día de semana (0-6, 0 = domingo)
 │ │ │ │ │
 * * * * *
```

| Querés correrlo... | Cron |
|---|---|
| cada **minuto** | `* * * * *` |
| cada 15 **minutos** | `*/15 * * * *` |
| en el minuto 30 de cada **hora** | `30 * * * *` |
| cada **hora** en punto | `0 * * * *` |
| cada 2 **horas** | `0 */2 * * *` |
| todos los **días** a las 23:00 | `0 23 * * *` |
| L–V a las 8:00 | `0 8 * * 1-5` |
| a las 8:00, 14:00 y 20:00 | `0 8,14,20 * * *` |
| **domingos** a las 6:00 | `0 6 * * 0` |
| el **día 1** de cada mes a las 6:00 | `0 6 1 * *` |

> **Segundos:** el cron estándar NO tiene campo de segundos (mínimo 1 minuto).

### C) Watchdog — `WATCHDOG_*` (no es un Schedule, es un timer por-episodio)
| Variable | Unidad | Ejemplo |
|---|---|---|
| `WATCHDOG_PRE_EXPIRY_MINUTES` | **minutos** antes de cerrar la ventana 24h | `30` |
| `WATCHDOG_QUIET_HOURS_START` | **hora** local (0-23) | `8` |
| `WATCHDOG_QUIET_HOURS_END` | **hora** local (0-23), ventana permitida `[START, END)` | `22` |
| `WATCHDOG_ENABLED` | bool — `true`/`false` (on/off del envío) | `false` |

### ⏱️ ¿Y los segundos?
**Ningún knob expone segundos** — es deliberado: reconciliar órdenes y correr
evals no necesita sub-minuto, y el cron estándar tampoco lo soporta. Si alguna
vez se necesitara (p.ej. reconciliar cada 30s) es **cambio de código** (el
intervalo de Temporal sí soporta segundos por dentro, pero la env var es
`..._MINUTES`). Como parche de emergencia se puede editar el Schedule directo en
la **Temporal UI** con un intervalo en segundos, pero el próximo restart del
worker lo **re-converge** al valor de SSM (en minutos) → úsalo solo para apagar
un fuego y después reconciliá el valor en SSM.

---

## 5. Comando exacto por cada variable

> Todos asumen tenant **hubara**. Tras el `put-parameter`, corré el Paso 2 (§3)
> reiniciando el worker indicado.

**`ORDER_RECONCILE_INTERVAL_MINUTES`** — cada cuánto reconcilia órdenes · reiniciar `worker-orders-reconcile`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/ORDER_RECONCILE_INTERVAL_MINUTES --value "5"
```

**`SALES_EVAL_SCHEDULE_ENABLED`** — `false` BORRA el Schedule (la eval pasa a event-driven al cerrar cada episodio) · reiniciar `worker-chats-sales_eval`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/SALES_EVAL_SCHEDULE_ENABLED --value "true"
```

**`SALES_EVAL_SCHEDULE_CRON`** — hora de la eval diaria (tz Bogotá) · reiniciar `worker-chats-sales_eval`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/SALES_EVAL_SCHEDULE_CRON --value "0 23 * * *"
```

**`GOLDEN_EVAL_SCHEDULE_ENABLED`** — opt-in del golden set · reiniciar `worker-chats-sales_eval`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/GOLDEN_EVAL_SCHEDULE_ENABLED --value "false"
```

**`GOLDEN_EVAL_SCHEDULE_CRON`** — hora del golden set (si enabled) · reiniciar `worker-chats-sales_eval`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/GOLDEN_EVAL_SCHEDULE_CRON --value "0 6 * * *"
```

**`WATCHDOG_ENABLED`** — feature flag del envío del watchdog · reiniciar `worker-chats-remarketing`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/WATCHDOG_ENABLED --value "false"
```

**`WATCHDOG_PRE_EXPIRY_MINUTES`** — min antes de cerrar la ventana 24h · reiniciar `worker-chats-remarketing`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/WATCHDOG_PRE_EXPIRY_MINUTES --value "30"
```

**`WATCHDOG_QUIET_HOURS_START` / `WATCHDOG_QUIET_HOURS_END`** — ventana horaria permitida `[START, END)` · reiniciar `worker-chats-remarketing`
```bash
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/WATCHDOG_QUIET_HOURS_START --value "8"
aws ssm put-parameter --overwrite --region us-east-1 --type String \
  --name /hubara/hubara/scheduler/WATCHDOG_QUIET_HOURS_END --value "22"
```

---

## 6. Verificar

```bash
# Ver TODOS los valores actuales de scheduler en SSM:
aws ssm get-parameters-by-path --region us-east-1 \
  --path /hubara/hubara/scheduler --recursive \
  --query 'Parameters[].[Name,Value]' --output table
```

```bash
# Ver el Schedule efectivo (lo que realmente disparará) en Temporal Cloud:
#   https://cloud.temporal.io  →  namespace hubara.ri1ti  →  Schedules
#   order-reconciliation-schedule · sales-eval-schedule · golden-eval-schedule
```

En la caja, confirmá que el worker tomó la env nueva:
```bash
ssh -i ~/.ssh/hubara_ops ec2-user@98.88.237.207 \
  "cd /opt/hubara && docker compose -f docker-compose.prod.yml logs --tail=20 worker-orders-reconcile"
# Buscá la línea "📅 Schedule '...' actualizado — ..."
```

---

## Gotchas

- **Path real = `/hubara/hubara/scheduler/`** (namespace por **tenant**), NO
  `/hubara/prod/scheduler/` como sugería de ejemplo el doc viejo. El render usa
  `/hubara/${TENANT}` → usá siempre `hubara`.
- **El cron es tz America/Bogota** (lo fija el código). El valor que escribís se
  interpreta en esa zona, no en UTC.
- **`SALES_EVAL_SCHEDULE_ENABLED=false` BORRA** el Schedule (no lo pausa). Volver
  a `true` + restart lo recrea.
- **No hardcodees estos knobs en `render-env-from-ssm.sh`** (sección b): vienen de
  SSM a propósito; si los duplicás ahí, el último gana en el `env_file` y pisás SSM.
- **Cero-restart no existe (aún):** hoy el cambio de valor en SSM sí es sin
  redeploy, pero requiere reiniciar el worker afectado. El "config-sync" que
  reconvergería sin restart está documentado como futuro en
  `hubara_agency/docs/scheduler-config-aws.md`.

---

*Refs: `infra/INFRASTRUCTURE.md` · `hubara_agency/docs/scheduler-config-aws.md` ·
`infra/compose/render-env-from-ssm.sh` · `infra/compose/docker-compose.prod.yml` ·
código: `src/plugins/orders/workers/reconcile.py`,
`src/plugins/chats/workers/sales_eval.py`,
`src/plugins/chats/agent/remarketing/activities/watchdog_activities.py`.*
