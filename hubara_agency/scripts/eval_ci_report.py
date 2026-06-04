"""Genera un reporte visual (Markdown) de los unit eval para GitHub Actions.

Muestra QUÉ se evaluó, no solo cuántos tests pasaron:
  * resultados del run (passed/failed/skipped, parseado del JUnit XML),
  * las 9 métricas de calidad del Asesor de Ventas (qué verifica cada una),
  * el golden dataset (las conversaciones de regresión: escenario + resultado esperado),
  * los invariantes deterministas del guion (saludo, voseo, cierres, emojis) leídos
    de la rúbrica real (`script_rubric`) — siempre en sync.

Se escribe a stdout → el workflow lo manda al Job Summary y a un comentario del PR.

Uso:  uv run python scripts/eval_ci_report.py [eval-results.xml]
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# hubara_agency en sys.path para importar la rúbrica real (sin deepeval).
_HUBARA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HUBARA))

from src.plugins.chats.agent.sales_eval.evals import script_rubric as R  # noqa: E402

_GOLDENS = _HUBARA / "tests" / "evals" / "goldens" / "sales" / "curated.json"

# Catálogo de métricas (presentación). Espeja `evals/metrics.py`.
_METRICS = [
    ("greeting_compliance", "La apertura saluda por hora de Colombia + marca «Hubara» y NO usa «¡Hola!»/«Hey»", "determinista", "1.0"),
    ("style_compliance", "Cero voseo rioplatense, cero em dash, ≤1 emoji (allowlist), sin frases de cierre prohibidas", "determinista", "1.0"),
    ("script_adherence", "Sigue el funnel de 6 fases (apertura → descubrimiento → recomendación → objeciones → cierre → despedida)", "LLM-juez", "0.7"),
    ("proactive_offering", "Siempre ofrece lo que debe (catálogo / productos / siguiente paso)", "LLM-juez", "0.7"),
    ("no_hallucination", "Solo afirma productos/precios/aromas que vinieron de una tool (cero inventos)", "LLM-juez", "0.8"),
    ("conversion_progress", "Avanza hacia el cierre cuando hay intención, sin presionar", "LLM-juez", "0.6"),
    ("correct_handoff", "Escala a humano en los disparadores correctos (descuento, B2B, salud, pedido de humano…)", "LLM-juez", "0.7"),
    ("role_adherence", "Se mantiene en el rol de Asesor de Ventas de Hubara", "LLM-juez", "0.7"),
    ("knowledge_retention", "No repregunta datos que el cliente ya dio (anti context-leak)", "LLM-juez", "0.7"),
]


def _parse_junit(path: Path) -> tuple[int, int, int, list[tuple[str, str, str]]]:
    """Devuelve (passed, failed, skipped, [(file, test, status)])."""
    if not path.exists():
        return 0, 0, 0, []
    root = ET.parse(path).getroot()
    passed = failed = skipped = 0
    rows: list[tuple[str, str, str]] = []
    for case in root.iter("testcase"):
        cls = case.get("classname", "").split(".")[-1]
        name = case.get("name", "")
        if not cls:  # skip a nivel módulo → classname vacío; derivar del name
            cls = name.split(".")[-1]
            name = "(módulo completo — requiere juez LLM vivo, se saltea en CI)"
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
            status = "❌"
        elif case.find("skipped") is not None:
            skipped += 1
            status = "⏭️"
        else:
            passed += 1
            status = "✅"
        rows.append((cls, name, status))
    return passed, failed, skipped, rows


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    xml_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _HUBARA.parent / "eval-results.xml"
    passed, failed, skipped, rows = _parse_junit(xml_path)
    out: list[str] = []

    # --- Encabezado + resultados ---
    badge = "🟢 PASÓ" if failed == 0 else "🔴 FALLÓ"
    out.append("# 🎯 Reporte de Unit Eval — Calidad del Asesor de Ventas")
    out.append("")
    out.append(f"### {badge} · ✅ {passed} passed · ⏭️ {skipped} skipped · ❌ {failed} failed")
    out.append("")
    if rows:
        by_file: dict[str, list[tuple[str, str]]] = {}
        for cls, name, status in rows:
            by_file.setdefault(cls, []).append((name, status))
        out.append("<details><summary>Detalle por test (click para expandir)</summary>\n")
        for cls, tests in by_file.items():
            ok = sum(1 for _, s in tests if s == "✅")
            out.append(f"\n**`{cls}`** — {ok}/{len(tests)} ✅\n")
            out.append("| Test | |")
            out.append("|---|:--:|")
            for name, status in tests:
                out.append(f"| {_md_escape(name)} | {status} |")
        out.append("\n</details>")
    out.append("")

    # --- Qué evalúa: las 9 métricas ---
    out.append("## 🧪 Qué evalúa el harness — 9 métricas de calidad")
    out.append("")
    out.append("| Métrica | Qué verifica | Tipo | Umbral |")
    out.append("|---|---|:--:|:--:|")
    for key, desc, tipo, thr in _METRICS:
        out.append(f"| `{key}` | {_md_escape(desc)} | {tipo} | {thr} |")
    out.append("")
    out.append("> En **CI** corren las **deterministas** (sin juez LLM, estables). Las **LLM-juez** "
               "corren contra el proxy litellm en el harness online (Superficie 2 → SigNoz).")
    out.append("")

    # --- Golden dataset ---
    if _GOLDENS.exists():
        goldens = json.loads(_GOLDENS.read_text(encoding="utf-8"))
        out.append(f"## 📋 Golden dataset — {len(goldens)} conversaciones de regresión")
        out.append("")
        out.append("Las conversaciones **ejemplares** contra las que se valida. Cada fallo de "
                   "producción se cura y se suma acá.")
        out.append("")
        out.append("| Caso | Escenario | Resultado esperado |")
        out.append("|---|---|---|")
        for g in goldens:
            name = g.get("name", "?")
            scen = _md_escape(g.get("scenario", ""))[:90]
            exp = _md_escape(g.get("expected_outcome", ""))[:160]
            out.append(f"| `{name}` | {scen} | {exp} |")
        out.append("")

    # --- Invariantes deterministas (de la rúbrica real) ---
    out.append("## 📐 Invariantes deterministas del guion (chequeados sin juez)")
    out.append("")
    out.append("- **Saludo válido en la apertura**: `Buenos días/tardes/noches` + marca `Hubara`.")
    out.append("- **Aperturas prohibidas**: «¡Hola!», «Hey», «Buen día».")
    out.append(f"- **Voseo prohibido** ({len(R.VOSEO_TOKENS)} marcadores): "
               + ", ".join(f"`{t}`" for t in R.VOSEO_TOKENS[:12]) + "…")
    out.append("- **Cierres prohibidos**: «gracias por tu compra», «compra realizada/exitosa», "
               "«tu pago fue procesado», «conversación cerrada», «caso cerrado»…")
    out.append("- **Em dash / en dash** (— –): prohibidos en respuestas al cliente.")
    out.append(f"- **Emojis permitidos** (máx 1 por burbuja): {' '.join(sorted(R.ALLOWED_EMOJIS))}")
    out.append("")
    out.append("---")
    out.append("<sub>Generado por `scripts/eval_ci_report.py` · harness `sales_eval` · "
               "fuente del guion: `workspace/skills/sales_script/SKILL.md`</sub>")

    print("\n".join(out))


if __name__ == "__main__":
    main()
