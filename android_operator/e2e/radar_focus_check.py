"""Radar sin robar el foco: la regla que pidió el operador, medida sin LLM.

Con el operador escribiendo en el chat, llega un incendio grave nuevo. Debe verse la tarjeta
apenas se sabe (sin esperar a que deje de escribir), el teclado debe seguir abierto y lo que
teclea después debe seguir entrando al mismo campo.

Precondición: la app abierta en un chat que el operador ya tomó (composer habilitado).
Uso: python3 radar_focus_check.py --expect "<texto de la tarjeta nueva>" --out <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from devicekit import Device

COMPOSER_HINT = "Escribe un mensaje…"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--expect", required=True, help="texto que debe traer la tarjeta del incendio nuevo")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    dev = Device(a.serial)
    facts: dict[str, object] = {}

    composer = dev.find(COMPOSER_HINT)
    assert composer, "no está el campo de texto: ¿el chat está tomado?"
    dev.tap(composer)
    time.sleep(1.0)
    dev.type_ascii("Hola")
    time.sleep(0.8)
    facts["ime_antes"] = dev.ime_shown()
    f = dev.focused()
    facts["foco_antes"] = f.text if f else None

    # Cuadros cada ~0,6 s desde la inyección: la tarjeta dura 4 s antes de plegarse al chip y
    # uiautomator tarda en leer una pantalla que se está animando.
    frames: list[tuple[float, str]] = []
    stop = threading.Event()

    def film() -> None:
        while not stop.is_set() and len(frames) < 30:
            path = out / "frames" / f"f{len(frames):02d}.png"
            dev.screenshot(path)
            frames.append((round(time.monotonic() - t0, 1), path.name))
            time.sleep(0.3)

    t0 = time.monotonic()
    threading.Thread(target=film, daemon=True).start()
    subprocess.run(os.environ.get("SANDBOX", f"python3 {Path(__file__).resolve().parent / 'sandbox' / 'inject.py'}") + " fire", shell=True, check=True, capture_output=True, timeout=60)
    card, waited = dev.wait_for(a.expect, timeout=20)
    facts["tarjeta_aparecio"] = card is not None
    facts["segundos_hasta_tarjeta"] = round(time.monotonic() - t0, 1)
    facts["ime_con_tarjeta"] = dev.ime_shown()
    stop.set()
    facts["cuadros"] = frames

    dev.type_ascii(" Laura")
    time.sleep(0.8)
    f = dev.focused()
    facts["foco_despues"] = f.text if f else None
    facts["ime_despues"] = dev.ime_shown()
    facts["texto_intacto"] = bool(f and "Hola Laura" in f.text)
    facts["pasa"] = bool(facts["tarjeta_aparecio"] and facts["ime_con_tarjeta"] and facts["ime_despues"] and facts["texto_intacto"])
    (out / "radar-focus.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(facts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
