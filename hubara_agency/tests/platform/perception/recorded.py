"""Respuestas grabadas de los dos proveedores reales del puerto de percepción.

Forma tomada de la referencia pública de cada API (verificada el 2026-09-23):

* **Decisions API de OpenRouter** (`POST /api/alpha/decisions`, Jev de
  TypeSafe): `answers` por clave de pregunta; `noul` trae la probabilidad de
  sí, `choice` la opción con `probabilities` y `confidence`, `score` la
  posición en la rúbrica con `probabilities` por nivel. La API está en alpha:
  el PR 5 reemplaza estas respuestas por una grabación real (plan §6, PR 5).
* **Chat completions con logprobs** (OpenAI por OpenRouter vía LiteLLM): cada
  respuesta es un código de un token; la probabilidad sale de `top_logprobs`.

Las claves de pregunta que viajan son `q0`, `q1`… (el adaptador las mapea a
los ids del puerto): así un id con puntos o acentos nunca depende de lo que
acepte una API alpha.
"""
from __future__ import annotations

from src.platform.perception.ports import TypedQuestion

QUESTIONS: tuple[TypedQuestion, ...] = (
    TypedQuestion(
        id="topic.catalogo",
        kind="noul",
        text="¿El cliente pide ver el catálogo o las opciones?",
        criteria={"true": "Pide ver productos, fotos o el catálogo.", "false": "No pide ver productos."},
    ),
    TypedQuestion(
        id="stage",
        kind="choice",
        text="¿En qué etapa de la compra está el cliente?",
        criteria={
            "descubrimiento": "Todavía no eligió producto.",
            "variantes": "Eligió producto y falta aroma, color o cantidad.",
            "confirmacion": "Tiene todo elegido y falta que confirme.",
        },
    ),
    TypedQuestion(
        id="urgencia",
        kind="score",
        text="¿Qué tan pronto necesita el pedido?",
        criteria=("Sin fecha", "Esta semana", "Hoy mismo"),
    ),
)

DECISIONS_OK: dict = {
    "id": "gen-dec-0000000000-TEST",
    "model": "typesafe/jev-1.13-20260917",
    "provider": "TypeSafe",
    "answers": {
        "q0": {"type": "noul", "noul": 0.93},
        "q1": {
            "type": "choice",
            "choice": "variantes",
            "confidence": 0.7,
            "probabilities": {"descubrimiento": 0.1, "variantes": 0.85, "confirmacion": 0.05},
        },
        "q2": {
            "type": "score",
            "score": 1.2,
            "confidence": 0.8,
            "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3},
            "legend": {"0": "Sin fecha", "1": "Esta semana", "2": "Hoy mismo"},
        },
    },
    "usage": {"input_tokens": 480, "output_tokens": 70, "cost": 0.00002},
}


def _lp(token: str, logprob: float, top: dict[str, float]) -> dict:
    return {
        "token": token,
        "logprob": logprob,
        "top_logprobs": [{"token": t, "logprob": v} for t, v in top.items()],
    }


# Respuesta "S\nB\n1": sí al catálogo, la opción B (variantes), nivel 1.
LOGPROBS_OK: dict = {
    "model": "openai/gpt-4o-mini-2024-07-18",
    "choices": [
        {
            "message": {"role": "assistant", "content": "S\nB\n1"},
            "finish_reason": "stop",
            "logprobs": {
                "content": [
                    _lp("S", -0.07, {"S": -0.07, "N": -2.7, "Si": -6.0}),
                    _lp("\n", -0.001, {"\n": -0.001}),
                    _lp("B", -0.16, {"B": -0.16, "A": -2.3, "C": -3.0, "D": -9.0}),
                    _lp("\n", -0.001, {"\n": -0.001}),
                    _lp("1", -0.5, {"1": -0.5, "2": -1.2, "0": -2.3}),
                ]
            },
        }
    ],
    "usage": {"prompt_tokens": 520, "completion_tokens": 5},
}
