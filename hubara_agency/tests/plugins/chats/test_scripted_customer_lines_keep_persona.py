"""Guard: los guiones que NOSOTROS le dictamos al agente no rompen la persona.

Run 5ed9af2d (2026-09-18): el cliente leyó "…quedó en manos del equipo humano".
Al auditar los prompts apareció que el vocabulario no lo inventó el modelo: lo
dictábamos nosotros — `etapa_cierre/SKILL.md` y el envelope de `register_order`
le ordenaban despedirse con *"Tu pedido quedó tomado y un humano te confirma en
unos minutos 🤍"*. Regla del operador: el relevo se nombra "un colega / un
compañero del equipo"; el cliente nunca debe notar cuándo lo atiende el bot y
cuándo una persona.

Qué cuenta como LÍNEA DICTADA (texto que el modelo va a copiar casi literal):
  * workspace `.md`: cursiva-con-comillas *"…"*, el ejemplo entre paréntesis
    de las tablas ("…") y `customer_message="…"`
  * strings de Python del agente: `customer_message='…'` y el giro
    "despide/dile/responde … con: '…'".
La guía interna ("un colega verifica el pago en el dashboard") NO es línea
dictada y no se audita acá: el cliente no la lee.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from src.platform.llm_text_sanitizer import breaks_human_persona

_HUBARA_ROOT = Path(__file__).resolve().parents[3]
_AGENT_ROOT = _HUBARA_ROOT / "src" / "plugins" / "chats" / "agent"
_PLATFORM_TOOLS = _HUBARA_ROOT / "src" / "platform" / "tools"

_MD_SCRIPTED = (
    re.compile(r"\*[\"“]([^\"”\n]{8,})[\"”]\*"),
    re.compile(r"\([\"“]([^\"”\n]{8,})[\"”]\)"),
    re.compile(r"customer_message\s*=\s*[\"“]([^\"”\n]{8,})[\"”]"),
)
# Piso: si la convención cambia y el guard deja de VER líneas, que falle en vez
# de pasar en vacío (hoy audita bastante más que esto).
_MIN_MD_LINES_AUDITED = 15
_PY_SCRIPTED = (
    re.compile(r"customer_message\s*=\s*['\"“]([^'\"”]{8,})['\"”]"),
    re.compile(
        r"(?:despide|desp[ií]dete|dile|responde|resp[oó]ndele)[^:'\"]{0,40}"
        r"(?:con)?\s*:\s*['\"“]([^'\"”]{8,})['\"”]",
        re.IGNORECASE,
    ),
)


def _py_string_constants(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_scripted_lines_in_agent_workspaces_keep_persona() -> None:
    violations: list[str] = []
    audited = 0
    md_files = sorted(_AGENT_ROOT.rglob("workspace/**/*.md"))
    assert md_files, f"no encontré workspaces bajo {_AGENT_ROOT}"
    for path in md_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in _MD_SCRIPTED:
                for match in pattern.finditer(line):
                    audited += 1
                    if breaks_human_persona(match.group(1)):
                        rel = path.relative_to(_HUBARA_ROOT)
                        violations.append(f"{rel}:{lineno}: {match.group(1)[:100]}")

    assert audited >= _MIN_MD_LINES_AUDITED, (
        f"el guard solo vio {audited} líneas dictadas: ¿cambió la convención "
        "de los guiones? Actualiza _MD_SCRIPTED."
    )
    assert not violations, (
        "Línea dictada que rompe la persona (nombra el relevo como 'un colega "
        "del equipo'):\n  " + "\n  ".join(violations)
    )


def test_scripted_lines_in_agent_python_strings_keep_persona() -> None:
    violations: list[str] = []
    py_files = sorted(_AGENT_ROOT.rglob("*.py")) + sorted(_PLATFORM_TOOLS.rglob("*.py"))
    for path in py_files:
        for lineno, text in _py_string_constants(path):
            for pattern in _PY_SCRIPTED:
                for match in pattern.finditer(text):
                    if breaks_human_persona(match.group(1)):
                        rel = path.relative_to(_HUBARA_ROOT)
                        violations.append(f"{rel}:{lineno}: {match.group(1)[:100]}")

    assert not violations, (
        "Línea dictada que rompe la persona (nombra el relevo como 'un colega "
        "del equipo'):\n  " + "\n  ".join(violations)
    )
