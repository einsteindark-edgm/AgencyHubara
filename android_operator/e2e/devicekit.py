"""Manejo y chequeos deterministas por adb (sin LLM): árbol de UI, toques, foco, teclado y capturas.

Con esto el conductor «script» recorre la app siempre igual (la compuerta de merge), y se verifica
con precisión lo que un modelo no mide bien (quién tiene el foco, si el teclado sigue abierto,
qué tapa a qué, cuánto tarda algo).
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


def _adb() -> str:
    """ADB del entorno, del SDK (Linux en CI o Mac) o del PATH."""
    candidates = [os.environ.get("ADB", "")]
    candidates += [str(Path(os.environ[v]) / "platform-tools" / "adb") for v in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if os.environ.get(v)]
    candidates.append(str(Path.home() / "Library/Android/sdk/platform-tools/adb"))
    return next((c for c in candidates if c and Path(c).exists()), shutil.which("adb") or "adb")


ADB = _adb()


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
        """Árbol de accesibilidad actual, en una sola llamada a adb (el XML sale por la terminal: las
        ventanas cortas, como los 5 s de «Deshacer», no alcanzan para dos idas y vueltas con el emulador
        cargado). Reintenta: uiautomator falla si la UI no está quieta."""
        res = ""
        for _ in range(attempts):
            out = subprocess.run([ADB, "-s", self.serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
                                 capture_output=True, text=True, timeout=30)
            res = out.stdout + out.stderr
            if "<hierarchy" in res:
                return [_node(m.group(0)) for m in re.finditer(r"<node [^>]*>", res)]
            time.sleep(0.4)
        raise RuntimeError(f"uiautomator no pudo leer la pantalla: {res.strip()[:300]}")

    def find(self, needle: str, nodes: list[Node] | None = None) -> Node | None:
        """Primer nodo cuyo texto o descripción contiene `needle`; con «=» adelante, igual exacto."""
        exact = needle.startswith("=")
        want = needle[1:] if exact else needle
        for n in nodes if nodes is not None else self.nodes():
            if (want in (n.text, n.desc)) if exact else (want in n.text or want in n.desc):
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

    def wait_for_all(self, needles: list[str], timeout: float = 15.0) -> list[Node] | None:
        """Espera a que TODOS los textos estén a la vez en la misma lectura; devuelve esa lectura."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            nodes = self.nodes()
            if all(self.find(n, nodes) for n in needles):
                return nodes
            time.sleep(0.3)
        return None

    def wait_gone(self, needle: str, timeout: float = 15.0) -> bool:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.find(needle) is None:
                return True
            time.sleep(0.3)
        return False

    def focused(self) -> Node | None:
        return next((n for n in self.nodes() if n.focused and n.cls.endswith("EditText")), None)

    def ime_shown(self) -> bool:
        return "mInputShown=true" in self.sh("dumpsys input_method")

    def tap(self, node: Node) -> None:
        x, y = node.center
        self.sh(f"input tap {x} {y}")

    def type_ascii(self, text: str, chunk: int = 10) -> None:
        """Teclea como el teclado (eventos de tecla al campo con foco). Solo ASCII. Por trozos: un solo
        `input text` largo llegaba cortado al campo («Hola Laura, ya te comparto » y el resto se perdía)."""
        assert text.isascii(), text
        for i in range(0, len(text), chunk):
            self.sh("input text " + shlex.quote(text[i:i + chunk].replace(" ", "%s")))

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            subprocess.run([ADB, "-s", self.serial, "exec-out", "screencap", "-p"], stdout=fh, check=True, timeout=30)
        return path

    def dismiss_not_responding(self) -> bool:
        """Cierra el aviso del sistema «X isn't responding» tocando «Wait». Sale en emuladores recién
        arrancados en una máquina cargada y tapa toda la pantalla; no es una falla de la app."""
        try:
            nodes = self.nodes()
        except RuntimeError:
            return False
        if not any("isn't responding" in n.text or "no responde" in n.text for n in nodes):
            return False
        wait = self.find("=Wait", nodes) or self.find("=Esperar", nodes)
        if wait:
            self.tap(wait)
            time.sleep(1)
        return wait is not None

    def back(self) -> None:
        self.sh("input keyevent KEYCODE_BACK")

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
