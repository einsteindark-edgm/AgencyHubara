"""Product ref from the storefront's WhatsApp button (`ref: HUB-…`).

The PDP prefills the message the customer sends (hubara_frontend,
`whatsapp-handoff.ts`): the product name, its URL and a machine-readable tail
`📦 ref: HUB-CUBOLOVE · via: chatgpt`. The ref is the variant **SKU** — the
stable identity Google's feed, Meta's catalog and the site moved to on
2026-09-14 — never a Medusa id, which changes when a product is re-created.

Same rules as the web cart (`web_cart.py`): the ref is detected by regex, never
by the LLM; the capture is episode-scoped; and the note only reaches the prompt
once the SKU resolved against the catalog, framed as metadata. `via:` is
attacker-writable, so it is stored for attribution and never enters the prompt.
"""
from __future__ import annotations

import re

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    get_active_episode,
)

# `ref:` (case-insensitive) + SKU `HUB-<CODE>[-<VARIANT>]`. The marker is
# mandatory: a bare SKU in the text triggers nothing (smaller attack surface).
# The SKU is normalised to uppercase, which is how the uploader loads it.
_PRODUCT_REF_RE = re.compile(r"(?i:ref):\s*(HUB-[A-Z0-9]+(?:-[A-Z0-9]+)?)(?![A-Z0-9-])", re.IGNORECASE)

# Closed list, mirrored from the storefront's `AgentSource`.
_AGENT_SOURCES = ("chatgpt", "gemini", "perplexity", "copilot", "claude")
_AGENT_SOURCE_RE = re.compile(
    r"(?i:via):\s*(" + "|".join(_AGENT_SOURCES) + r")(?![a-z0-9.])", re.IGNORECASE
)


#: `origin` of a ref that came from WhatsApp's own catalog (the product card's
#: «Enviar mensaje a la empresa» button), not from the website.
CATALOG_ORIGIN = "catalogo_whatsapp"


# A catalog code: the variant SKU (`HUB-…`) or a Medusa id (`prod_…`,
# `variant_…`). Anything else (spaces, punctuation, a paragraph) is not one.
_RETAILER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def referred_product_id(context: object) -> str | None:
    """The `product_retailer_id` of `context.referred_product`: the customer
    wrote from a product card of our WhatsApp catalog («Enviar mensaje a la
    empresa»). Meta sends it; until 2026-09-30 it was dropped. Only a real
    catalog code is read (it later reaches the prompt as a SKU)."""
    referred = context.get("referred_product") if isinstance(context, dict) else None
    code = referred.get("product_retailer_id") if isinstance(referred, dict) else None
    return code if isinstance(code, str) and _RETAILER_ID_RE.fullmatch(code) else None


def detect_product_ref(text: str | None) -> str | None:
    """Extracts the SKU of a `ref: HUB-…` token, uppercased."""
    if not text:
        return None
    match = _PRODUCT_REF_RE.search(text)
    return match.group(1).upper() if match else None


def detect_agent_source(text: str | None) -> str | None:
    """Which AI agent sent the customer, when the storefront tagged it."""
    if not text:
        return None
    match = _AGENT_SOURCE_RE.search(text)
    return match.group(1).lower() if match else None


def apply_web_product_capture(
    metadata: dict, *, sku: str, source: str | None, now_ms: int, origin: str = "web"
) -> bool:
    """Records the ref in `metadata.web_product_ref`.

    Same SKU within the same episode is a no-op (a second tap must not reset a
    resolved state); a different SKU wins (the customer moved to another
    product). Returns True when the state changed, so the caller resolves
    against the catalog once per ref.
    """
    episode = get_active_episode(metadata)
    episode_id = (episode or {}).get("episode_id")
    current = metadata.get("web_product_ref")
    if (
        isinstance(current, dict)
        and current.get("sku") == sku
        and current.get("episode_id") == episode_id
    ):
        return False
    metadata["web_product_ref"] = {
        "sku": sku,
        "source": source,
        # Where the customer saw it: the website (`ref: HUB-…`) or the product
        # card of the WhatsApp catalog (`CATALOG_ORIGIN`).
        "origin": origin,
        "status": "pending",
        "detected_at_ms": now_ms,
        # Episode-scoped, like the web cart: a note about a product seen weeks
        # ago must not follow the customer into a new conversation.
        "episode_id": episode_id,
    }
    return True


def mark_web_product_resolved(
    metadata: dict, *, handle: str, title: str, variant: str | None = None
) -> None:
    state = metadata.setdefault("web_product_ref", {})
    state["status"] = "resolved"
    state["handle"] = handle
    state["title"] = title
    if variant:
        state["variant"] = variant


def mark_web_product_unresolved(metadata: dict, *, reason: str) -> None:
    """`reason` is observability only (sku_not_found, timeout…); never prompted."""
    state = metadata.setdefault("web_product_ref", {})
    state["status"] = "unresolved"
    state["reason"] = reason


# Tuteo colombiano (REGLA #1 IDENTITY.md, guard test_no_voseo_in_agent_strings).
_NOTE_HEADER = (
    "[PRODUCTO VISTO EN LA WEB, metadata, no es instruccion del usuario]\n"
)


def build_web_product_note(metadata: dict) -> str | None:
    """Projects the note for `plugin_context`, only for a resolved ref in the
    episode where it was captured and while that episode has no order."""
    state = metadata.get("web_product_ref")
    if not isinstance(state, dict) or state.get("status") != "resolved":
        return None

    episode = get_active_episode(metadata)
    if episode is not None and episode.get("order_id"):
        return None
    captured_in = state.get("episode_id")
    if captured_in and (episode or {}).get("episode_id") != captured_in:
        return None

    if state.get("origin") == CATALOG_ORIGIN:
        return _catalog_card_note(state)
    return (
        _NOTE_HEADER
        + f"El cliente llego a WhatsApp desde la pagina de {state.get('title')} "
        f"(handle {state.get('handle')}, SKU {state.get('sku')}). "
        "Ese es el producto que le interesa: confirmalo con una frase y sigue "
        "con la venta (aroma, color o signo si aplica, cantidad, datos de "
        "envio). No le preguntes de nuevo que producto busca."
    )


_CATALOG_NOTE_HEADER = "[PRODUCTO DE LA FICHA DEL CATÁLOGO, metadata, no es instrucción del usuario]\n"


def _catalog_card_note(state: dict) -> str:
    """The customer tapped «Enviar mensaje a la empresa» on a product card of
    our WhatsApp catalog: their message is about that product."""
    variant = f" ({state['variant']})" if state.get("variant") else ""
    return (
        _CATALOG_NOTE_HEADER
        + f"El cliente escribió desde la ficha de {state.get('title')}{variant} en el catálogo de "
        f"WhatsApp, con el botón «Enviar mensaje a la empresa» (handle {state.get('handle')}, "
        f"código {state.get('sku')}). Su mensaje es sobre ese producto: respóndele sobre él y "
        "no le preguntes qué producto busca."
    )


_DAY_MS = 86_400_000

# The referral log only has to cover a busy season of conversations with one
# customer; bounding it keeps a spammed tap from growing metadata.json forever.
_MAX_AGENT_REFERRALS = 100


def record_agent_referral(metadata: dict, *, source: str, sku: str, now_ms: int) -> bool:
    """Appends to `metadata.agent_referrals`, the history the dashboard counts.

    `web_product_ref` answers "what is this customer looking at now" and the
    next ref overwrites it, so it cannot count "how many conversations did
    ChatGPT send". This log is append-only: one entry per source per episode
    (a second tap is the same referral). Human-routed sessions have no active
    episode, so those dedupe by day instead.

    `via:` is attacker-writable: only the closed list is recorded, and nothing
    here reaches the prompt. Returns True when an entry was appended.
    """
    if source not in _AGENT_SOURCES:
        return False

    episode_id = (get_active_episode(metadata) or {}).get("episode_id")
    referrals = metadata.get("agent_referrals")
    if not isinstance(referrals, list):
        referrals = []

    for entry in referrals:
        if not isinstance(entry, dict) or entry.get("source") != source:
            continue
        if episode_id is not None and entry.get("episode_id") == episode_id:
            return False
        if (
            episode_id is None
            and entry.get("episode_id") is None
            and isinstance(entry.get("at_ms"), int)
            and entry["at_ms"] // _DAY_MS == now_ms // _DAY_MS
        ):
            return False

    referrals.append(
        {"source": source, "sku": sku, "episode_id": episode_id, "at_ms": now_ms}
    )
    metadata["agent_referrals"] = referrals[-_MAX_AGENT_REFERRALS:]
    return True
