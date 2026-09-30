"""Chequeos deterministas por adb (sin LLM): árbol de UI, foco, teclado y capturas.

Artemis recorre la app como un humano; esto verifica con precisión lo que un modelo
no puede medir bien (quién tiene el foco, si el teclado sigue abierto, cuánto tarda algo).
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ADB = os.environ.get("ADB", str(Path.home() / "Library/Android/sdk/platform-tools/adb"))


@dataclass(frozen=True)
class Node:
    text: str
    desc: str
    cls: str
    focused: bool
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def label(self) -> str:
        return self.text or self.desc


class Device:
    def __init__(self, serial: str) -> None:
        self.serial = serial

    def sh(self, cmd: str, timeout: float = 30) -> str:
        out = subprocess.run([ADB, "-s", self.serial, "shell", cmd], capture_output=True, text=True, timeout=timeout)
        return out.stdout + out.stderr

    def nodes(self, attempts: int = 5) -> list[Node]:
        """Árbol de accesibilidad actual. Reintenta: uiautomator falla si la UI no está quieta."""
        for _ in range(attempts):
            res = self.sh("uiautomator dump /sdcard/hubara-ui.xml")
            if "dumped" in res:
                xml = self.sh("cat /sdcard/hubara-ui.xml")
                return [_node(m.group(0)) for m in re.finditer(r"<node [^>]*>", xml)]
            time.sleep(0.4)
        raise RuntimeError(f"uiautomator no pudo leer la pantalla: {res.strip()}")

    def find(self, needle: str, nodes: list[Node] | None = None) -> Node | None:
        for n in nodes if nodes is not None else self.nodes():
            if needle in n.text or needle in n.desc:
                return n
        return None

    def wait_for(self, needle: str, timeout: float = 15.0) -> tuple[Node | None, float]:
        """Espera a que aparezca un texto. Devuelve el nodo y los segundos que tardó."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            n = self.find(needle)
            if n:
                return n, time.monotonic() - t0
            time.sleep(0.3)
        return None, time.monotonic() - t0

    def focused(self) -> Node | None:
        return next((n for n in self.nodes() if n.focused and n.cls.endswith("EditText")), None)

    def ime_shown(self) -> bool:
        return "mInputShown=true" in self.sh("dumpsys input_method")

    def tap(self, node: Node) -> None:
        x, y = node.center
        self.sh(f"input tap {x} {y}")

    def type_ascii(self, text: str) -> None:
        """Teclea como el teclado (eventos de tecla al campo con foco). Solo ASCII."""
        assert text.isascii(), text
        self.sh("input text " + text.replace(" ", "%s"))

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            subprocess.run([ADB, "-s", self.serial, "exec-out", "screencap", "-p"], stdout=fh, check=True, timeout=30)
        return path

    def launch(self, component: str = "com.hubara.operator/.MainActivity") -> None:
        self.sh(f"am start -W -n {component}")

    def open_link(self, uri: str) -> None:
        self.sh(f"am start -W -n com.hubara.operator/.MainActivity -a android.intent.action.VIEW -d '{uri}'")


def _attr(node: str, name: str) -> str:
    m = re.search(rf' {name}="([^"]*)"', node)
    return _unescape(m.group(1)) if m else ""


def _unescape(s: str) -> str:
    return s.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _node(raw: str) -> Node:
    b = [int(v) for v in re.findall(r"\d+", _attr(raw, "bounds"))] or [0, 0, 0, 0]
    return Node(_attr(raw, "text"), _attr(raw, "content-desc"), _attr(raw, "class"), _attr(raw, "focused") == "true",
                (b[0], b[1], b[2], b[3]))
