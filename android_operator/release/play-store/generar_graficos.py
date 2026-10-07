"""Gráficos de la ficha de Google Play: ícono 512, gráfico de funciones 1024×500 y capturas 1080×2160.

Uso (desde android_operator/):
    python3 release/play-store/generar_graficos.py release/play-store <carpeta de capturas del E2E claro> <oscuro>

Las capturas salen de `e2e/qa.sh` (QA_OUT=…); el modo oscuro, con `adb shell cmd uimode night yes` antes de correrlo.
Necesita Pillow (`pip install pillow`).
"""
import os, sys
from PIL import Image, ImageDraw, ImageFont
D, LIGHT, DARK = sys.argv[1], sys.argv[2], sys.argv[3]
BLUE = (0, 93, 184); WHITE = (255, 255, 255); SOFT = (214, 227, 255)
FONT = os.path.join(os.path.dirname(__file__), "..", "..", "core", "designsystem", "src", "main", "res", "font", "google_sans_flex.ttf")

def bubble(draw, cx, cy, k, fill, dots):
    """El globo del ícono (cuadrícula de 108 del ícono adaptable) centrado en (cx, cy) con escala k."""
    def P(x, y): return (cx + (x - 54) * k, cy + (y - 56) * k)
    x0, y0 = P(30, 34); x1, y1 = P(78, 70)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=12 * k, fill=fill)
    draw.polygon([P(42, 69), P(50, 69), P(38, 79)], fill=fill)
    for x in (42, 54, 66):
        a, b = P(x - 3.6, 52 - 3.6), P(x + 3.6, 52 + 3.6)
        draw.ellipse([a, b], fill=dots)

def font(size, weight):
    f = ImageFont.truetype(FONT, size)
    try: f.set_variation_by_axes([weight])
    except Exception: pass
    return f

# Ícono de la ficha: 512×512, PNG de 32 bits (Play aplica la máscara redondeada).
ic = Image.new("RGBA", (512, 512), BLUE + (255,)); d = ImageDraw.Draw(ic, "RGBA")
bubble(d, 256, 262, 512 / 108 * 1.25, WHITE + (255,), BLUE + (255,))
ic.save(os.path.join(D, "icono-512.png"))

# Gráfico de funciones: 1024×500, sin transparencia.
fg = Image.new("RGB", (1024, 500), BLUE); d = ImageDraw.Draw(fg)
d.rounded_rectangle([96, 130, 336, 370], radius=56, fill=(0, 70, 141))
bubble(d, 216, 254, 3.6, WHITE, BLUE)
d.text((392, 168), "App Operador", font=font(76, 650), fill=WHITE)
d.text((394, 272), "Chats, incendios y pedidos", font=font(40, 450), fill=SOFT)
d.text((394, 324), "de la tienda, en tu teléfono.", font=font(40, 450), fill=SOFT)
fg.save(os.path.join(D, "grafico-funciones-1024x500.png"))

# Capturas: sin barra de estado ni botones del sistema, 1080×2160 (el lado largo no pasa del doble del corto).
shots = [("01-bandeja", LIGHT, "S01_bandeja_radar.png"), ("02-chat", LIGHT, "S02_tomar_y_enviar_aromas.png"),
         ("03-radar", LIGHT, "S01_bandeja_radar.radar-desplegado.png"),
         ("04-varios-incendios", LIGHT, "S14_varios_incendios_mientras_escribes.tres-tarjetas.png"),
         ("05-ficha-pedido", LIGHT, "S07b_ficha_abre_completa.png"), ("06-modo-oscuro", DARK, "S01_bandeja_radar.png")]
for name, folder, src in shots:
    im = Image.open(os.path.join(folder, src)).convert("RGB")
    w, h = im.size
    top = 70  # sin la barra de estado; el lado largo no pasa del doble del corto (regla de Play)
    im = im.crop((0, top, w, top + min(2 * w, h - top)))
    im.save(os.path.join(D, "capturas", f"{name}.png"))
    print(name, im.size)
for f in ("icono-512.png", "grafico-funciones-1024x500.png"):
    p = os.path.join(D, f); print(f, Image.open(p).size, Image.open(p).mode, os.path.getsize(p) // 1024, "KB")
