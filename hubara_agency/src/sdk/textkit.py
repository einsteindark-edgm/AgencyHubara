"""TextKit — guards PUROS del texto LLM→cliente, importables desde una TOOL.

Por qué es un kit aparte (run b06636a6): parte de estos guards también sale por
`src.sdk.agentkit`, pero ese kit trae el turn loop (`workflow_helpers` →
`temporalio`) y el contrato R-DIP prohíbe que `tools/*.py` importe Temporal
(ADR-001: la tool es inerte). Una tool que recibe texto para el cliente en un
param tipado (`customer_message` — la regla de L-20) no tenía camino limpio
hacia ellos. Este kit depende SOLO de `src.platform.llm_text_sanitizer`, que es
stdlib-only (guard: `tests/platform/test_textkit.py`).

DÓNDE se valida el texto importa tanto como con qué (L-21): un regex cuyo
veredicto decide commands DENTRO de un workflow es lógica de replay. La tool
corre en una activity y su resultado queda grabado en la history → validá ACÁ
y que el workflow solo lea el resultado.

Uso canónico (en una tool con `customer_message`)::

    from src.sdk.textkit import keep_customer_safe_sentences, sanitize_llm_text

    safe = keep_customer_safe_sentences(sanitize_llm_text(customer_message).text)
    # "" = el modelo habló y nada era seguro → la tool lo DECLARA vacío.

Workers y workflows siguen usando `src.sdk.agentkit` (los que re-exporta son
los mismos objetos). `keep_customer_safe_sentences` y `breaks_human_persona`
salen SOLO por acá: su lugar es la tool, no el workflow.
"""
from __future__ import annotations

# Alias idiom (regla 1 SDK): sin el `as x`, ruff --fix poda el re-export.
from src.platform.llm_text_sanitizer import (
    breaks_human_persona as breaks_human_persona,
    keep_customer_safe_sentences as keep_customer_safe_sentences,
    looks_like_admin_leak as looks_like_admin_leak,
    salvage_customer_text as salvage_customer_text,
    sanitize_llm_text as sanitize_llm_text,
)
