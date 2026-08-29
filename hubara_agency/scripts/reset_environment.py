"""reset_environment.py — Deja Medusa + el vault "desde 0" para arrancar limpio.

Pensado para correrse UNA vez, después de las pruebas internas y ANTES de
abrir el canal a clientes reales, para que la data de producción no quede
contaminada con las conversaciones / pedidos de prueba.

────────────────────────────────────────────────────────────────────
LOCAL vs PRODUCCIÓN
────────────────────────────────────────────────────────────────────
  · Medusa es UNA sola instancia (Railway) — la misma para tu stack local y
    para prod. Por eso la fase Medusa NO cambia entre targets: siempre usa
    `MEDUSA_BASE_URL` de tu .env. El único eje local/prod es el VAULT.
  · El vault es un named volume Docker. Local → Docker Desktop de tu Mac.
    Prod → dentro de la caja EC2 (proyecto compose `hubara-prod`).

  --target local  (default) → vault vía `docker exec` contra tu Docker local.
  --target prod             → vault en el EC2. Dos formas:
       a) con --ssh-host → el script hace `ssh ec2-user@HOST docker exec …`.
       b) sin --ssh-host → IMPRIME los comandos para que los pegues en la
                           terminal del EC2 (AWS Console → EC2 → Connect →
                           Session Manager). No ejecuta el vault.

────────────────────────────────────────────────────────────────────
USO
────────────────────────────────────────────────────────────────────
    # LOCAL — ver qué borraría (solo lecturas):
    cd hubara_agency && uv run python scripts/reset_environment.py --dry-run
    # LOCAL — reset real:
    cd hubara_agency && uv run python scripts/reset_environment.py

    # PROD con SSH (tenés la key del EC2 local) — dry-run y luego real:
    cd hubara_agency && uv run python scripts/reset_environment.py --target prod \
        --ssh-host <TU_EIP> --ssh-key ~/.ssh/hubara-ops --dry-run
    cd hubara_agency && uv run python scripts/reset_environment.py --target prod \
        --ssh-host <TU_EIP> --ssh-key ~/.ssh/hubara-ops

    # PROD sin key (entrás por Session Manager): limpia Medusa desde tu Mac
    # e IMPRIME los comandos del vault para pegar en el EC2:
    cd hubara_agency && uv run python scripts/reset_environment.py --target prod

    # Solo una mitad (combinable con --target):
    cd hubara_agency && uv run python scripts/reset_environment.py --medusa-only
    cd hubara_agency && uv run python scripts/reset_environment.py --vault-only --target prod ...

────────────────────────────────────────────────────────────────────
QUÉ LIMPIA
────────────────────────────────────────────────────────────────────
  [Medusa — vía Admin API, reusa el HttpMedusaClient del repo]
     · Draft orders   → DELETE  (se borran de verdad)
     · Orders         → CANCEL  (Medusa v2 NO permite borrar una order ya
                                  consumada: queda con status="canceled".
                                  Para hacerlas DESAPARECER ver el runbook
                                  docs/runbook-medusa-borrar-canceladas.md.)
     · Customers      → DELETE  (los creados por el bot / cualquiera)
     · PRESERVA: productos / catálogo, regions, sales channels, shipping
                 options, y toda la config del store.

  [Vault — el named volume Docker con la data de conversaciones]
     · wa_*           → conversaciones (sessions/*.jsonl + metadata.json + media/)
     · _analytics     → analytics derivados de las pruebas
     · _evals         → candidatos / historia de evals de prueba
     · PRESERVA: catalog/ (snapshot derivado; se re-sincroniza solo).
                 Usá --wipe-catalog-snapshot para borrarlo también.

────────────────────────────────────────────────────────────────────
QUÉ NO TOCA (a propósito)
────────────────────────────────────────────────────────────────────
  · WhatsApp / Meta — no se puede "resetear" el historial del lado de Meta.
  · El workspace de identidad del agente (SOUL.md / IDENTITY.md / memory/…):
    es configuración, no data de prueba.
  · Temporal — los workflows de sesión "vivos" no se terminan acá (en prod
    usás Temporal Cloud). Reiniciá los workers tras el wipe (el script te
    imprime el comando del target correcto).

⚠️  Esto BORRA en producción. El script te muestra el destino (Medusa host +
    vault EC2) y te hace tipearlo antes de borrar. No imprime secretos.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

from loguru import logger

# Permite `uv run python scripts/reset_environment.py` directo: al ejecutar por
# path, sys.path[0] es scripts/, no hubara_agency/. Insertamos la raíz del
# backend para que `from src...` resuelva sin depender del editable-install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Importar config PRIMERO: su `load_dotenv()` puebla os.environ desde el .env
# (vive en el repo principal, no en el worktree) antes de instanciar settings.
from src.platform.config import WORKSPACE_VAULT_DIR  # noqa: E402,F401  (side-effect: load_dotenv)
from src.platform.logging import setup_logging  # noqa: E402
from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError  # noqa: E402
from src.platform.medusa.composition import (  # noqa: E402
    get_medusa_client,
    get_medusa_settings,
)

setup_logging()

# Subdirectorios del vault que son data de prueba (se borran). `catalog/` queda
# afuera a propósito: es un snapshot derivado de Medusa, no data de conversación.
VAULT_TEST_DIRS = ["wa_*", "_analytics", "_evals"]
VAULT_CATALOG_DIR = "catalog"

# Container por defecto sobre el que hacemos `docker exec` para tocar el named
# volume del vault (montado en /app/hubara_vault). Cualquiera que monte el
# volume sirve; la API/`api` es el más estable.
DEFAULT_VAULT_CONTAINER = "local-hubara-api"   # local (docker-compose.local.yml)
PROD_VAULT_CONTAINER = "hubara-prod-api-1"     # EC2 (compose `name: hubara-prod`)
DEFAULT_VAULT_DIR = "/app/hubara_vault"
SSH_DEFAULT_USER = "ec2-user"                  # AMI del EC2 (ver backend-deploy.yml)


class Ssh(NamedTuple):
    """Destino SSH para ejecutar `docker` en una caja remota (el EC2)."""
    user: str
    host: str
    key: str | None


# ─────────────────────────────────────────────────────────────────────
# Medusa  (idéntico en local y prod — es la misma instancia Railway)
# ─────────────────────────────────────────────────────────────────────

async def _collect_ids(
    list_fn,
    envelope_key: str,
    *,
    page_size: int = 100,
    keep=lambda _row: True,
) -> list[str]:
    """Pagina `list_fn(limit, offset)` y junta los `id` de las filas que pasan
    `keep`. Junta TODO antes de borrar para no pelear con el offset mientras
    la colección se encoge."""
    ids: list[str] = []
    offset = 0
    while True:
        page = await list_fn(limit=page_size, offset=offset)
        rows = page.get(envelope_key, [])
        if not rows:
            break
        for row in rows:
            if keep(row):
                ids.append(row["id"])
        count = page.get("count", len(rows))
        offset += page_size
        if offset >= count:
            break
    return ids


async def _list_customers_page(
    client: HttpMedusaClient, *, limit: int, offset: int
) -> dict:
    """El cliente no expone offset para customers; lo llamamos directo. Es una
    utilidad operacional, por eso usamos el `_request` de bajo nivel (reusa la
    auth Basic + retries del cliente)."""
    return await client._request(
        "GET", "/admin/customers", params={"limit": limit, "offset": offset}
    )


async def reset_medusa(*, dry_run: bool, keep_customers: bool) -> None:
    client = get_medusa_client()
    try:
        # 1) DRAFT ORDERS — se borran de verdad (DELETE).
        draft_ids = await _collect_ids(
            client.list_draft_orders, "draft_orders"
        )
        logger.info("Medusa · draft orders a borrar: {}", len(draft_ids))
        if not dry_run:
            deleted = await _apply(
                draft_ids, client.cancel_draft_order, "draft order"
            )
            logger.info("Medusa · draft orders borradas: {}/{}", deleted, len(draft_ids))

        # 2) ORDERS — solo se pueden CANCELAR (quedan status="canceled").
        #    Saltamos las que ya están canceladas.
        order_ids = await _collect_ids(
            client.list_orders,
            "orders",
            keep=lambda o: (o.get("status") != "canceled"
                            and not o.get("canceled_at")),
        )
        logger.info(
            "Medusa · orders a cancelar (no se pueden borrar en v2): {}",
            len(order_ids),
        )
        if not dry_run:
            cancelled = await _apply(order_ids, client.cancel_order, "order")
            logger.info("Medusa · orders canceladas: {}/{}", cancelled, len(order_ids))

        # 3) CUSTOMERS — DELETE. Puede fallar si Medusa retiene el customer por
        #    sus orders (canceladas siguen existiendo); esos se reportan y se
        #    saltan, no abortan el reset.
        if keep_customers:
            logger.info("Medusa · customers: SKIP (--keep-customers)")
        else:
            cust_ids = await _collect_ids(
                lambda **kw: _list_customers_page(client, **kw), "customers"
            )
            logger.info("Medusa · customers a borrar: {}", len(cust_ids))
            if not dry_run:
                async def _del_customer(cid: str):
                    return await client._request(
                        "DELETE", f"/admin/customers/{cid}"
                    )
                removed = await _apply(cust_ids, _del_customer, "customer")
                logger.info("Medusa · customers borrados: {}/{}", removed, len(cust_ids))
    finally:
        await client.aclose()


async def _apply(ids: list[str], fn, label: str) -> int:
    """Ejecuta `fn(id)` por cada id, contando éxitos y reportando fallos
    individuales sin abortar."""
    ok = 0
    for _id in ids:
        try:
            await fn(_id)
            ok += 1
        except MedusaAPIError as e:
            logger.warning("  ✗ {} {} no se pudo: HTTP {} — {}",
                           label, _id, e.status_code, e.body[:120])
        except Exception as e:  # noqa: BLE001 — utilidad ops, seguimos con el resto
            logger.warning("  ✗ {} {} error: {}", label, _id, e)
    return ok


# ─────────────────────────────────────────────────────────────────────
# Vault (named volume Docker) — local (`docker`) o prod (`ssh … docker`)
# ─────────────────────────────────────────────────────────────────────

def _docker(ssh: Ssh | None, *args: str) -> subprocess.CompletedProcess:
    """Corre `docker <args>` localmente, o vía SSH si `ssh` está seteado.

    Para el caso remoto, serializamos el comando con `shlex.join` y se lo
    pasamos a `ssh` como un único argumento → el shell del EC2 lo re-parsea
    intacto (incl. el `sh -c '…'` anidado del wipe)."""
    if ssh is None:
        argv = ["docker", *args]
    else:
        remote = shlex.join(["docker", *args])
        argv = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
        if ssh.key:
            argv += ["-i", os.path.expanduser(ssh.key)]
        argv += [f"{ssh.user}@{ssh.host}", remote]
    return subprocess.run(argv, capture_output=True, text=True)


def _resolve_vault_container(requested: str, ssh: Ssh | None) -> str | None:
    """Devuelve un container vivo que monte el vault. Prefiere el pedido; si no
    está, cae al primero con 'hubara' en el nombre. Funciona local o vía SSH."""
    ps = _docker(ssh, "ps", "--format", "{{.Names}}")
    if ps.returncode != 0:
        where = f"vía ssh {ssh.user}@{ssh.host}" if ssh else "local"
        logger.error("No pude listar containers ({}): {}", where, ps.stderr.strip())
        return None
    names = [n for n in ps.stdout.splitlines() if n.strip()]
    if requested in names:
        return requested
    for n in names:
        if "hubara" in n:
            logger.warning(
                "Container '{}' no está corriendo; uso '{}' (monta el mismo volume).",
                requested, n,
            )
            return n
    logger.error(
        "No encontré ningún container vivo que monte el vault "
        "(pedido '{}', vivos: {}). ¿Está levantado el stack?",
        requested, names or "ninguno",
    )
    return None


def reset_vault(
    *,
    dry_run: bool,
    container: str,
    vault_dir: str,
    wipe_catalog: bool,
    ssh: Ssh | None,
    print_only: bool,
) -> None:
    dirs = list(VAULT_TEST_DIRS)
    if wipe_catalog:
        dirs.append(VAULT_CATALOG_DIR)
    du_cmd = f"cd {vault_dir} 2>/dev/null && du -sh {' '.join(dirs)} 2>/dev/null || true"
    rm_cmd = f"cd {vault_dir} && rm -rf {' '.join(dirs)} && echo WIPED"

    # PROD sin SSH: no ejecutamos; imprimimos los comandos para Session Manager.
    if print_only:
        logger.info(
            "Vault · sin --ssh-host → NO ejecuto el vault. Pegá esto en la "
            "terminal del EC2 (AWS Console → EC2 → tu instancia → Connect → "
            "Session Manager):"
        )
        print()
        print("  # 1) ver qué hay (no borra):")
        print(f"  docker exec {container} sh -c {shlex.quote(du_cmd)}")
        print("  # 2) borrar:")
        print(f"  docker exec {container} sh -c {shlex.quote(rm_cmd)}")
        print()
        return

    target = _resolve_vault_container(container, ssh)
    if target is None:
        raise SystemExit(2)

    where = f"{ssh.user}@{ssh.host}:" if ssh else ""
    listing = _docker(ssh, "exec", target, "sh", "-c", du_cmd)
    logger.info("Vault · contenido a borrar en {}{}:{}\n{}",
                where, target, vault_dir, listing.stdout.rstrip() or "  (vacío)")

    if dry_run:
        logger.info("Vault · DRY-RUN: no se borró nada.")
        return

    rm = _docker(ssh, "exec", target, "sh", "-c", rm_cmd)
    if rm.returncode != 0 or "WIPED" not in rm.stdout:
        logger.error("Vault · falló el wipe: {}", rm.stderr.strip() or rm.stdout.strip())
        raise SystemExit(2)
    logger.info("Vault · limpio ✅ (borrado: {})", ", ".join(dirs))


# ─────────────────────────────────────────────────────────────────────
# Confirmación + main
# ─────────────────────────────────────────────────────────────────────

def _confirm(
    medusa_host: str, do_medusa: bool, do_vault: bool, *, target: str, ssh: Ssh | None
) -> bool:
    print()
    print("  ╔══════════════════════════════════════════════════════════════╗")
    print("  ║   RESET DE AMBIENTE — esto BORRA datos de forma irreversible  ║")
    print("  ╚══════════════════════════════════════════════════════════════╝")
    if target == "prod":
        print("   \033[1;31m⚠️  TARGET: PRODUCCIÓN\033[0m")
    if do_medusa:
        print(f"   • Medusa (Admin API)   → {medusa_host}")
        print("       drafts: DELETE · orders: CANCEL · customers: DELETE")
        print("       (preserva catálogo / regions / sales channels / shipping)")
    if do_vault:
        if ssh:
            dest = f"{ssh.user}@{ssh.host} (EC2, vía SSH)"
        elif target == "prod":
            dest = "EC2 vía Session Manager (el script imprime los comandos)"
        else:
            dest = "Docker local"
        print(f"   • Vault                → {dest}")
        print("       borra: wa_* + _analytics + _evals")
    print()
    # Token de confirmación (guardarraíl anti-ambiente-equivocado):
    #   con Medusa → el host de Medusa; vault-only prod con SSH → el host del EC2.
    if do_medusa:
        expected = medusa_host
        print(f"   El target de Medusa es: \033[1;31m{medusa_host}\033[0m")
    elif ssh:
        expected = ssh.host
    else:
        expected = "BORRAR"
    resp = input(f'   Para confirmar, tipeá EXACTO "{expected}": ').strip()
    return resp == expected


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Limpia Medusa + el vault para arrancar 'desde 0'.",
    )
    ap.add_argument("--target", choices=["local", "prod"], default="local",
                    help="local (Docker de tu Mac) o prod (vault en el EC2). Default: local.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Solo reporta qué borraría (solo lecturas, no destruye).")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="No pedir confirmación interactiva.")
    ap.add_argument("--medusa-only", action="store_true", help="Solo limpiar Medusa.")
    ap.add_argument("--vault-only", action="store_true", help="Solo limpiar el vault.")
    ap.add_argument("--keep-customers", action="store_true",
                    help="No borrar customers de Medusa.")
    ap.add_argument("--wipe-catalog-snapshot", action="store_true",
                    help="Borrar también el snapshot catalog/ del vault (se re-sincroniza).")
    # Vault target prod (SSH al EC2). Sin --ssh-host en prod → imprime comandos.
    ap.add_argument("--ssh-host", default=None,
                    help="[target prod] EIP/host del EC2 (lo resolvés con terraform output).")
    ap.add_argument("--ssh-user", default=SSH_DEFAULT_USER,
                    help=f"[target prod] usuario SSH (default: {SSH_DEFAULT_USER}).")
    ap.add_argument("--ssh-key", default=None,
                    help="[target prod] path a la private key (ej: ~/.ssh/hubara-ops).")
    ap.add_argument("--vault-container", default=None,
                    help="Container del docker exec. Default: según --target.")
    ap.add_argument("--vault-dir", default=DEFAULT_VAULT_DIR,
                    help=f"Path del vault dentro del container (default: {DEFAULT_VAULT_DIR}).")
    args = ap.parse_args()

    if args.medusa_only and args.vault_only:
        ap.error("--medusa-only y --vault-only son mutuamente excluyentes.")
    do_medusa = not args.vault_only
    do_vault = not args.medusa_only
    is_prod = args.target == "prod"

    # Resolver cómo llega la fase vault a su Docker daemon.
    ssh: Ssh | None = None
    print_only = False
    if do_vault:
        if is_prod and args.ssh_host:
            ssh = Ssh(user=args.ssh_user, host=args.ssh_host, key=args.ssh_key)
        elif is_prod:
            print_only = True  # sin key → imprimimos comandos para el EC2
        elif args.ssh_host:
            logger.warning("--ssh-host se ignora con --target local.")
    vault_container = args.vault_container or (
        PROD_VAULT_CONTAINER if is_prod else DEFAULT_VAULT_CONTAINER
    )

    medusa_host = ""
    if do_medusa:
        settings = get_medusa_settings()
        medusa_host = urlparse(settings.base_url).netloc or settings.base_url

    if not args.dry_run and not args.yes:
        if not _confirm(medusa_host, do_medusa, do_vault, target=args.target, ssh=ssh):
            logger.info("Cancelado (no coincidió la confirmación). No se tocó nada.")
            sys.exit(1)

    mode = "DRY-RUN (no borra nada)" if args.dry_run else "REAL"
    logger.info("🧹 Reset de ambiente [{}] · target={}", mode, args.target)

    if do_medusa:
        logger.info("── Medusa @ {} ──", medusa_host)
        asyncio.run(reset_medusa(dry_run=args.dry_run, keep_customers=args.keep_customers))

    if do_vault:
        logger.info("── Vault ({}) ──", "EC2/prod" if is_prod else "local")
        reset_vault(
            dry_run=args.dry_run,
            container=vault_container,
            vault_dir=args.vault_dir,
            wipe_catalog=args.wipe_catalog_snapshot,
            ssh=ssh,
            print_only=print_only,
        )

    logger.info("✅ Reset terminado [{}].", mode)
    if not args.dry_run and do_vault and not print_only:
        if is_prod:
            logger.info(
                "Tip: reiniciá los workers en el EC2 para que suelten estado en memoria → "
                "cd /opt/hubara && docker compose restart "
                "worker-chats-sales worker-chats-remarketing worker-eta-eta worker-orders-reconcile"
            )
        else:
            logger.info(
                "Tip: reiniciá los workers para que suelten estado en memoria → "
                "docker compose -f docker-compose.local.yml restart "
                "hubara-worker-chats-sales hubara-worker-chats-remarketing "
                "hubara-worker-eta-eta hubara-worker-orders-reconcile"
            )


if __name__ == "__main__":
    main()
