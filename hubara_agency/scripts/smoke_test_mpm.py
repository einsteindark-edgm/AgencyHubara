"""Smoke test: enviar `interactive.product_list` a un destinatario.

Verifica end-to-end:
  * Token tiene `whatsapp_business_messaging` (necesario para POST /messages).
  * El catálogo está linkeado al WABA del Phone Number ID.
  * Los retailer_ids existen en Meta Catalog en status "Active".

NO requiere `catalog_management` — el send de MPM solo necesita
`whatsapp_business_messaging`. Por eso este test sirve ANTES de hacer
Fases 9-11 del .md de setup (útil para validar que el catálogo manual
está en orden mientras se completa el setup del System User).

Uso (desde repo root):
  cd hubara_agency
  uv run python scripts/smoke_test_mpm.py <recipient_e164> <retailer_id_1> [<retailer_id_2> ...]

Ej:
  uv run python scripts/smoke_test_mpm.py 573001234567 vela-lavanda-001 vela-cafe-002

Env vars requeridos (los del .env del backend):
  * META_CATALOG_ID
  * META_SYSTEM_USER_TOKEN
  * WHATSAPP_PHONE_NUMBER_ID

Caps Meta para `interactive.product_list`:
  * Max 10 sections, max 30 productos totales.
  * Min 1 producto.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

GRAPH_API_VERSION = "v23.0"


def _require_env(name: str) -> str:
    val = (os.environ.get(name) or "").strip()
    if not val:
        print(f"ERROR: env var {name} no está seteado.", file=sys.stderr)
        print(
            "  Cargalo en hubara_agency/.env o exportalo en la sesión.",
            file=sys.stderr,
        )
        sys.exit(2)
    return val


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recipient",
        help="Número destinatario en formato E.164 sin `+`. Ej 573001234567",
    )
    parser.add_argument(
        "retailer_ids",
        nargs="+",
        help="Uno o más retailer_id de productos en Meta Catalog.",
    )
    parser.add_argument(
        "--section-title",
        default="Aromáticas",
        help="Título de la sección del MPM (default: 'Aromáticas')",
    )
    parser.add_argument(
        "--header",
        default="Nuestras velas aromáticas",
        help="Texto del header (default: 'Nuestras velas aromáticas')",
    )
    parser.add_argument(
        "--body",
        default="Mirá lo que tenemos para vos hoy",
        help="Texto del body (default: 'Mirá lo que tenemos para vos hoy')",
    )
    args = parser.parse_args()

    catalog_id = _require_env("META_CATALOG_ID")
    token = _require_env("META_SYSTEM_USER_TOKEN")
    phone_id = _require_env("WHATSAPP_PHONE_NUMBER_ID")

    # Validar cap Meta (30 productos max)
    if len(args.retailer_ids) > 30:
        print(
            f"ERROR: Meta cap es 30 productos por MPM, recibí {len(args.retailer_ids)}.",
            file=sys.stderr,
        )
        sys.exit(2)

    payload = {
        "messaging_product": "whatsapp",
        "to": args.recipient,
        "type": "interactive",
        "interactive": {
            "type": "product_list",
            "header": {"type": "text", "text": args.header},
            "body": {"text": args.body},
            "footer": {"text": "Hubara"},
            "action": {
                "catalog_id": catalog_id,
                "sections": [
                    {
                        "title": args.section_title,
                        "product_items": [
                            {"product_retailer_id": rid}
                            for rid in args.retailer_ids
                        ],
                    }
                ],
            },
        },
    }

    print(f"→ Enviando MPM a {args.recipient}")
    print(f"  catalog_id: {catalog_id}")
    print(f"  phone_id:   {phone_id}")
    print(f"  productos:  {len(args.retailer_ids)}")
    for rid in args.retailer_ids:
        print(f"    - {rid}")

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_id}/messages"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        print(f"\n❌ Transport error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nStatus: {resp.status_code}")
    try:
        body = resp.json()
        print(json.dumps(body, indent=2, ensure_ascii=False))
    except ValueError:
        print(resp.text)

    if resp.status_code >= 300:
        print("\n❌ Send FAILED. Diagnosticos comunes:", file=sys.stderr)
        print(
            "  - 'Product retailer_id X doesn't exist': mistype o producto en "
            "'Pending review' / 'Rejected'. Verificar en Commerce Manager.",
            file=sys.stderr,
        )
        print(
            "  - 'Invalid catalog_id': catálogo no linkeado al WABA del "
            "phone_id usado. Ver Fase 8 del .md.",
            file=sys.stderr,
        )
        print(
            "  - 'Recipient not in allowed list': tu número no está en "
            "'Manage phone number list' de la app (development mode).",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\n✅ Send OK. Chequeá tu WhatsApp en ~3 segundos.")


if __name__ == "__main__":
    main()
