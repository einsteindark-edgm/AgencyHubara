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

    def find(self, needle: str, nodes: list[Node] | None = None, above: str | None = None) -> Node | None:
        """Primer nodo cuyo texto o descripción contiene `needle`; con «=» adelante, igual exacto. Con `above`, el
        primero que queda encima del nodo con ese texto (dos elementos con el mismo nombre en pantalla)."""
        exact = needle.startswith("=")
        want = needle[1:] if exact else needle
        nodes = nodes if nodes is not None else self.nodes()
        ref = self.find(above, nodes) if above else None
        if above and ref is None:
            return None
        for n in nodes:
            if ref is not None and n.center[1] >= ref.bounds[1]:
                continue
            if (want in (n.text, n.desc)) if exact else (want in n.text or want in n.desc):
                return n
        return None

    def wait_for(self, needle: str, timeout: float = 15.0, above: str | None = None) -> tuple[Node | None, float]:
        """Espera a que aparezca un texto. Devuelve el nodo y los segundos que tardó."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            n = self.find(needle, above=above)
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

    def finger_tap(self, node: Node) -> None:
        """Toque con el panel táctil del emulador (consola `adb emu`), no con `input tap`.

        En Android 11 `input tap` inyecta el toque sin tipo de puntero (TOOL_TYPE_UNKNOWN; desde Android 12 es
        FINGER) y lo que usa `SelectionTracker` lo ignora: en el selector de archivos del sistema tocar una foto no
        hace nada. Por la consola el toque entra por el dispositivo táctil virtual, como un dedo. Solo emulador.
        """
        x, y = node.center
        width, height = self._screen_size()
        span = 32767  # rango ABS_MT_POSITION_* de virtio_input_multi_touch
        down = ["EV_ABS:ABS_MT_TRACKING_ID:1", f"EV_ABS:ABS_MT_POSITION_X:{x * span // width}",
                f"EV_ABS:ABS_MT_POSITION_Y:{y * span // height}", "EV_ABS:ABS_MT_PRESSURE:100", "EV_KEY:BTN_TOUCH:1",
                "EV_SYN:0:0"]
        up = ["EV_ABS:ABS_MT_TRACKING_ID:-1", "EV_KEY:BTN_TOUCH:0", "EV_SYN:0:0"]
        for events in (down, up):
            out = subprocess.run([ADB, "-s", self.serial, "emu", "event", "send", *events],
                                 capture_output=True, text=True, timeout=15)
            if "OK" not in out.stdout:
                raise RuntimeError(f"la consola del emulador no aceptó el toque: {(out.stdout + out.stderr).strip()[:200]}")

    def add_photo(self, name: str) -> str:
        """Deja una foto en la galería (la captura de la pantalla de ese momento) y la registra en MediaStore en el acto;
        devuelve su `content://`. Con el aviso `MEDIA_SCANNER_SCAN_FILE` (asíncrono, sin respuesta) la foto no llegó a
        la galería en el emulador recién creado de CI (S38, 2026-11-04: el selector decía «No items»), aunque en un
        emulador local sí. `content call … scan_file` la escanea antes de volver y dice qué registró.

        Además borra la memoria del selector de archivos (DocumentsUI): abre donde la app lo dejó la última vez (en
        local, «Downloads», donde la foto no está) y en un emulador nuevo abre «Recent». Así siempre arranca en
        «Recent», donde sale la foto recién registrada."""
        for pkg in re.findall(r"package:(\S*documentsui\S*)", self.sh("pm list packages documentsui")):
            self.sh(f"pm clear {pkg}")
        path = f"/storage/emulated/0/Pictures/{name}"
        made = self.sh(f"mkdir -p /storage/emulated/0/Pictures && screencap -p {shlex.quote(path)} && ls {shlex.quote(path)}")
        if path not in made:
            raise RuntimeError(f"no se pudo crear {path}: {made.strip()[:200]}")
        scanned = self.sh(f"content call --uri content://media --method scan_file --arg {shlex.quote(path)}")
        uri = re.search(r"content://media/\S+/images/media/\d+", scanned)
        if not uri:
            raise RuntimeError(f"MediaStore no registró {path}: {scanned.strip()[:200]}")
        return uri.group(0)

    def _screen_size(self) -> tuple[int, int]:
        m = re.search(r"Physical size: (\d+)x(\d+)", self.sh("wm size"))
        if not m:
            raise RuntimeError("no se pudo leer el tamaño de la pantalla (wm size)")
        return int(m.group(1)), int(m.group(2))

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

    def collapse_shade(self) -> None:
        """Cierra la cortina de notificaciones. Un escenario que falla con ella abierta (S12) la dejaba encima de la
        app y tumbaba todos los siguientes."""
        self.sh("cmd statusbar collapse")

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

    def launch(self, component: str = "com.acktos.operator/com.hubara.operator.MainActivity") -> None:
        self.sh(f"am start -W -n {component}")

    def open_link(self, uri: str) -> None:
        self.sh(f"am start -W -n com.acktos.operator/com.hubara.operator.MainActivity -a android.intent.action.VIEW -d '{uri}'")


def _attr(node: str, name: str) -> str:
    m = re.search(rf' {name}="([^"]*)"', node)
    return _unescape(m.group(1)) if m else ""


def _unescape(s: str) -> str:
    return s.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _node(raw: str) -> Node:
    b = [int(v) for v in re.findall(r"\d+", _attr(raw, "bounds"))] or [0, 0, 0, 0]
    return Node(_attr(raw, "text"), _attr(raw, "content-desc"), _attr(raw, "class"), _attr(raw, "focused") == "true",
                (b[0], b[1], b[2], b[3]))
