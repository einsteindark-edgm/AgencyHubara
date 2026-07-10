"""Formato de texto outbound: markdown del LLM → formato nativo WhatsApp.

WhatsApp usa `*bold*` (UN asterisco) y `_italic_` (UN guion bajo); los LLM
tienden a emitir markdown (`**bold**`, `__italic__`) a pesar del prompt, y
el cliente ve los símbolos crudos (caso wa_573125671604: `**Banco**:
Bancolombia`). Esta conversión es determinista y idempotente — texto ya en
formato WhatsApp pasa intacto.
"""
from __future__ import annotations

import re

# Pares **...** / __...__ sin cruzar saltos de línea (un span de markdown
# no abarca párrafos) y sin tocar asteriscos sueltos (`2 * 3 = 6`).
_MD_BOLD = re.compile(r"\*\*(?!\s)([^*\n]+?)(?<!\s)\*\*")
_MD_ITALIC = re.compile(r"__(?!\s)([^_\n]+?)(?<!\s)__")


def to_whatsapp_text(text: str) -> str:
    """Convierte bold/italic de markdown al formato nativo de WhatsApp."""
    if not text or ("**" not in text and "__" not in text):
        return text
    text = _MD_BOLD.sub(r"*\1*", text)
    text = _MD_ITALIC.sub(r"_\1_", text)
    return text
