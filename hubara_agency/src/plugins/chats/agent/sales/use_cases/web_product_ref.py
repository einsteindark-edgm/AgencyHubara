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
    metadata: dict, *, sku: str, source: str | None, now_ms: int
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
        "status": "pending",
        "detected_at_ms": now_ms,
        # Episode-scoped, like the web cart: a note about a product seen weeks
        # ago must not follow the customer into a new conversation.
        "episode_id": episode_id,
    }
    return True


def mark_web_product_resolved(metadata: dict, *, handle: str, title: str) -> None:
    state = metadata.setdefault("web_product_ref", {})
    state["status"] = "resolved"
    state["handle"] = handle
    state["title"] = title


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

    return (
        _NOTE_HEADER
        + f"El cliente llego a WhatsApp desde la pagina de {state.get('title')} "
        f"(handle {state.get('handle')}, SKU {state.get('sku')}). "
        "Ese es el producto que le interesa: confirmalo con una frase y sigue "
        "con la venta (aroma, color o signo si aplica, cantidad, datos de "
        "envio). No le preguntes de nuevo que producto busca."
    )
