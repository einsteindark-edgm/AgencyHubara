"""Corre los escenarios E2E de la app del operador sobre un emulador.

Cada escenario: prepara el estado (backend de prueba + app), un conductor recorre la app y después
se verifica con hechos deterministas: lo que el backend registró como enviado, el estado en el API y
el árbol de UI por adb. Hay dos conductores para los MISMOS escenarios y los MISMOS chequeos:

  script   (por defecto) sigue los pasos de `script:` por adb: siempre igual, sin LLM ni llaves.
           Es la compuerta de merge (.github/workflows/qa-emulador.yml).
  artemis  Artemis (Gemini) persigue el `goal:` en lenguaje natural: explora y encuentra lo que nadie
           guionizó. Gasta tokens; se corre a mano.

Uso:
  python3 run_suite.py --out <dir> [--driver script|artemis] [--only S1,S2] [--serial emulator-5554] [--retries 1]
Variables (todas opcionales):
  ARTEMIS      comando de Artemis (por defecto ./artemis.sh; ver ARTEMIS_HOME / ARTEMIS_KEY_FILE allí)
  SANDBOX      comando para mutar el backend de prueba (por defecto python3 sandbox/inject.py)
  SENT_LOG     lo que el backend de prueba habría enviado por WhatsApp (por defecto sandbox/sent.log)
  SANDBOX_URL  base del backend de prueba (por defecto http://127.0.0.1:8010)
  ADB          adb a usar (por defecto el del SDK o el del PATH)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from devicekit import Device, Node

HERE = Path(__file__).resolve().parent
PACKAGE = "com.hubara.operator"
DRIVERS = ("script", "artemis")


@dataclass
class Result:
    id: str
    title: str
    driver: str = "script"
    driver_ok: bool = False
    driver_says: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    seconds: float = 0.0
    shot: str = ""
    error: str = ""
    skipped: str = ""
    attempts: int = 1

    @property
    def passed(self) -> bool:
        return bool(self.skipped) or (self.driver_ok and all(ok for _, ok, _ in self.checks) and not self.error)


class StepFailed(AssertionError):
    """Un paso del guion no se cumplió: el escenario falla ahí, con el motivo."""


def sandbox(args: str) -> str:
    cmd = os.environ.get("SANDBOX", f"python3 {HERE / 'sandbox' / 'inject.py'}") + " " + args
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"sandbox {args} falló: {out.stderr[-500:]}")
    return out.stdout


def sent_lines() -> list[str]:
    p = Path(os.environ.get("SENT_LOG", HERE / "sandbox" / "sent.log"))
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


class Run:
    """Un escenario en curso: el dispositivo y desde qué envío contar lo que salió en este escenario."""

    def __init__(self, dev: Device, out: Path | None = None, sid: str = "") -> None:
        self.dev = dev
        self.out = out
        self.sid = sid
        self.sent_before = len(sent_lines())

    def _relay_push(self, timeout: float) -> None:
        """Firebase de mentira: lo que el backend de prueba habría mandado a Google (`inject.py push-relay`) llega a
        la app por un broadcast a E2eWakeReceiver (E2E_PUSH), que lo pasa al MISMO PushHandler de un push real."""
        deadline = time.time() + timeout
        while True:
            pushes = [json.loads(line) for line in sandbox("push-relay").splitlines() if line.startswith("{")]
            if pushes:
                break
            if time.time() > deadline:
                raise StepFailed(f"el backend no mandó ningún push en {timeout:.0f} s")
            time.sleep(1)
        for push in pushes:
            extras = " ".join(f"--es {key} {value}" for key, value in push["data"].items())
            self.dev.sh(f"am broadcast -n {PACKAGE}/{PACKAGE}.E2eWakeReceiver -a {PACKAGE}.E2E_PUSH {extras}")

    def step(self, step: dict | str) -> None:
        """Un paso. Sin argumento: "reset", "clear_app", "launch", "home", "back", "notifications".
        Con argumento: {"link": uri}, {"wait": s}, {"inject": "fire …"}, {"adb": "shell cmd"},
        {"tap": "texto"}, {"type": "ascii"}, {"expect": "texto"}, {"expect_gone": "texto"},
        {"expect_all": [...] | {texts, in_order, above_focused}}, {"assert": {<chequeo>}},
        {"shot": "nombre"} (captura a mitad del escenario, la que muestra el comentario del PR),
        {"relay_push": s} (espera hasta s segundos el push que mandó el backend y se lo entrega a la app).
        Un texto con «=» adelante busca igual exacto (p. ej. "=Enviar" y no «Enviar aromas»)."""
        if isinstance(step, str):
            step = {step: True}
        (kind, arg), = step.items()
        dev = self.dev
        if kind == "reset":
            sandbox("reset")
            self.sent_before = len(sent_lines())
        elif kind == "clear_app":
            dev.sh(f"pm clear {PACKAGE}")
        elif kind == "launch":
            dev.launch()
        elif kind == "home":
            dev.sh("input keyevent KEYCODE_HOME")
        elif kind == "back":
            dev.back()
        elif kind == "notifications":
            dev.sh("cmd statusbar expand-notifications")
        elif kind == "link":
            dev.open_link(arg)
        elif kind == "wait":
            time.sleep(float(arg))
        elif kind == "inject":
            sandbox(arg)
        elif kind == "adb":
            dev.sh(arg)
        elif kind == "tap":
            text, timeout = _text_and_timeout(arg, 20)
            node, _ = dev.wait_for(text, timeout=timeout)
            if not node:
                raise StepFailed(f"no apareció «{text}» para tocarlo")
            dev.tap(node)
        elif kind == "type":
            dev.type_ascii(arg)
        elif kind == "expect":
            text, timeout = _text_and_timeout(arg, 20)
            if not dev.wait_for(text, timeout=timeout)[0]:
                raise StepFailed(f"no apareció «{text}» en {timeout:.0f} s")
        elif kind == "expect_gone":
            text, timeout = _text_and_timeout(arg, 20)
            if not dev.wait_gone(text, timeout=timeout):
                raise StepFailed(f"«{text}» seguía en pantalla a los {timeout:.0f} s")
        elif kind == "expect_all":
            self._expect_all(arg)
        elif kind == "relay_push":
            self._relay_push(float(arg))
        elif kind == "shot":
            if self.out is not None:
                self.dev.screenshot(self.out / f"{self.sid}.{arg}.png")
        elif kind == "assert":
            name, ok, detail = check(dev, arg, self.sent_before)
            if not ok:
                raise StepFailed(f"{name}: {detail}")
        else:
            raise ValueError(f"paso desconocido: {kind}")

    def _expect_all(self, arg: list[str] | dict) -> None:
        """Todos los textos a la vez, en UNA lectura; opcionalmente en ese orden de arriba abajo y por
        encima del campo con foco (lo que el operador está escribiendo no queda tapado)."""
        spec = arg if isinstance(arg, dict) else {"texts": arg}
        texts: list[str] = spec["texts"]
        nodes = self.dev.wait_for_all(texts, timeout=float(spec.get("timeout", 20)))
        if nodes is None:
            raise StepFailed(f"no estuvieron a la vez en pantalla: {texts}")
        found = [self.dev.find(t, nodes) for t in texts]
        if spec.get("in_order"):
            tops = [n.bounds[1] for n in found if n]
            if tops != sorted(tops):
                raise StepFailed(f"orden de arriba abajo distinto a {texts}: y={tops}")
        if spec.get("above_focused"):
            focus = next((n for n in nodes if n.focused and n.cls.endswith("EditText")), None)
            if focus is None:
                raise StepFailed("no hay campo de texto con foco")
            covering = [t for t, n in zip(texts, found) if n and n.bounds[3] > focus.bounds[1]]
            if covering:
                raise StepFailed(f"tapan el campo de texto (borde superior y={focus.bounds[1]}): {covering}")


def _text_and_timeout(arg: str | dict, default: float) -> tuple[str, float]:
    if isinstance(arg, dict):
        return arg["text"], float(arg.get("timeout", default))
    return arg, default


def run_artemis(sc: dict, out: Path, serial: str) -> tuple[bool, str]:
    artemis = os.environ.get("ARTEMIS", str(HERE / "artemis.sh"))
    # --standalone: Artemis corre en este proceso. Sin eso levanta un servicio aparte (por defecto en 127.0.0.1:8000,
    # el puerto de la API local) y el log solo dice «Task completed successfully!», sin el informe que se lee abajo.
    cmd = [*shlex.split(artemis), "run", sc["goal"].strip(), "-p", sc.get("profile", "flash"), "-s", serial,
           "-n", sc["id"], "-t", str(out / "traces"), "--standalone"]
    if sc.get("locked_app", True):
        cmd += ["-a", PACKAGE]
    log = out / f"{sc['id']}.artemis.log"
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=sc.get("timeout_s", 900))
    text = log.read_text(encoding="utf-8", errors="replace")
    ok = proc.returncode == 0 and f"Automation '{sc['id']}' is success" in text
    return ok, final_report(text)


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


def skip_reason(sc: dict, driver: str) -> str:
    if driver == "script" and "script" not in sc:
        return "solo con Artemis (no tiene guion)"
    if driver == "artemis" and "goal" not in sc:
        return "sin objetivo para Artemis"
    return ""


def run_one(dev: Device, sc: dict, out: Path, serial: str, driver: str) -> Result:
    res = Result(sc["id"], sc["title"], driver)
    t0 = time.monotonic()
    timers: list[threading.Timer] = []
    run = Run(dev, out, sc["id"])
    try:
        dev.collapse_shade()
        dev.dismiss_not_responding()
        # Lo enviado se cuenta desde el `reset`, no desde el fin de la preparación: si un «Deshacer» dado
        # en `setup` falla, el envío sale ahí mismo y el chequeo tiene que verlo.
        for step in sc.get("setup", []):
            run.step(step)
        for ev in sc.get("during", []):
            t = threading.Timer(float(ev["after_s"]), run.step, args=(ev["step"],))
            t.start()
            timers.append(t)
        if driver == "artemis":
            res.driver_ok, res.driver_says = run_artemis(sc, out, serial)
        else:
            for i, step in enumerate(sc.get("script") or [], start=1):
                try:
                    run.step(step)
                except StepFailed as e:
                    res.driver_says = f"paso {i} {json.dumps(step, ensure_ascii=False)}: {e}"
                    break
            else:
                res.driver_ok, res.driver_says = True, f"{len(sc.get('script') or [])} pasos"
        time.sleep(float(sc.get("settle_s", 1)))
        for spec in sc.get("checks", []):
            res.checks.append(check(dev, spec, run.sent_before))
    except Exception as e:  # el reporte debe salir aunque un escenario reviente
        res.error = f"{type(e).__name__}: {e}"
    finally:
        for t in timers:
            t.cancel()
        res.shot = str(dev.screenshot(out / f"{sc['id']}.png"))
        if not res.passed:
            _dump_screen(dev, out / f"{sc['id']}.pantalla.txt")
        res.seconds = time.monotonic() - t0
    return res


def _dump_screen(dev: Device, path: Path) -> None:
    """Lo que había en pantalla al fallar: para entender una falla de CI sin el emulador delante."""
    try:
        nodes: list[Node] = dev.nodes()
    except RuntimeError as e:
        path.write_text(str(e), encoding="utf-8")
        return
    path.write_text("\n".join(f"{n.bounds} {n.cls.rsplit('.', 1)[-1]} {'[foco] ' if n.focused else ''}{n.label}"
                              for n in nodes if n.label), encoding="utf-8")


def report(results: list[Result], out: Path, driver: str) -> Path:
    passed = sum(1 for r in results if r.passed and not r.skipped)
    ran = sum(1 for r in results if not r.skipped)
    lines = [f"# QA en emulador — conductor `{driver}`: {passed} de {ran} pasan", "",
             "| Escenario | Resultado | Conductor | Chequeos | Tiempo |", "|---|---|---|---|---|"]
    for r in results:
        if r.skipped:
            lines.append(f"| {r.id} {r.title} | — {r.skipped} | | | |")
            continue
        checks = "; ".join(f"{'✅' if ok else '❌'} {name}" for name, ok, _ in r.checks) or "—"
        verdict = "✅ pasa" if r.passed else "❌ falla"
        if r.passed and r.attempts > 1:
            verdict += f" (al intento {r.attempts})"
        lines.append(f"| {r.id} {r.title} | {verdict} | {'✅' if r.driver_ok else '❌'} | {checks} | {r.seconds:.0f} s |")
    lines += ["", "## Detalle", ""]
    for r in results:
        if r.skipped:
            continue
        lines += [f"### {r.id} — {r.title}", "", f"- Conductor: {r.driver_says or '(sin explicación)'}"]
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
    ap.add_argument("--driver", choices=DRIVERS, default="script")
    ap.add_argument("--only", default="")
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--retries", type=int, default=0, help="reintentos de un escenario que falla (se marcan en el reporte)")
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
        if reason := skip_reason(sc, a.driver):
            results.append(Result(sc["id"], sc["title"], a.driver, skipped=reason))
            print(f"— {sc['id']} {reason}", flush=True)
            continue
        print(f"▶ {sc['id']} {sc['title']}", flush=True)
        for attempt in range(1, a.retries + 2):
            r = run_one(dev, sc, out, a.serial, a.driver)
            r.attempts = attempt
            if r.passed:
                break
            print(f"  ↻ intento {attempt} falló: {r.error or r.driver_says}", flush=True)
        print(f"  {'✅' if r.passed else '❌'} {r.seconds:.0f} s · {r.driver_says[:160]}", flush=True)
        for name, ok, detail in r.checks:
            print(f"    {'✅' if ok else '❌'} {name} {('— ' + detail) if detail and not ok else ''}", flush=True)
        if r.error:
            print(f"    error: {r.error}", flush=True)
        results.append(r)
    print(report(results, out, a.driver))
    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
