"""`ctwa-report` con SCORECARD (rediseño 2026-09-25): el reporte EXPLICA, no lista números.

El reporte viejo abría con una tabla diaria y un código (`scale_budget`); la narrativa del
LLM repetía la tabla en prosa ("el 2026-09-22 aparece con spend 28306…"). La operadora
no sabía qué hacer. Ahora, con el drill-down de la campaña:

1. Veredicto en lenguaje llano con el POR QUÉ (retorno confirmado vs el mínimo).
2. "Qué hacer, en orden": cada acción nombra el anuncio/segmento/tarjeta EXACTO + la
   evidencia + la confianza.
3. Resumen, segmentos, anuncios, tarjetas y advertencias de datos.
4. La narrativa del LLM recibe hallazgos priorizados (no la tabla diaria) y un prompt que
   exige explicar a alguien no experto, sin inventar cifras (el guard sigue gobernando).

Golden sobre el caso REAL Halloween (fixture del drill-down de prod).
"""
from __future__ import annotations

import json
from pathlib import Path

from graphs.ctwa_report import _narrate_user, invented_numbers, run
from graphs.ctwa_scorecard import run as score
from sdk.connectorkit.ports import FixtureLLM
from tools.entity_economics.impl import run as entity_economics

GA = Path(__file__).resolve().parents[2]
BREAKDOWN = json.loads((GA / "fixtures" / "halloween_breakdown.json").read_text(encoding="utf-8"))
SCORECARD = score({"breakdown": BREAKDOWN}, tools={"entity-economics": entity_economics})["scorecard"]
INPUT = {"days": [], "period": None, "unmatched": {"meta_only": [], "sales_only": []},
         "qa_passed": True, "campaigns": [], "scorecard": SCORECARD}


def _md() -> str:
    return run(INPUT)["markdown"]


def test_veredicto_explicado_en_lenguaje_llano() -> None:
    out = run(INPUT)
    assert out["verdict"] == "hold_budget"
    assert out["headline"] == "No subas el presupuesto todavía."
    md = out["markdown"]
    assert md.startswith("## Liliana / Promocional / Interacción / CBO / Advantage+ / Haloween\n")
    assert "Del 11 sep al 25 sep · solo ventas atribuidas a esta campaña." in md
    assert (
        "**No subas el presupuesto todavía.** Con las ventas confirmadas, cada $1 en anuncios "
        "trajo $1,43 en ventas; para que la pauta se pague sola necesitas al menos $2,00. "
        "Si se confirman los 2 pedidos que faltan ($106.940), llegaría a $2,15."
    ) in md


def test_que_hacer_en_orden_con_el_objetivo_exacto() -> None:
    md = _md()
    assert "### Qué hacer, en orden" in md
    acciones = [
        "1. **Cambia la tarjeta «Chatea con nosotros» del anuncio Carrusel / beneficios (Personalizada).** "
        "Trajo 7 chats y ningún pedido; la tarjeta «Velas aromáticas» del mismo anuncio trajo 6 chats y "
        "3 pedidos. Reemplázala por un producto con precio o quítala. _Confianza: media._",
        "2. **Dale seguimiento a 14 conversaciones abiertas de hace más de 2 días** (Abierta 6, "
        "Personalizada 5, Similar 2, Intereses 1). Son personas que preguntaron y no cerraron: "
        "escribirles cuesta menos que conseguir un chat nuevo. _Confianza: alta._",
        "3. **Dale más espacio a Carrusel / beneficios.** Cierra el 15% de sus chats (2 de 13) contra el "
        "7% de Video / Decoración (2 de 27), pero Meta casi no lo muestra en Abierta, Intereses y Similar. "
        "Para probarlo, pausa Video / Decoración unos días en uno de esos segmentos. "
        "_Confianza: baja: todavía son pocas ventas._",
        "4. **Vigila Similar.** Lleva $16.805 y 4 chats, sin ventas; con menos de 10 chats todavía es "
        "pronto para juzgar. Si llega a $55.667 sin vender, páusalo.",
        "5. **Vigila Imagen / Beneficios en Personalizada.** Lleva $11.557 y 4 chats, sin ventas; con "
        "menos de 10 chats todavía es pronto para juzgar. Si llega a $55.667 sin vender, páusalo.",
        "6. **Vigila Intereses.** Lleva $24.160 y 6 chats, con 1 venta confirmada; con menos de 10 "
        "chats todavía es pronto para juzgar.",
    ]
    pos = [md.index(a) for a in acciones]
    assert pos == sorted(pos)


def test_resumen_separa_confirmadas_por_confirmar_y_canceladas() -> None:
    md = _md()
    assert "| Ventas confirmadas | 4 · $211.600 |" in md
    assert "| Por confirmar | 2 · $106.940 |" in md
    assert "| Canceladas (no cuentan) | 1 · $49.500 |" in md
    assert "| Retorno confirmado | 1,43 · de cada $100 vendidos, $70 se fueron en anuncios |" in md
    assert "| Costo por venta · ticket promedio | $37.112 · $52.900 |" in md
    assert "| Presupuesto | a nivel campaña (CBO): Meta lo reparte entre segmentos |" in md


def test_tabla_de_segmentos_con_su_decision() -> None:
    md = _md()
    assert "| Personalizada | $66.972 | 45% | 17 | 2 | 1 | 1,58 (2,50 si se confirma) | Mantener: depende de confirmar 1 pedido |" in md
    assert "| Similar | $16.805 | 11% | 4 | 0 | 0 | 0,00 | Vigilar: pausar si llega a $55.667 sin vender |" in md
    assert "| Intereses | $24.160 | 16% | 6 | 1 | 0 | 2,19 | Vigilar: pocos chats para juzgar |" in md


def test_anuncios_con_entrega_y_resumen_de_los_que_no() -> None:
    md = _md()
    assert "| Carrusel / beneficios | Personalizada | $55.415 | 13 | 2 | 1,37 | Mantener: depende de confirmar 1 pedido |" in md
    assert "6 anuncios casi no se mostraron (menos del 3% del gasto cada uno)" in md
    assert "| «Chatea con nosotros» | Carrusel / beneficios (Personalizada) | 7 | 0 |" in md


def test_advertencias_de_datos_en_palabras() -> None:
    md = _md()
    assert "### Tener en cuenta" in md
    assert "1 pedido pendiente de pago ($45.000) y 1 sin verificar en Orders ($61.940) no cuentan en el retorno hasta confirmarse." in md
    assert "1 pedido cancelado ($49.500) no cuenta, aunque el chat lo marque como ganado." in md
    assert "9 chats de los últimos 2 días (desde el 24 sep) siguen abiertos: todavía pueden llegar ventas." in md
    assert "Retorno mínimo usado: 2,00 (ajústalo a tu margen)." in md


def test_sin_codigos_internos_en_el_reporte() -> None:
    md = _md()
    for code in ("hold_budget", "keep_pending", "no_delivery", "scale_budget", "rotate_creative"):
        assert code not in md


def test_la_narrativa_recibe_hallazgos_no_la_tabla_diaria() -> None:
    prompt = run(INPUT, ports={"llm": FixtureLLM(lambda user: user)})["narrative"]
    assert "No subas el presupuesto todavía" in prompt
    assert "Cambia la tarjeta «Chatea con nosotros»" in prompt
    assert "(confianza media)" in prompt
    assert "spend" not in prompt and "MER" not in prompt


class _SpyLLM:
    def __init__(self) -> None:
        self.system = ""

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        self.system = system
        return "No subas el presupuesto: cada $1 trajo $1,43 y el mínimo es $2,00."


def test_el_prompt_pide_explicar_a_alguien_no_experto() -> None:
    spy = _SpyLLM()
    out = run(INPUT, ports={"llm": spy})
    assert out["narrative"] == "No subas el presupuesto: cada $1 trajo $1,43 y el mínimo es $2,00."
    for rule in ("tuteando", "no es experta", "SOLO los números", "no propongas acciones", "150 palabras"):
        assert rule in spy.system


def test_el_guard_entiende_el_formato_colombiano() -> None:
    src = _narrate_user(INPUT)
    assert invented_numbers("Gastaste $148.446 y cada $1 trajo $1,43; con lo pendiente, $2,15.", src) == []
    assert invented_numbers("Gastaste $148.446 y vendiste $900.000.", src) == ["900.000"]
