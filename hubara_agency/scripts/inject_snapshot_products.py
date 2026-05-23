"""Inyecta productos al snapshot local SIN correr el sync Medusa→Meta.

Caso de uso: tenés productos en Meta Catalog (subidos manualmente desde
Commerce Manager) y querés que el agente sales los conozca para probar
el flow `interactive.product_list` (MPM) — pero todavía no podés correr
el sync productivo (porque falta el `catalog_management` scope, por
ejemplo).

Cómo funciona:
  1. Editás la lista `PRODUCTS_TO_INJECT` abajo con los IDs Meta + datos.
  2. Corrés el script con --mode replace (default) o --mode append.
  3. El script escribe `snapshot.json` + `by_handle/*.json` + `manifest.json`
     en el volumen del worker via `docker exec`.
  4. El `LocalSnapshotCatalogClient` del agente detecta el cambio por mtime
     y recarga — sin restart del worker.
  5. Cliente chatea, agente usa los productos inyectados.

REQUISITOS:
  * El contenedor `local-hubara-worker-catalog-sync` corriendo.
  * Los `id` de cada producto deben matchear el "Content ID" / retailer_id
    EXACTO que vos subiste en Commerce Manager — sino el MPM falla con
    "retailer_id not found".

USO:
    # Modo REPLACE (default): el snapshot tendrá SOLO estos productos.
    # Útil para test puro de MPM. Para restaurar Medusa: correr el sync
    # cuando tengas permisos, o re-correr este script en append.
    uv run python scripts/inject_snapshot_products.py

    # Modo APPEND: agrega a lo que ya hay en snapshot (sin tocar Medusa).
    # CUIDADO: el agente puede tratar de mostrar productos Medusa en MPM
    # y fallar porque sus retailer_ids no existen en Meta.
    uv run python scripts/inject_snapshot_products.py --mode append
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

CONTAINER = "local-hubara-worker-catalog-sync"
SNAPSHOT_PATH = "/app/hubara_vault/catalog/snapshot.json"
MANIFEST_PATH = "/app/hubara_vault/catalog/manifest.json"
BY_HANDLE_DIR = "/app/hubara_vault/catalog/by_handle"


# ═════════════════════════════════════════════════════════════════════════
# ⬇⬇⬇  EDITÁ ESTOS 2 PRODUCTOS CON TUS VALORES REALES DE COMMERCE MANAGER
# ═════════════════════════════════════════════════════════════════════════
#
#   `id`        → debe ser EXACTAMENTE el "Content ID" (retailer_id) que
#                 vos pusiste en Commerce Manager al subir la vela. Si no
#                 matchea, el MPM en WhatsApp NO renderiza la card.
#   `handle`    → slug URL-friendly, libre (ej. "vela-lavanda-250g").
#   `title`     → nombre legible de la vela.
#   `thumbnail` → URL pública JPG/PNG (NO .webp). Si está en assets.hubara
#                 .com.co, dejá la URL .webp — el agente la sirve igual
#                 (el agente usa send_image que normaliza a JPEG).
#   `price`     → entero en COP. Ej 45000.
#   `tags`      → para el LLM matchear ("Aroma: Lavanda", "Color: Morado").
#   `categories`→ para el LLM matchear ("velas", "aromaticas").
#
# Tags compartidos entre las dos velas religiosas (vienen del snapshot
# de Medusa — clientes piden por aroma/color y el agente filtra usando
# estos tags).
_RELIGIOUS_VARIANT_TAGS = [
    "Aroma: Caballero de la noche",
    "Aroma: Limoncillo",
    "Color: gris",
    "Color: Amarillo",
    "Color: verde",
    "Aroma: Lavanda",
    "Aroma: Café",
    "Aroma: Sándalo",
    "Aroma: Ylan ylang",
    "Aroma: Coco cremoso",
    "Aroma: Frutos rojos",
    "Aroma: Verde menta",
    "Aroma: Drakar",
    "Aroma: Chanel",
    "Color: Naranja",
    "Color: Morado",
    "Color: Azul",
    "Color: Blanco",
    "Color: Lila",
    "Color: Rosado",
]

PRODUCTS_TO_INJECT: list[dict] = [
    # ──────────────────────────────────────────────────────────────────
    # Luz Serena — manualmente subida a Meta Catalog, Content ID = 1yxc1gm6cn
    # Metadata (imágenes, precio, tags) tomada del snapshot de Medusa para
    # que el agente tenga toda la información rica que necesita para
    # describir el producto, sugerirlo, filtrar por aroma, etc.
    # ──────────────────────────────────────────────────────────────────
    {
        "id": "1yxc1gm6cn",  # ← Content ID exacto de Commerce Manager
        "handle": "luz-serena",
        "title": "Luz Serena",
        "status": "published",
        "description": "Vela religiosa Luz Serena de Hubara — disponible en múltiples aromas y colores.",
        "thumbnail": "https://assets.hubara.com.co/hero-desktop_2560x1440%20(1)-01KN0WTG0ZYM3QTMWYRFZJWT4N.webp",
        "variants": [
            {
                "id": "1yxc1gm6cn-v1",
                "title": "Unico",
                "sku": None,
                "prices": [{"amount": "23000", "currency_code": "cop"}],
            }
        ],
        "images": [
            {"url": "https://assets.hubara.com.co/hero-desktop_2560x1440%20(1)-01KN0WTG0ZYM3QTMWYRFZJWT4N.webp", "rank": 0},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(10)-01KN0WTH28F8DR8PXFY8F0XM4N.webp", "rank": 1},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(11)-01KN0WTHJSH7XYD20S17M831DC.webp", "rank": 2},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(12)-01KN0WTJ57R840FQD66M1TMZ0D.webp", "rank": 3},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(13)-01KN0WTJWVVJESZVK8ES6P06KH.webp", "rank": 4},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(2)-01KN0WTKQJ2G7RYB71J13ARGG4.webp", "rank": 5},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(8)-01KN0WTMJTM4WDMRBP9DYGHA6B.webp", "rank": 6},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(9)-01KN0WTN7EES9ZY9SQB5AGHKPE.webp", "rank": 7},
        ],
        "tags": list(_RELIGIOUS_VARIANT_TAGS),
        "categories": ["religiosas"],
        "metadata": None,
    },
    # ──────────────────────────────────────────────────────────────────
    # Cruz de Vida — manualmente subida a Meta Catalog, Content ID = kpirhs1jrp
    # ──────────────────────────────────────────────────────────────────
    {
        "id": "kpirhs1jrp",  # ← Content ID exacto de Commerce Manager
        "handle": "cruz-de-vida",
        "title": "Cruz de Vida",
        "status": "published",
        "description": "Vela religiosa Cruz de Vida de Hubara — disponible en múltiples aromas y colores.",
        "thumbnail": "https://assets.hubara.com.co/hero-desktop_2560x1440%20(1)-01KN0WTXJ5PD59GW932VYHYXDQ.webp",
        "variants": [
            {
                "id": "kpirhs1jrp-v1",
                "title": "Unico",
                "sku": None,
                "prices": [{"amount": "17000", "currency_code": "cop"}],
            }
        ],
        "images": [
            {"url": "https://assets.hubara.com.co/hero-desktop_2560x1440%20(1)-01KN0WTXJ5PD59GW932VYHYXDQ.webp", "rank": 0},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(10)-01KN0WTY5C3P4Q7BQ5TGZGVMJ2.webp", "rank": 1},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(11)-01KN0WTYQWWFP7JDJWYXJEWRN3.webp", "rank": 2},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(12)-01KN0WTZDG71SMBK3YAPX8D4RP.webp", "rank": 3},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(14)-01KN0WTZZN1AXTM90PNH86FV03.webp", "rank": 4},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(2)-01KN0WV0NT6ZM3S9V7K8S6RJGE.webp", "rank": 5},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(8)-01KN0WV18M1SBJ48AT59GBR4SR.webp", "rank": 6},
            {"url": "https://assets.hubara.com.co/product-vertical-3-4_1500x2000%20(9)-01KN0WV1V9R7D19BVR01MEVFAK.webp", "rank": 7},
        ],
        "tags": list(_RELIGIOUS_VARIANT_TAGS),
        "categories": ["religiosas"],
        "metadata": None,
    },
]
# ═════════════════════════════════════════════════════════════════════════


def _validate_products() -> None:
    """Sanity check antes de tocar el snapshot."""
    for i, p in enumerate(PRODUCTS_TO_INJECT):
        if "REEMPLAZAR" in p["id"]:
            print(
                f"❌ Producto #{i+1}: todavía dice 'REEMPLAZAR' en el id. "
                "Editá `PRODUCTS_TO_INJECT` arriba con tus valores reales "
                "antes de correr.",
                file=sys.stderr,
            )
            sys.exit(2)
        if not p["variants"] or not p["variants"][0].get("prices"):
            print(f"❌ Producto #{i+1}: falta variants[0].prices", file=sys.stderr)
            sys.exit(2)


def _docker_exec(args: list[str], stdin: str | None = None) -> str:
    """Wrapper sobre `docker exec` con manejo de errores."""
    cmd = ["docker", "exec"]
    if stdin is not None:
        cmd.append("-i")
    cmd.extend([CONTAINER, *args])
    try:
        result = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"❌ docker exec falló: {' '.join(cmd)}", file=sys.stderr)
        print(f"   stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["replace", "append"],
        default="replace",
        help=(
            "replace (default): snapshot tendrá SOLO los productos inyectados. "
            "append: se agregan a los existentes (ej. los de Medusa)."
        ),
    )
    args = parser.parse_args()

    _validate_products()

    # 1. Verificar que el contenedor está corriendo
    check = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        print(
            f"❌ Contenedor {CONTAINER!r} no está corriendo. "
            f"Levantalo con: docker compose -f hubara_agency/docker-compose.local.yml up -d {CONTAINER}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Leer snapshot actual (puede no existir si nunca corrió el sync)
    try:
        current_raw = _docker_exec(["cat", SNAPSHOT_PATH])
        current = json.loads(current_raw) if current_raw.strip() else []
    except Exception:
        current = []

    print(f"📂 Snapshot actual tiene {len(current)} productos")

    # 3. Construir la nueva lista según mode
    new_ids = {p["id"] for p in PRODUCTS_TO_INJECT}
    if args.mode == "replace":
        final = list(PRODUCTS_TO_INJECT)
        print(f"🔄 Modo REPLACE: snapshot quedará con {len(final)} productos")
    else:
        # Append: filtrar duplicados por id (re-inyección idempotente)
        filtered = [p for p in current if p["id"] not in new_ids]
        final = filtered + list(PRODUCTS_TO_INJECT)
        print(
            f"➕ Modo APPEND: snapshot quedará con {len(final)} productos "
            f"({len(filtered)} preexistentes + {len(PRODUCTS_TO_INJECT)} nuevos)"
        )

    # 4. Escribir snapshot.json
    snapshot_json = json.dumps(final, ensure_ascii=False, indent=2)
    _docker_exec(["tee", SNAPSHOT_PATH], stdin=snapshot_json)
    print(f"✅ Escrito {SNAPSHOT_PATH}")

    # 5. Asegurar by_handle/ existe + escribir un archivo por producto
    _docker_exec(["mkdir", "-p", BY_HANDLE_DIR])
    if args.mode == "replace":
        # Limpiar by_handle/ vieja (productos Medusa que ya no están)
        _docker_exec(["sh", "-c", f"rm -f {BY_HANDLE_DIR}/*.json"])

    for prod in PRODUCTS_TO_INJECT:
        handle_path = f"{BY_HANDLE_DIR}/{prod['handle']}.json"
        prod_json = json.dumps(prod, ensure_ascii=False, indent=2)
        _docker_exec(["tee", handle_path], stdin=prod_json)
    print(f"✅ Escritos {len(PRODUCTS_TO_INJECT)} archivos by_handle/")

    # 6. Actualizar manifest
    from datetime import datetime, timezone

    manifest = {
        "version": "manual-inject-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "product_count": len(final),
        "source_etag": None,
    }
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    _docker_exec(["tee", MANIFEST_PATH], stdin=manifest_json)
    print(f"✅ Manifest actualizado (count={len(final)})")

    print()
    print("═" * 60)
    print("🎉 Snapshot inyectado. El agente sales lo recarga automáticamente.")
    print()
    print("Productos inyectados:")
    for p in PRODUCTS_TO_INJECT:
        price = p["variants"][0]["prices"][0]["amount"]
        print(f"  • {p['id']:45s}  {p['title']}  ${price} COP")
    print()
    print("Probá ahora desde tu WhatsApp:")
    print("  'muéstrame el catálogo' → debería usar MPM (interactive.product_list)")
    print("  'qué velas tienen para comprar' → debería usar MPM")
    print()
    print("Para restaurar Medusa: correr `trigger_catalog_sync.py` cuando")
    print("tengas el `catalog_management` scope.")


if __name__ == "__main__":
    main()
