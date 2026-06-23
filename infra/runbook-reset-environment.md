# Runbook — Reset de ambiente (`reset_environment.py`)

Cómo dejar **Medusa + el vault de conversaciones "desde 0"** después de las
pruebas internas y antes de abrir el canal a clientes reales, para que la data
de producción no quede contaminada.

- **Script:** [`hubara_agency/scripts/reset_environment.py`](../hubara_agency/scripts/reset_environment.py)
- **Complemento** (hacer desaparecer las orders canceladas): [`docs/runbook-medusa-borrar-canceladas.md`](../docs/runbook-medusa-borrar-canceladas.md)

---

## TL;DR

```bash
# LOCAL — ver qué borraría (no toca nada):
cd hubara_agency && uv run python scripts/reset_environment.py --dry-run

# LOCAL — reset real:
cd hubara_agency && uv run python scripts/reset_environment.py

# PRODUCCIÓN (tenés la key del EC2) — dry-run y luego real:
cd hubara_agency && uv run python scripts/reset_environment.py --target prod \
    --ssh-host <TU_EIP> --ssh-key ~/.ssh/hubara-ops --dry-run
cd hubara_agency && uv run python scripts/reset_environment.py --target prod \
    --ssh-host <TU_EIP> --ssh-key ~/.ssh/hubara-ops

# PRODUCCIÓN (sin key, entrás por Session Manager):
cd hubara_agency && uv run python scripts/reset_environment.py --target prod
```

> **Siempre corré `--dry-run` primero.** Es solo-lectura y te muestra los conteos reales.

---

## Qué borra y qué preserva

| Zona | Borra | Preserva |
|---|---|---|
| **Medusa** (Admin API) | draft orders (DELETE), orders (CANCEL), customers (DELETE) | **catálogo/productos, regions, sales channels, shipping, config del store** |
| **Vault** (volume Docker) | `wa_*` (conversaciones: `.jsonl` + `metadata.json` + media), `_analytics`, `_evals` | `catalog/` (snapshot derivado, se re-sincroniza solo) |

**No toca:** WhatsApp/Meta (no se puede resetear del lado de Meta), el workspace
de identidad del agente (SOUL.md / memory), ni Temporal.

> ⚠️ **Las orders ya consumadas no se borran, solo se cancelan** (límite de Medusa
> v2): quedan con `status="canceled"`. Para que **desaparezcan** del dashboard y
> de Medusa Admin, corré después el soft-delete del
> [runbook de canceladas](../docs/runbook-medusa-borrar-canceladas.md).

---

## Modelo mental: local vs producción

- **Medusa es UNA sola instancia** (Railway), la misma para tu stack local y para
  prod. Por eso la fase Medusa **no cambia** entre targets: siempre usa
  `MEDUSA_BASE_URL` de tu `.env`.
- **El vault sí es distinto por ambiente** (es un volume Docker):
  - `--target local` → el Docker Desktop de tu Mac (container `local-hubara-api`).
  - `--target prod` → dentro de la caja **EC2** (container `hubara-prod-api-1`).

Es decir: `--target prod` solo cambia **a qué vault apunta**. Medusa se limpia
igual en ambos casos.

---

## Prerrequisitos

1. **Corré desde `main`**, no desde un worktree: el script lee el `.env` (con
   `MEDUSA_BASE_URL` + `MEDUSA_ADMIN_TOKEN`), que vive en
   `hubara_agency/.env` del repo principal.
2. **El prefijo `cd hubara_agency &&` es obligatorio** (lo exige un hook del repo
   y resuelve el entorno `uv`).
3. Para **producción**, además:
   - El stack tiene que estar **levantado** en el EC2 (`docker compose up -d`).
   - **Camino A (SSH):** la private key del par `hubara-ops` en tu Mac
     (ej. `~/.ssh/hubara-ops`) + la **EIP** del EC2.
   - **Camino B (Session Manager):** acceso a la consola de AWS. No necesitás key.

---

## Uso LOCAL

```bash
# 1) Dry-run — lista las conversaciones y los conteos de Medusa, sin borrar:
cd hubara_agency && uv run python scripts/reset_environment.py --dry-run

# 2) Reset real — pide confirmación (tipeás el host de Medusa):
cd hubara_agency && uv run python scripts/reset_environment.py

# 3) Reiniciá los workers para que suelten estado en memoria (el script te
#    imprime este comando al terminar):
cd hubara_agency && docker compose -f docker-compose.local.yml restart \
    hubara-worker-chats-sales hubara-worker-chats-remarketing \
    hubara-worker-eta-eta hubara-worker-orders-reconcile
```

---

## Uso PRODUCCIÓN

Primero conseguí los dos datos (ver [Apéndice](#apéndice-conseguir-eip-y-key)):
tu **EIP** y, si vas por SSH, la **key**.

### Camino A — con la key del EC2 en tu Mac (un solo comando)

El script hace el `ssh … docker exec` solo. La fase Medusa corre desde tu Mac
(apunta a Railway prod igual).

```bash
# 1) Dry-run (lecturas vía SSH):
cd hubara_agency && uv run python scripts/reset_environment.py --target prod \
    --ssh-host <TU_EIP> --ssh-key ~/.ssh/hubara-ops --dry-run

# 2) Reset real:
cd hubara_agency && uv run python scripts/reset_environment.py --target prod \
    --ssh-host <TU_EIP> --ssh-key ~/.ssh/hubara-ops

# 3) Reiniciá los workers EN EL EC2 (el script te imprime este comando):
#    entrá por SSH y corré:
ssh -i ~/.ssh/hubara-ops ec2-user@<TU_EIP> \
    'cd /opt/hubara && docker compose restart worker-chats-sales worker-chats-remarketing worker-eta-eta worker-orders-reconcile'
```

### Camino B — sin key, por Session Manager

El script limpia **Medusa desde tu Mac** e **imprime los comandos del vault**
para que los pegues en la terminal del EC2.

```bash
cd hubara_agency && uv run python scripts/reset_environment.py --target prod
```

Después:

1. **AWS Console → EC2 → tu instancia → Connect → Session Manager** (abre una
   terminal en el browser).
2. Pegá los dos comandos `docker exec …` que imprimió el script (uno para ver,
   otro para borrar).
3. Reiniciá los workers en esa misma terminal:
   ```bash
   cd /opt/hubara && docker compose restart \
       worker-chats-sales worker-chats-remarketing worker-eta-eta worker-orders-reconcile
   ```

---

## Flujo completo recomendado (pre-launch)

```bash
# 1) Revisar:
cd hubara_agency && uv run python scripts/reset_environment.py --target prod --ssh-host <EIP> --ssh-key ~/.ssh/hubara-ops --dry-run

# 2) Borrar drafts + cancelar orders + borrar customers + limpiar vault:
cd hubara_agency && uv run python scripts/reset_environment.py --target prod --ssh-host <EIP> --ssh-key ~/.ssh/hubara-ops

# 3) Hacer DESAPARECER las orders que quedaron canceladas (soft-delete):
#    → seguí docs/runbook-medusa-borrar-canceladas.md (npx medusa exec).
```

Resultado: Medusa y el vault quedan limpios para el arranque real.

---

## Referencia de flags

| Flag | Para qué |
|---|---|
| `--target {local,prod}` | Dónde está el vault. Default `local`. |
| `--dry-run` | Solo reporta, no borra. **Corrélo siempre primero.** |
| `-y`, `--yes` | Saltea la confirmación interactiva (automatización). |
| `--medusa-only` | Solo Medusa (no toca el vault). |
| `--vault-only` | Solo el vault (no toca Medusa). |
| `--keep-customers` | No borrar customers de Medusa. |
| `--wipe-catalog-snapshot` | Borrar también `catalog/` del vault (se re-sincroniza). |
| `--ssh-host <IP>` | `[prod]` EIP/host del EC2. Sin esto en prod → imprime comandos. |
| `--ssh-user <user>` | `[prod]` usuario SSH (default `ec2-user`). |
| `--ssh-key <path>` | `[prod]` private key (ej. `~/.ssh/hubara-ops`). |
| `--vault-container <name>` | Override del container (default según `--target`). |
| `--vault-dir <path>` | Path del vault dentro del container (default `/app/hubara_vault`). |

---

## Troubleshooting

| Síntoma | Causa / fix |
|---|---|
| `El comando 'uv run' debe ir con prefijo 'cd hubara_agency &&'` | Hook del repo. Antepóné `cd hubara_agency &&`. |
| `No module named 'src'` | Corrés desde un worktree sin entorno. Usá `main` (donde está el `.env` y el venv). |
| Medusa: `HTTP 401` | `MEDUSA_ADMIN_TOKEN` expirado/ inválido en el `.env`. Regenerá el Secret API Key en Medusa Admin. |
| Vault: `No encontré ningún container vivo que monte el vault` | El stack no está levantado, o el nombre del container difere. Verificá con `docker ps` y pasá `--vault-container <name>`. |
| SSH se cuelga pidiendo `passphrase` | Tu key tiene passphrase. Tipeala, o usá `ssh-add ~/.ssh/hubara-ops` antes. |
| SSH: `Permission denied (publickey)` | Key incorrecta o usuario equivocado. Confirmá `--ssh-key` y que el usuario sea `ec2-user`. |
| En prod borró el vault **local** por error | Te faltó `--target prod`. Sin él, apunta al Docker de tu Mac. |

---

## Apéndice: conseguir EIP y key

**EIP del EC2 (tenant `hubara`):**

```bash
# Opción 1 — Terraform (necesita el backend S3 inicializado):
cd infra/terraform/compute && terraform output -json app_hosts | jq -r '.hubara.public_ip'

# Opción 2 — AWS Console → EC2 → Elastic IPs (filtrá por el tag del tenant).
```

**Key SSH:** es la privada del par `hubara-ops` (la pública está en
`infra/terraform/compute/tenants.auto.tfvars`). La generaste vos al hacer el
primer deploy; debería estar en tu `~/.ssh/`. Si no la tenés a mano, usá el
**Camino B** (Session Manager) — no requiere key.
