"""Guard anti-voseo: REGLA #1 de IDENTITY.md (dialecto colombiano, INNEGOCIABLE).

El agente de ventas Hubara es una marca colombiana y su identidad PROHÍBE el
voseo rioplatense. El LLM respeta la regla, pero los **strings hardcodeados en
Python** (tails de render, fallbacks, descripciones de tools, docstrings,
comentarios) la bypassean — y se colaron voseos que el cliente terminaba viendo
(bug run fe86d4e4: "Decime cuál te gusta" en el variant picker, "¿Me lo
escribís?" en el fallback de audio, etc.).

Este test es el guard durable equivalente a `ruff --select F821` para la clase
de import faltante: escanea TODO el árbol del agente chats (sales + remarketing)
y falla si aparece cualquier forma voseo del denylist curado.

El denylist es CURADO para evitar falsos positivos: solo incluye formas que NO
tienen homógrafo legítimo en español estándar (tuteo / 3ª persona / infinitivo /
participio). Por eso usamos fronteras de palabra unicode-aware: p.ej. `\\busá\\b`
NO matchea "usando"/"usaba"/"usar", y `\\btomá\\b` NO matchea "automático".

Si agregás un string nuevo en el agente, escribilo en TUTEO colombiano
("dime", "cuéntame", "puedes", "quieres", "usa", "mira") — no en voseo.
Tabla de conversión completa: IDENTITY.md §"REGLA #1".
"""
from __future__ import annotations

import re
from pathlib import Path

# Raíz del árbol del agente chats. Este archivo vive en
# tests/plugins/chats/ → parents[3] == hubara_agency/.
_AGENT_ROOT = Path(__file__).resolve().parents[3] / "src" / "plugins" / "chats" / "agent"

# Formas voseo INEQUÍVOCAS (sin homógrafo legítimo en tuteo/3ª/infinitivo).
# Excluimos deliberadamente formas ambiguas con 1ª persona pretérito
# ("escribí", "pedí", "elegí", "seguí", "viví", "describí") porque "yo escribí"
# es español estándar correcto.
_VOSEO_DENYLIST = [
    # Pronombre
    "vos",
    # Presente indicativo voseo (-ás / -és / -ís)
    "tenés", "querés", "podés", "sabés", "pensás", "contás", "pasás", "usás",
    "llevás", "dejás", "mirás", "recibís", "preferís", "compartís", "decís",
    "vivís", "escribís", "parseás",
    # Imperativos afirmativos voseo (tilde en última sílaba)
    "esperá", "mirá", "mandá", "llamá", "usá", "empezá", "cerrá", "informá",
    "avisá", "invitá", "continuá", "reasoná", "considerá", "tocá", "recordá",
    "aguantá", "volvé", "poné", "hacé", "andá", "contá", "dejá", "recortá",
    "fijá", "tomá",
    # Imperativos con enclítico (voseo)
    "decime", "contame", "mirame", "mostrame", "avisame", "pedile", "mandale",
    "decile", "contale", "preguntale", "fijate", "acordate", "llevate",
    "sentate", "pegale", "invitalo", "describílos",
]

# Fronteras unicode-aware: el token no puede estar pegado a otra letra
# (incluye acentuadas/ñ). Así `usá` no matchea dentro de "usando" ni `tomá`
# dentro de "automático".
_WORDCHAR = r"[\wáéíóúñÁÉÍÓÚÑ]"
_PATTERN = re.compile(
    rf"(?<!{_WORDCHAR})(?:" + "|".join(re.escape(t) for t in _VOSEO_DENYLIST) + rf")(?!{_WORDCHAR})",
    re.IGNORECASE,
)


# Archivos EXENTOS: contienen vocabulario voseo como DATO de un detector (la
# rúbrica de evals caza voseo en las respuestas del agente, igual que este
# guard) — ese vocabulario nunca es texto customer-facing. Cualquier entrada
# nueva acá requiere la misma justificación: "es denylist de un detector".
_DETECTOR_VOCABULARY_EXEMPT = {
    "sales_eval/evals/script_rubric.py",
}


def _iter_agent_py_files():
    assert _AGENT_ROOT.is_dir(), f"no existe el árbol del agente: {_AGENT_ROOT}"
    # workspace/ son .md (no .py); rglob("*.py") ya los excluye.
    return sorted(
        p
        for p in _AGENT_ROOT.rglob("*.py")
        if str(p.relative_to(_AGENT_ROOT)).replace("\\", "/")
        not in _DETECTOR_VOCABULARY_EXEMPT
    )


def test_no_voseo_in_chats_agent_python_strings() -> None:
    """Falla si cualquier .py del agente chats contiene voseo rioplatense.

    Anti-regresión bug run fe86d4e4. Cubre strings customer-facing,
    descripciones de tools (LLM-facing), docstrings y comentarios — todo el
    texto en español del árbol del agente debe ser tuteo colombiano.
    """
    violations: list[str] = []
    for path in _iter_agent_py_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _PATTERN.finditer(line):
                rel = path.relative_to(_AGENT_ROOT.parents[3])
                violations.append(f"{rel}:{lineno}: voseo '{match.group(0)}' → {line.strip()[:100]}")

    assert not violations, (
        "Voseo rioplatense detectado (viola REGLA #1 de IDENTITY.md — usá tuteo "
        "colombiano: 'dime', 'cuéntame', 'puedes', 'usa', 'mira'):\n  "
        + "\n  ".join(violations)
    )
