"""LLM on-demand summary del cliente.

Diseño:
  * NO se usa para SCORING — el scoring es determinístico (rules.yaml).
  * Se invoca SOLO cuando el operador clickea "Resumir cliente con IA" en
    el panel. La respuesta NO es cacheada por sesión — siempre fresh.
  * Toma como input: el score determinístico + episodes + último episodio
    activo. Output: 2-3 oraciones en español que el operador puede usar
    para retomar la conversación o priorizar follow-ups.
  * Determinístico-ish: temperature=0.3 + prompt estructurado. La idea es
    síntesis, no creatividad.

R-DET / R-JSON: el adapter es async; el caller (endpoint) lo invoca
directo sin pasar por Temporal. NO se usa en flujos de workflow — es UI.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import litellm

from src.platform.config import (
    API_BASE_LLMLITE,
    CUSTOMER_SUMMARY_MODEL,
    LITELLM_API_KEY,
)
from src.platform.customer_scoring.port import CustomerScore

log = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """Eres un asistente comercial. Generá un resumen MUY breve (2-3 oraciones)
del cliente para que el operador humano sepa cómo retomar la conversación o
priorizar el seguimiento. Tono profesional, conciso, sin emojis. Español rioplatense
neutro. NO inventes datos que no estén abajo.

DATOS DEL CLIENTE
=================
Tag: {tag}
Score: {score_letter} ({score_value}/100) — {score_reason}
Valor total acumulado: ${monetary_cop:,} COP
Última compra: {last_purchase}

EPISODIOS ({episodes_count} en total):
{episodes_summary}

NOTAS DEL OPERADOR (si las hay):
{notes}

INSTRUCCIONES
=============
- Si es VIP/Recurrente: enfatizá la relación + sugerí una acción concreta.
- Si es Nuevo: mencionalo + sugerí qué información falta validar.
- Si es Frío/Bajo score: sugerí estrategia de reactivación si aplica.
- Si no hay datos suficientes: decilo explícitamente — NO inventes.

Devolvé SOLO el resumen, sin preámbulo ("Aquí va...", "Resumen:") ni cierres.
"""


@dataclass(frozen=True)
class CustomerSummaryResult:
    """Resultado del LLM summary — JSON-serializable."""
    summary: str
    model: str
    latency_ms: int
    rules_version: int        # del score de input — auditoría
    error_detail: str | None  # None si OK; mensaje si fallback


class CustomerSummaryAdapter:
    """Adapter que llama litellm.acompletion para generar el summary.

    Default model + api_base vienen del config global (mismo patrón que el
    audio adapter). El caller puede override en composition para tests /
    A/B testing de modelos.
    """

    def __init__(
        self,
        *,
        model: str = CUSTOMER_SUMMARY_MODEL,
        api_base: str | None = API_BASE_LLMLITE,
        api_key: str | None = LITELLM_API_KEY,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._timeout = timeout_seconds

    async def summarize(
        self,
        *,
        score: CustomerScore,
        metadata: dict[str, Any],
    ) -> CustomerSummaryResult:
        """Generar el summary. Si litellm falla → devuelve summary fallback
        con error_detail (NO levanta — el endpoint debe degradar gracefully).
        """
        prompt = _build_prompt(score, metadata)
        started = time.time()

        try:
            response = await litellm.acompletion(
                model=self._model,
                api_base=self._api_base,
                api_key=self._api_key,
                temperature=0.3,
                max_tokens=300,  # 2-3 oraciones máx
                timeout=self._timeout,
                messages=[
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001 — transport/5xx/rate-limit
            elapsed_ms = int((time.time() - started) * 1000)
            err_type = type(exc).__name__
            log.warning(
                "customer_summary: litellm call failed (%s: %s) — fallback summary",
                err_type, exc,
            )
            return CustomerSummaryResult(
                summary=_fallback_summary(score),
                model=self._model,
                latency_ms=elapsed_ms,
                rules_version=score.rules_version,
                error_detail=f"{err_type}: {exc}"[:300],
            )

        elapsed_ms = int((time.time() - started) * 1000)
        try:
            content = response["choices"][0]["message"]["content"]
            summary = str(content).strip()
        except (KeyError, IndexError, TypeError):
            log.warning(
                "customer_summary: respuesta litellm con shape inesperado: %r",
                response,
            )
            return CustomerSummaryResult(
                summary=_fallback_summary(score),
                model=self._model,
                latency_ms=elapsed_ms,
                rules_version=score.rules_version,
                error_detail="malformed_llm_response",
            )

        if not summary:
            return CustomerSummaryResult(
                summary=_fallback_summary(score),
                model=self._model,
                latency_ms=elapsed_ms,
                rules_version=score.rules_version,
                error_detail="empty_llm_response",
            )

        return CustomerSummaryResult(
            summary=summary,
            model=self._model,
            latency_ms=elapsed_ms,
            rules_version=score.rules_version,
            error_detail=None,
        )


def _build_prompt(score: CustomerScore, metadata: dict[str, Any]) -> str:
    """Construye el prompt determinístico. NO incluye datos sensibles
    (teléfono, dirección) — solo síntesis de historia."""
    episodes = metadata.get("episodes") or []
    episodes_lines: list[str] = []
    for ep in episodes[-5:]:  # máximo los últimos 5 — evita prompts gigantes
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get("episode_id", "—")
        closing = ep.get("closing_tag") or "ACTIVO"
        motivo = ep.get("closing_motivo") or ""
        order = ep.get("order_id") or ""
        line = f"- {ep_id}: cierre={closing}"
        if motivo:
            line += f" · motivo='{motivo}'"
        if order:
            line += f" · order_id={order}"
        episodes_lines.append(line)
    episodes_summary = "\n".join(episodes_lines) if episodes_lines else (
        "(sin episodios registrados)"
    )

    # Notas del operador (status_history motivos únicos legacy + episodes motivos).
    notes_set: set[str] = set()
    for ep in episodes:
        if isinstance(ep, dict) and ep.get("closing_motivo"):
            notes_set.add(str(ep["closing_motivo"]))
    motivo_root = metadata.get("motivo")
    if isinstance(motivo_root, str) and motivo_root:
        notes_set.add(motivo_root)
    notes = "\n".join(f"- {n}" for n in sorted(notes_set)) if notes_set else "(sin notas)"

    last_purchase = (
        f"hace ~{score.last_purchase_at_ms // 86400000} días" if score.last_purchase_at_ms
        else "nunca"
    )

    return _PROMPT_TEMPLATE.format(
        tag=score.tag,
        score_letter=score.score_letter,
        score_value=score.score_value,
        score_reason=score.score_reason,
        monetary_cop=score.monetary_cop,
        last_purchase=last_purchase,
        episodes_count=len(episodes),
        episodes_summary=episodes_summary,
        notes=notes,
    )


def _fallback_summary(score: CustomerScore) -> str:
    """Summary determinístico cuando el LLM no respondió. Mejor que cadena
    vacía — el operador igual ve algo útil."""
    return (
        f"Cliente {score.tag.lower()} ({score.score_letter}). "
        f"{score.score_reason}. Valor histórico: ${score.monetary_cop:,} COP."
    )
