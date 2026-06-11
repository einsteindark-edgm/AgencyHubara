"""Auto-curación de goldens — la pieza que mejora lo que hace Confident AI.

Confident AI (cloud) solo COLECTA trazas de producción en un dataset. Acá vamos
un paso más: para una conversación de bajo score, el LLM-juez **redacta el
`expected_outcome` ideal** (qué debió hacer el asesor según el guion), de modo
que el curador humano solo APRUEBA/edita en vez de escribir desde cero. El caso
real de prod se convierte así, casi sin fricción, en un golden de regresión.

Privacidad: los turnos que se persisten en el candidato ya vienen REDACTADOS
(el reconstructor aplica `redact_pii`). El humano hace la redacción final antes
de promover a `curated.json`.

`deepeval` no se importa acá (trabajamos con dicts de turnos + el juez ya
construido). Funciones puras salvo `propose_expected_outcome` (llama al juez).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.evals import script_rubric as R


def _render_transcript(turns: list[dict[str, Any]]) -> str:
    lines = []
    for t in turns:
        who = "Cliente" if t.get("role") == "user" else "Asesor"
        tools = t.get("tools") or []
        suffix = f"  [tools: {', '.join(tools)}]" if tools else ""
        lines.append(f"{who}: {t.get('content', '')}{suffix}")
    return "\n".join(lines)


def build_golden_draft_prompt(
    turns: list[dict[str, Any]], failed: list[tuple[str, float, str]]
) -> str:
    """Prompt para que el juez redacte el `expected_outcome` ideal de esta charla."""
    fails = "\n".join(
        f"- {name} (score {score:.2f}): {reason}" for name, score, reason in failed
    ) or "- (sin detalle por métrica)"
    return (
        "Eres un auditor de calidad del Asesor de Ventas de Hubara. Abajo hay una "
        "conversación REAL de WhatsApp (PII ya redactada) que obtuvo bajo puntaje en "
        "la evaluación, junto con el guion y las métricas que falló.\n\n"
        f"GUION (resumen):\n{R.SCRIPT_CONTEXT}\n\n"
        f"CONVERSACIÓN:\n{_render_transcript(turns)}\n\n"
        f"MÉTRICAS QUE FALLÓ:\n{fails}\n\n"
        "TAREA: redacta en 2 a 4 oraciones el RESULTADO ESPERADO ideal de esta "
        "conversación según el guion: qué debió hacer el asesor (saludo, "
        "descubrimiento, oferta, tools correctas, cierre, escalación) para que "
        "fuera ejemplar. NO reescribas toda la conversación turno por turno; "
        "describe el comportamiento esperado de forma concisa, accionable y "
        "verificable. Responde SOLO con el resultado esperado, sin preámbulo."
    )


async def propose_expected_outcome(
    judge: Any, turns: list[dict[str, Any]], failed: list[tuple[str, float, str]]
) -> str:
    """Llama al juez para redactar el `expected_outcome`. Degrada a '' si falla."""
    prompt = build_golden_draft_prompt(turns, failed)
    try:
        result = await judge.a_generate(prompt)
    except Exception:  # noqa: BLE001 — la curación no debe romper la evaluación
        return ""
    # LiteLLMModel.a_generate → Tuple[str|BaseModel, float]; toleramos ambas formas.
    text = result[0] if isinstance(result, tuple) else result
    return str(text).strip()


def build_candidate_golden(
    *,
    session_id: str,
    turns: list[dict[str, Any]],
    scenario: str,
    expected_outcome: str,
    scores: list[tuple[str, float, bool, str]],
    episode_id: str = "",
) -> dict[str, Any]:
    """Arma el dict del candidato a golden (shape `ConversationalGolden` + metadata).

    `turns` deben venir ya redactados. `scores` = [(metric_key, score, success, reason)].
    `episode_id` ata el candidato al episodio que falló (vacío = sesión entera
    legacy) — es lo que permite que la UI muestre QUÉ conversación se va a
    convertir en golden, no solo el número de WhatsApp.
    """
    return {
        "scenario": scenario,
        "expected_outcome": expected_outcome,
        # Turns en el shape que `add_goldens_from_json_file` espera (role/content).
        "turns": [{"role": t["role"], "content": t["content"]} for t in turns],
        "additional_metadata": {
            "source": "auto_curation",
            "source_session_redacted": session_id,
            "source_episode": episode_id,
            "status": "needs_human_review",
            "failed_metrics": [
                {"metric": k, "score": s, "success": ok, "reason": r}
                for (k, s, ok, r) in scores
                if not ok
            ],
            "all_scores": [
                {"metric": k, "score": s, "success": ok} for (k, s, ok, _) in scores
            ],
        },
    }


def write_candidate(
    candidates_dir: Path, unit_id: str, golden: dict[str, Any]
) -> Path:
    """Escribe el candidato a `<candidates_dir>/<safe_unit>.json`. Idempotente
    POR UNIDAD: un re-eval pisa el candidato previo del MISMO episodio
    (`wa_x::ep_002` → `wa_x__ep_002.json`), pero episodios distintos de la
    misma sesión conviven como candidatos separados."""
    candidates_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "+-_" else "_" for c in unit_id)
    path = candidates_dir / f"{safe}.json"
    path.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
