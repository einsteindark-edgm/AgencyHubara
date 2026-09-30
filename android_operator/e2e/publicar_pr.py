"""Arma el comentario del PR con lo que probó la QA en emulador y achica las capturas para publicarlas.

Lo corre el job «Capturas en el PR» de .github/workflows/qa-emulador.yml sobre el artefacto de la corrida:

  python3 publicar_pr.py --qa <artefacto> --dest <rama de capturas> --pr 385 --sha <sha> \\
      --base-url https://raw.githubusercontent.com/<repo>/<rama> --run-url <corrida> > comentario.md

Las capturas quedan en <dest>/pr-<N>/<sha corto>/<escenario>.jpg (360 px de ancho). Si un escenario tomó una
captura a mitad de camino (paso `shot`), se publica esa: muestra mejor lo que se probó que la pantalla final.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
MARK = "<!-- qa-emulador -->"
PER_ROW = 5
WIDTH = 360


def pick_shot(qa: Path, sid: str) -> Path | None:
    mid = sorted(qa.glob(f"{sid}.*.png"))
    final = qa / f"{sid}.png"
    return mid[0] if mid else (final if final.exists() else None)


def thumb(src: Path, dest: Path) -> None:
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        im = im.resize((WIDTH, round(im.height * WIDTH / im.width)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "JPEG", quality=82, optimize=True)


def short_id(sid: str) -> str:
    return sid.split("_", 1)[0]


def comment(report: list[dict] | None, summaries: dict[str, str], images: dict[str, str], sha: str, run_url: str) -> str:
    head = "App Operador en un emulador Android 11 de GitHub, contra el backend de esta rama con datos sintéticos."
    if report is None:
        return "\n".join([MARK, "### 📱 QA en emulador: no llegó a correr los escenarios", "",
                          f"{head} La corrida falló antes de los escenarios. [Ver la corrida]({run_url})."])
    ran = [r for r in report if not r.get("skipped")]
    passed = [r for r in ran if r.get("passed")]
    icon = "✅" if len(passed) == len(ran) else "❌"
    lines = [MARK, f"### 📱 QA en emulador: {len(passed)} de {len(ran)} escenarios pasan {icon}", "",
             f"{head} Un guion por adb recorre la app (sin IA) y chequeos deterministas deciden: lo que el backend "
             f"envió por WhatsApp, el estado en el API y lo que hay en pantalla. Commit `{sha[:7]}` · "
             f"[ver la corrida]({run_url})", ""]
    for i in range(0, len(ran), PER_ROW):
        chunk = ran[i:i + PER_ROW]
        lines.append("| " + " | ".join(f"{'✅' if r['passed'] else '❌'} {short_id(r['id'])}" for r in chunk) + " |")
        lines.append("|" + ":-:|" * len(chunk))
        lines.append("| " + " | ".join(f'<img src="{images[r["id"]]}" width="160">' if r["id"] in images else "—"
                                       for r in chunk) + " |")
        lines.append("| " + " | ".join(f"<sub>{r['title']}</sub>" for r in chunk) + " |")
        lines.append("")
    lines += ["**Qué probó cada escenario**", ""]
    for r in report:
        sid = short_id(r["id"])
        if r.get("skipped"):
            lines.append(f"- — **{sid}** {summaries.get(r['id'], r['title'])} _({r['skipped'].split(' (')[0]}; no corre en CI)_")
            continue
        extra = f" · pasó al intento {r['attempts']}" if r["passed"] and r.get("attempts", 1) > 1 else ""
        lines.append(f"- {'✅' if r['passed'] else '❌'} **{sid}** {summaries.get(r['id'], r['title'])} "
                     f"_({r['seconds']:.0f} s{extra})_")
        if not r["passed"]:
            why = r.get("error") or ("; ".join(n for n, ok, _ in r.get("checks", []) if not ok)) or r.get("driver_says", "")
            lines.append(f"  - Falló: {why}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qa", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--run-url", required=True)
    a = ap.parse_args()
    report_file = a.qa / "reporte.json"
    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else None
    scenarios = yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))["scenarios"]
    summaries = {sc["id"]: sc.get("resumen", sc["title"]) for sc in scenarios}
    images: dict[str, str] = {}
    for r in report or []:
        src = None if r.get("skipped") else pick_shot(a.qa, r["id"])
        if src is None:
            continue
        rel = f"pr-{a.pr}/{a.sha[:7]}/{r['id']}.jpg"
        thumb(src, a.dest / rel)
        images[r["id"]] = f"{a.base_url.rstrip('/')}/{rel}"
    print(comment(report, summaries, images, a.sha, a.run_url), end="")


if __name__ == "__main__":
    main()
