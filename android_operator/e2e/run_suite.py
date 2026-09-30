"""Corre los escenarios E2E de la app del operador con Artemis sobre un emulador.

Cada escenario: prepara el estado (backend de prueba + app), le da a Artemis un objetivo en
lenguaje natural, y después verifica con hechos deterministas (lo que el backend registró como
enviado, el árbol de UI por adb). Artemis dice «lo hice y vi esto»; los chequeos lo confirman.

Uso:
  python3 run_suite.py --scenarios scenarios.yaml --out <dir> [--only S1,S2] [--serial emulator-5554]
Variables (todas opcionales):
  ARTEMIS      comando de Artemis (por defecto ./artemis.sh; ver ARTEMIS_HOME / ARTEMIS_KEY_FILE allí)
  SANDBOX      comando para mutar el backend de prueba (por defecto python3 sandbox/inject.py)
  SENT_LOG     lo que el backend de prueba habría enviado por WhatsApp (por defecto sandbox/sent.log)
  SANDBOX_URL  base del backend de prueba (por defecto http://127.0.0.1:8010)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from devicekit import Device

HERE = Path(__file__).resolve().parent
PACKAGE = "com.hubara.operator"


@dataclass
class Result:
    id: str
    title: str
    artemis_ok: bool = False
    artemis_says: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    seconds: float = 0.0
    shot: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.artemis_ok and all(ok for _, ok, _ in self.checks) and not self.error


def sandbox(args: str) -> str:
    cmd = os.environ.get("SANDBOX", f"python3 {HERE / 'sandbox' / 'inject.py'}") + " " + args
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"sandbox {args} falló: {out.stderr[-500:]}")
    return out.stdout


def sent_lines() -> list[str]:
    p = Path(os.environ.get("SENT_LOG", HERE / "sandbox" / "sent.log"))
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def do_step(dev: Device, step: dict | str) -> None:
    """Un paso de preparación. Formas: "reset", "clear_app", "launch", {"link": uri}, {"wait": s},
    {"inject": "fire"}, {"adb": "shell cmd"}, {"tap": "texto"}, {"type": "ascii"}."""
    if isinstance(step, str):
        step = {step: True}
    (kind, arg), = step.items()
    if kind == "reset":
        sandbox("reset")
    elif kind == "clear_app":
        dev.sh(f"pm clear {PACKAGE}")
    elif kind == "launch":
        dev.launch()
    elif kind == "home":
        dev.sh("input keyevent KEYCODE_HOME")
    elif kind == "link":
        dev.open_link(arg)
    elif kind == "wait":
        time.sleep(float(arg))
    elif kind == "inject":
        sandbox(arg)
    elif kind == "adb":
        dev.sh(arg)
    elif kind == "tap":
        node, _ = dev.wait_for(arg, timeout=20)
        if not node:
            raise RuntimeError(f"no apareció «{arg}» para tocarlo")
        dev.tap(node)
    elif kind == "type":
        dev.type_ascii(arg)
    else:
        raise ValueError(f"paso desconocido: {kind}")


def run_artemis(sc: dict, out: Path, serial: str) -> tuple[bool, str, str]:
    artemis = os.environ.get("ARTEMIS", str(HERE / "artemis.sh"))
    cmd = [*shlex.split(artemis), "run", sc["goal"].strip(), "-p", sc.get("profile", "flash"), "-s", serial,
           "-n", sc["id"], "-t", str(out / "traces")]
    if sc.get("locked_app", True):
        cmd += ["-a", PACKAGE]
    log = out / f"{sc['id']}.artemis.log"
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=sc.get("timeout_s", 900))
    text = log.read_text(encoding="utf-8", errors="replace")
    ok = proc.returncode == 0 and f"Automation '{sc['id']}' is success" in text
    return ok, final_report(text), str(log)


def final_report(text: str) -> str:
    """El último `report_task_status({...})` del log (repr de un dict de Python)."""
    for m in reversed(list(re.finditer(r"report_task_status\((\{.*?\})\)\n", text, re.S))):
        try:
            d = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            continue
        return f"[{d.get('status', '?')}] {d.get('explanation', '')}"
    m2 = re.findall(r"(?:Final (?:answer|report)|Task summary)[:\s]+(.{0,600})", text)
    return m2[-1] if m2 else ""


def check(dev: Device, spec: dict, sent_before: int) -> tuple[str, bool, str]:
    (kind, arg), = spec.items()
    if kind == "screen_has":
        n = dev.find(arg)
        return (f"en pantalla «{arg}»", n is not None, "" if n else "no está")
    if kind == "screen_lacks":
        n = dev.find(arg)
        return (f"no está «{arg}»", n is None, "" if n is None else "sí está")
    if kind == "sent_contains":
        new = sent_lines()[sent_before:]
        hit = [line for line in new if all(part in line for part in _as_list(arg))]
        return (f"el backend envió {arg}", bool(hit), hit[0][:200] if hit else f"{len(new)} envíos nuevos, ninguno coincide")
    if kind == "sent_nothing_matching":
        new = sent_lines()[sent_before:]
        hit = [line for line in new if all(part in line for part in _as_list(arg))]
        return (f"el backend NO envió {arg}", not hit, hit[0][:200] if hit else "")
    if kind == "ime_shown":
        shown = dev.ime_shown()
        return (f"teclado {'abierto' if arg else 'cerrado'}", shown == bool(arg), f"mInputShown={shown}")
    if kind == "focused_contains":
        f = dev.focused()
        return (f"campo con foco contiene «{arg}»", bool(f and arg in f.text), f"foco en: {f.text if f else 'nada'}")
    if kind in ("api_equals", "api_not_equals"):
        value = api_value(arg["path"], arg["key"])
        same = value == arg["equals"]
        ok = same if kind == "api_equals" else not same
        rel = "=" if kind == "api_equals" else "≠"
        return (f"backend {arg['key']} {rel} {arg['equals']}", ok, f"{arg['key']}={value!r}")
    raise ValueError(f"chequeo desconocido: {kind}")


def api_value(path: str, key: str) -> object:
    with urllib.request.urlopen(os.environ.get("SANDBOX_URL", "http://127.0.0.1:8010") + path, timeout=10) as r:
        data: object = json.loads(r.read())
    for part in key.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def _as_list(v: str | list[str]) -> list[str]:
    return v if isinstance(v, list) else [v]


def run_one(dev: Device, sc: dict, out: Path, serial: str) -> Result:
    res = Result(sc["id"], sc["title"])
    t0 = time.monotonic()
    timers: list[threading.Timer] = []
    try:
        for step in sc.get("setup", []):
            do_step(dev, step)
        sent_before = len(sent_lines())
        for ev in sc.get("during", []):
            t = threading.Timer(float(ev["after_s"]), do_step, args=(dev, ev["step"]))
            t.start()
            timers.append(t)
        res.artemis_ok, res.artemis_says, _ = run_artemis(sc, out, serial)
        time.sleep(float(sc.get("settle_s", 1)))
        for spec in sc.get("checks", []):
            res.checks.append(check(dev, spec, sent_before))
    except Exception as e:  # el reporte debe salir aunque un escenario reviente
        res.error = f"{type(e).__name__}: {e}"
    finally:
        for t in timers:
            t.cancel()
        res.shot = str(dev.screenshot(out / f"{sc['id']}.png"))
        res.seconds = time.monotonic() - t0
    return res


def report(results: list[Result], out: Path) -> Path:
    lines = ["# Pruebas E2E con Artemis", "", "| Escenario | Resultado | Artemis | Chequeos | Tiempo |", "|---|---|---|---|---|"]
    for r in results:
        checks = "; ".join(f"{'✅' if ok else '❌'} {name}" for name, ok, _ in r.checks) or "—"
        lines.append(f"| {r.id} {r.title} | {'✅ pasa' if r.passed else '❌ falla'} | "
                     f"{'✅' if r.artemis_ok else '❌'} | {checks} | {r.seconds:.0f} s |")
    lines += ["", "## Detalle", ""]
    for r in results:
        lines += [f"### {r.id} — {r.title}", "", f"- Artemis: {r.artemis_says or '(sin explicación)'}"]
        lines += [f"- {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else "") for name, ok, detail in r.checks]
        if r.error:
            lines.append(f"- Error: {r.error}")
        lines += [f"- Captura: `{Path(r.shot).name}`", ""]
    path = out / "reporte.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (out / "reporte.json").write_text(json.dumps([r.__dict__ | {"passed": r.passed} for r in results],
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default=str(HERE / "scenarios.yaml"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--serial", default="emulator-5554")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    scenarios = yaml.safe_load(Path(a.scenarios).read_text(encoding="utf-8"))["scenarios"]
    only = {s for s in a.only.split(",") if s}
    dev = Device(a.serial)
    results = []
    for sc in scenarios:
        if only and sc["id"] not in only:
            continue
        print(f"▶ {sc['id']} {sc['title']}", flush=True)
        r = run_one(dev, sc, out, a.serial)
        print(f"  {'✅' if r.passed else '❌'} {r.seconds:.0f} s · {r.artemis_says[:160]}", flush=True)
        for name, ok, detail in r.checks:
            print(f"    {'✅' if ok else '❌'} {name} {('— ' + detail) if detail and not ok else ''}", flush=True)
        if r.error:
            print(f"    error: {r.error}", flush=True)
        results.append(r)
    print(report(results, out))


if __name__ == "__main__":
    main()
