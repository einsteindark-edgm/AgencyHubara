# Configurar los tiempos de los schedulers desde AWS (sin redeploy)

> **Para qué:** cambiar **cuándo** corren los jobs periódicos del proyecto
> (orders-reconcile, sales-eval, golden-eval, remarketing-watchdog) editando
> una variable en AWS y reiniciando un solo worker — **sin rebuild de imagen
> ni redeploy del proyecto**.

## TL;DR

1. Cambiás el valor de la variable en AWS (SSM Parameter Store).
2. Reiniciás **solo** el worker afectado (segundos).
3. Al rebootear, el worker **converge** el Temporal Schedule al valor nuevo.

No se toca código, no se rebuildea la imagen, no se redesplega el resto.

---

## Por qué funciona sin redeploy

Los 3 schedulers de eval/reconcile son **Temporal Schedules**: objetos
*server-side* que viven en Temporal Cloud, no en la imagen del worker. Al
bootear, cada worker corre `_ensure_schedule`, que ahora **no solo crea** el
schedule si falta sino que **CONVERGE su spec** (intervalo / cron) al valor de
la env var — aunque el schedule ya exista.

> **El fix de fondo:** antes hacían *create-only* y tragaban
> `ScheduleAlreadyRunningError` sin actualizar, así que la env var solo aplicaba
> en la **primera** creación; cambiarla y reiniciar no movía nada. Ahora cada
> reboot sincroniza el spec (preservando una pausa manual hecha desde la
> Temporal UI). Ver `src/plugins/orders/workers/reconcile.py` y
> `src/plugins/chats/workers/sales_eval.py`.

El **remarketing-watchdog** NO es un Schedule: es un workflow per-episodio que
calcula su timer al abrir cada episodio. Su lead time (`WATCHDOG_PRE_EXPIRY_MINUTES`)
se lee fresco en cada episodio nuevo → con reiniciar el worker alcanza para que
los próximos episodios tomen el valor nuevo (los in-flight ya tienen su timer).

---

## Las variables

| Scheduler | Variable | Default | Tipo |
|---|---|---|---|
| orders-reconcile | `ORDER_RECONCILE_INTERVAL_MINUTES` | `5` | minutos |
| sales-eval ONLINE | `SALES_EVAL_SCHEDULE_ENABLED` | `true` | bool (`false` = borra el schedule) |
| sales-eval ONLINE | `SALES_EVAL_SCHEDULE_CRON` | `0 23 * * *` | cron (tz America/Bogota) |
| golden-eval | `GOLDEN_EVAL_SCHEDULE_ENABLED` | `false` | bool (opt-in) |
| golden-eval | `GOLDEN_EVAL_SCHEDULE_CRON` | `0 6 * * *` | cron (tz America/Bogota) |
| remarketing-watchdog | `WATCHDOG_ENABLED` | `false` | bool (feature flag del send) |
| remarketing-watchdog | `WATCHDOG_PRE_EXPIRY_MINUTES` | `30` | minutos antes de cerrar la ventana 24h |
| remarketing-watchdog | `WATCHDOG_QUIET_HOURS_START` | `8` | hora local (allowed `[START, END)`) |
| remarketing-watchdog | `WATCHDOG_QUIET_HOURS_END` | `22` | hora local |

**Qué worker corre cada uno** (a cuál reiniciar tras cambiar su variable):

| Worker (proceso) | Variables que lee |
|---|---|
| `src.plugins.orders.workers.reconcile` | `ORDER_RECONCILE_INTERVAL_MINUTES` |
| `src.plugins.chats.workers.sales_eval` | `SALES_EVAL_*`, `GOLDEN_EVAL_*` |
| `src.plugins.chats.workers.remarketing` | `WATCHDOG_*` |

---

## Cuál es tu infra (leé esto primero)

Según `infra/INFRASTRUCTURE.md` (decisión de mayo 2026, fuente de verdad):

- **Producción HOY = VPS + docker-compose + Temporal Cloud.** Los secretos y la
  config van en **AWS SSM Parameter Store** (§3.7). → **usá la Ruta A (SSM)**.
- **EKS / Kubernetes NO se usa hoy** — los manifests en `k8s/aws-produccion/`
  son "ruta de alto volumen futura" (§1, "Lo que NO usamos"). → la **Ruta B
  (ConfigMap)** aplica solo si/ cuando migres a EKS.

Las dos rutas terminan en lo mismo: las variables llegan como **env vars** al
contenedor del worker, y el código las lee con `os.environ`.

---

## Ruta A — AWS SSM Parameter Store (VPS, la que aplica hoy)

La idea: guardás cada knob como un **parámetro SSM**; el deploy de la VPS los
hidrata a env vars del contenedor al arrancar; reiniciás el worker.

### A.1 — Crear / actualizar los parámetros (una vez, y cada cambio)

Convención de path sugerida (alineada con el multi-tenant de
`INFRASTRUCTURE.md`: un namespace por compañía): `/hubara/<env>/scheduler/<VAR>`.

```bash
# Region donde están tus parámetros (ej. us-east-1).
export AWS_REGION=us-east-1
PREFIX=/hubara/prod/scheduler

# Crear (o sobrescribir con --overwrite) un knob:
aws ssm put-parameter --overwrite \
  --name "$PREFIX/ORDER_RECONCILE_INTERVAL_MINUTES" \
  --type String --value "5"

aws ssm put-parameter --overwrite \
  --name "$PREFIX/SALES_EVAL_SCHEDULE_CRON" \
  --type String --value "0 23 * * *"

aws ssm put-parameter --overwrite \
  --name "$PREFIX/WATCHDOG_PRE_EXPIRY_MINUTES" \
  --type String --value "30"
# ... idem para el resto de la tabla de arriba.
```

> **Cambiar un tiempo después** = un solo `put-parameter --overwrite` con el
> valor nuevo. No hace falta tocar nada más en AWS.

Verificar lo cargado:

```bash
aws ssm get-parameters-by-path --path "$PREFIX" --recursive \
  --query 'Parameters[].{Name:Name,Value:Value}' --output table
```

### A.2 — Hidratar SSM → env del contenedor (en el deploy de la VPS)

El prod docker-compose está pendiente (`INFRASTRUCTURE.md` §7 #3: "env desde
SSM"). Patrón recomendado para el entrypoint/deploy — **bajar el path SSM a un
`.env` y pasárselo a docker-compose**:

```bash
# scripts/load-ssm-env.sh  (corre en la VPS antes de levantar los workers)
set -euo pipefail
PREFIX=/hubara/prod/scheduler
OUT=/opt/hubara/scheduler.env

aws ssm get-parameters-by-path --path "$PREFIX" --recursive --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text \
| while read -r name value; do
    echo "$(basename "$name")=$value"
  done > "$OUT"
```

```yaml
# docker-compose.prod.yml (extracto) — cada worker toma el env file
services:
  worker-orders-reconcile:
    image: hubara-agency-prod:latest
    command: ["python", "-m", "src.plugins.orders.workers.reconcile"]
    env_file: [/opt/hubara/scheduler.env]   # ← knobs desde SSM
  worker-sales-eval:
    command: ["python", "-m", "src.plugins.chats.workers.sales_eval"]
    env_file: [/opt/hubara/scheduler.env]
  worker-remarketing:
    command: ["python", "-m", "src.plugins.chats.workers.remarketing"]
    env_file: [/opt/hubara/scheduler.env]
```

> **Alternativa más limpia:** [`chamber`](https://github.com/segmentio/chamber)
> — `chamber exec hubara/prod/scheduler -- python -m src.plugins...` inyecta el
> path SSM como env directo, sin archivo intermedio.
>
> La VPS necesita un **IAM role/policy** con `ssm:GetParametersByPath` (y
> `kms:Decrypt` si usás `SecureString`) sobre `arn:aws:ssm:<region>:<acct>:parameter/hubara/prod/scheduler/*`.

### A.3 — Aplicar el cambio (reiniciar el worker afectado)

```bash
# 1. (si usás el .env file) re-hidratá desde SSM:
./scripts/load-ssm-env.sh
# 2. reiniciá SOLO el worker que lee esa variable:
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate worker-orders-reconcile
```

El worker arranca, lee la env nueva y **converge** el Temporal Schedule. Listo.

---

## Ruta B — EKS ConfigMap (solo si migrás a Kubernetes)

Si en el futuro adoptás los manifests de `k8s/aws-produccion/`, los knobs ya
están centralizados en el ConfigMap `hubara-scheduler-config`
(`k8s/aws-produccion/scheduler-config-configmap.yaml`), referenciado por los 3
worker Deployments vía `envFrom`.

```bash
# 1. Editar el valor:
kubectl edit configmap hubara-scheduler-config        # (o kubectl apply -f ...)
# 2. Reiniciar SOLO el worker afectado:
kubectl rollout restart deploy/hubara-worker-orders-reconcile
kubectl rollout restart deploy/hubara-worker-sales-eval     # sales/golden eval
kubectl rollout restart deploy/hubara-worker-remarketing    # watchdog
```

> **Importante:** NO repitas estas claves como `env:` inline en los Deployments
> — un `env:` con el mismo nombre **pisa** al `envFrom`. Por eso este PR sacó
> los valores inline (ej. `ORDER_RECONCILE_INTERVAL_MINUTES`) de los manifests.

---

## ¿AWS o GitHub variables?

**AWS** (SSM hoy / ConfigMap en EKS). Las **GitHub variables** son de
tiempo-de-deploy: cambiarlas implica re-correr el pipeline de CI/CD para que
re-templatee la config → eso **es** un redeploy, justo lo que queremos evitar.
GitHub queda para lo que se hornea en build (no para tunear un cron en caliente).
La excepción legítima es el `golden-eval` de CI
(`.github/workflows/golden-eval.yml`), que es un job *de* GitHub y vive ahí.

---

## Gotchas

- **El cron es en `America/Bogota`** (lo fija el código, `time_zone_name`). El
  valor del cron es UTC-agnóstico: lo interpreta Temporal en esa tz.
- **`SALES_EVAL_SCHEDULE_ENABLED=false` BORRA** el Temporal Schedule (la eval
  pasa a ser event-driven al cerrar cada episodio). Volver a `true` + restart lo
  recrea.
- **`WATCHDOG_*` aplica a episodios NUEVOS.** Los watchdogs ya programados
  mantienen el timer con el que nacieron — es el comportamiento esperado.
- **Cambio en caliente sin restart:** los Schedules son server-side, así que en
  una emergencia podés cambiar el cron/intervalo directo desde la **Temporal UI**
  o `temporal schedule update` (cero código). Pero eso es imperativo y genera
  drift respecto a lo declarado en SSM/ConfigMap — usalo solo para apagar fuegos
  y después reconciliá el valor en AWS.
- **Cero-restart (futuro):** si querés cambiar el valor en SSM y que converja
  **sin** reiniciar el pod, es el upgrade "config-sync" (un mini Schedule que
  re-corre `_ensure_schedule` leyendo SSM cada N min). No está implementado acá.

---

*Referencias: `infra/INFRASTRUCTURE.md` (§3.7 SSM, §7 pendientes) ·
`k8s/aws-produccion/scheduler-config-configmap.yaml` · `.env.example` (sección
Schedulers).*
