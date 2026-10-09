"""IdentityKit — quién es el cliente de WhatsApp (teléfono o id de Meta), PURO.

Un cliente con nombre de usuario de WhatsApp puede llegar SIN teléfono: Meta
solo manda su id (`from_user_id`, BSUID: `CO.1502576394655843`). Su
conversación es `wa_CO1502576394655843` (el id sin el punto: directorio seguro
del vault) y el cliente HTTP de platform le contesta con `recipient` en vez de
`to` — ningún plugin arma ese campo.

Por qué es un kit aparte: el parser del webhook lo necesita y el contrato
R-DIP #8 (`parsers-pure`) le prohíbe importar I/O; `messagingkit` trae las
activities de envío (`httpx`, `temporalio`). Este kit depende solo de
`src.platform.whatsapp.user_id` (stdlib + constantes; guard:
`tests/platform/test_identitykit.py`).

Uso canónico::

    from src.sdk.identitykit import address_from_user_id, is_customer_session_id

    address_from_user_id("CO.1502576394655843")      # "CO1502576394655843"
    is_customer_session_id("wa_CO1502576394655843")  # True (y "wa_573001234567")
"""
from __future__ import annotations

# Alias idiom (regla 1 SDK): sin el `as x`, ruff --fix poda el re-export.
from src.platform.whatsapp.user_id import (
    address_from_user_id as address_from_user_id,
    is_customer_session_id as is_customer_session_id,
    is_user_id_address as is_user_id_address,
)
