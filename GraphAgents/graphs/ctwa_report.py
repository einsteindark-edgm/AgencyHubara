"""Capability `ctwa-report` (reporter). El render determinista (tabla markdown + verdict
del periodo) es un nodo PURO; la narrativa interpretativa es un nodo LLM marcado (G1+,
temperature=0, structured output) que NUNCA computa — cita los números del analyzer. Acá
va el run PURO (sin LLM): números undefined → "—", nunca un valor adivinado. Las fechas
sin match se LISTAN (no se ocultan).

Con SCORECARD (el drill-down de UNA campaña, 2026-09-25) el reporte EXPLICA: veredicto en
lenguaje llano con su porqué, "qué hacer, en orden" nombrando el anuncio/segmento/tarjeta
exacto + evidencia + confianza, y las advertencias de datos en palabras. Sin scorecard
(análisis de cuenta completa) queda el render viejo.

- run(input, *, ports, tools) — PURA (G-RUN-SIG).
- build()                     — StateGraph con el nodo LLM aislado (G1+).
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_DASH = "—"

# El nodo LLM narrativo: cita los números del analyzer, NUNCA computa. temperature=0.
_NARRATE_SYSTEM = (
    "Eres un analista de anuncios de WhatsApp. Escribe una interpretación BREVE (máx 4 frases) en "
    "español neutro, tuteando, de los números que te paso. Reglas DURAS: (1) CITA solo los números "
    "EXACTOS que están en los datos; (2) NUNCA inventes ni calcules un número nuevo; (3) si el QA no "
    "reconcilia, advierte que los números no son confiables. Devuelve solo la prosa."
)
# Con el drill-down de la campaña (2026-09-25): la narrativa EXPLICA a la operadora qué hacer.
# El reporte viejo la llevaba a repetir la tabla diaria ("el 2026-09-22 aparece con spend
# 28306…") — ahora recibe el veredicto + las acciones priorizadas, no la tabla.
_NARRATE_SYSTEM_SCORECARD = (
    "Eres el analista de pauta de una tienda pequeña en Colombia. Le explicas a la dueña, que no es "
    "experta en marketing, qué pasó con su campaña de anuncios de WhatsApp y qué hacer ahora. Escribe "
    "en español neutro, tuteando, con frases cortas y sin jerga: si dices 'retorno', aclara que es "
    "cuánto vendió cada peso invertido.\n"
    "Estructura en Markdown:\n"
    "1. Un párrafo de 2 o 3 frases: el veredicto y por qué, con los 2 o 3 números que lo explican.\n"
    "2. 'Lo más importante:' y 2 o 3 viñetas con las primeras acciones de la lista, cada una con su "
    "porqué en una frase.\n"
    "3. Una frase final con lo que falta confirmar o vigilar.\n"
    "Reglas DURAS: (1) usa SOLO los números que aparecen en los datos, escritos igual; nunca calcules "
    "ni inventes una cifra; (2) no propongas acciones que no estén en la lista; (3) si una acción "
    "tiene confianza baja, dilo; (4) si el QA no reconcilia, empieza advirtiendo que los números no "
    "son confiables; (5) máximo 150 palabras."
)
_NUM = re.compile(r"\d[\d.,]*\d|\d")


def _to_value(tok: str) -> float | None:
    """Parsea un token numérico a su VALOR, resolviendo separador de miles vs decimal (es/us)."""
    t = re.sub(r"[^\d.,]", "", tok)
    if not t:
        return None
    if "." in t and "," in t:                       # ambos: el ÚLTIMO separador es el decimal
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:                                   # un solo ',': grupos de 3 → miles, si no decimal
        parts = t.split(",")
        t = t.replace(",", "") if (len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3)) else t.replace(",", ".")
    elif "." in t:                                   # un solo '.': 120.000 = miles; 5.0 / 0.40 = decimal
        parts = t.split(".")
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3):
            t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _narrate_user(input: dict) -> str:
    """Los números del analyzer, serializados para el prompt (la MISMA data que renderiza run())."""
    if input.get("scorecard"):
        return _narrate_user_scorecard(input)
    period = input.get("period")
    verdict = period["diagnosis"]["recommendation"] if period else "insufficient_data"
    pm = period["metrics"] if period else {}
    lines = [f"diagnóstico del periodo: {verdict}",
             f"MER del periodo: {_fmt(pm.get('mer'))}",
             f"drop-off del periodo: {_fmt(pm.get('drop_off_rate'))}",
             f"QA reconcilia: {input.get('qa_passed')}"]
    for d in input.get("days", []):
        lines.append(f"- {d['date']}: spend {d['spend_cop']}, conversaciones {d['conversations_started']}, "
                     f"órdenes {d['total_orders']}, recomendación {d['diagnosis']['recommendation']}")
    for c in input.get("campaigns", []):
        lines.append(f"- campaña {c.get('campaign_name', '')}: spend {c.get('spend_cop', 0)}, "
                     f"clicks {c.get('link_clicks', 0)}, conversaciones {c.get('conversations', 0)} "
                     f"(fuente {c.get('conversation_source', 'none')})")
    return "\n".join(lines)


def invented_numbers(narrative: str, source: str) -> list[str]:
    """Guard anti-alucinación: los tokens numéricos del narrative cuyo VALOR no está en la fuente.
    Compara VALORES, no dígitos — así `0.40`↔`40%` y `120000`↔`$120.000`/`120.000,00` son el MISMO
    número, pero un `50%` inventado NO colisiona con MER `5.0` (el bug del guard por-dígitos, L-20).
    Tolera |v|<10 (ruido de prosa: '5 días', '3 campañas' — daño bajo). Es lo que hace CONFIABLE el
    nodo LLM: si inventa una cifra financiera, la caza."""
    src: set[float] = set()
    for tok in _NUM.findall(source):
        v = _to_value(tok)
        if v is not None:
            src |= {round(v, 6), round(v * 100, 6), round(v / 100, 6)}  # valor + ratio↔%
    out = []
    for tok in _NUM.findall(narrative):
        v = _to_value(tok)
        if v is None or abs(v) < 10:  # números chicos: ruido de prosa, se toleran
            continue
        if not any(abs(v - s) < 0.01 for s in src):
            out.append(tok)
    return sorted(set(out))


def narrate(input: dict, *, llm) -> dict:
    """Nodo LLM MARCADO: le pasa los números del analyzer al LLM (temperature=0) y devuelve la
    prosa. NO computa — el LLM solo interpreta. `llm` es el port (FixtureLLM en golden, LiteLLMProxy
    en real). El guard `invented_numbers` (en el test + disponible para el caller) prueba que no
    alucinó un número. Con scorecard el prompt pide EXPLICAR a alguien no experto."""
    user = _narrate_user(input)
    system = _NARRATE_SYSTEM_SCORECARD if input.get("scorecard") else _NARRATE_SYSTEM
    text = llm.complete(system=system, user=user, temperature=0.0)
    return {"narrative": text.strip()}


def _fmt(v: str | None) -> str:
    """Presentación de un ratio (MER / drop-off): None → "—"; numérico → redondeado a 2
    decimales (el analyzer serializa Decimal con cola larga — '0.7860605266605528…' — que
    ni la tabla ni el prompt del LLM deben repetir; el LLM cita lo que recibe). No numérico
    → tal cual (no adivinar)."""
    if v is None:
        return _DASH
    try:
        return str(round(float(v), 2))
    except (TypeError, ValueError):
        return str(v)


# ── formato colombiano (presentación del scorecard) ──────────────────────────────

_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _money(v) -> str:
    if v is None:
        return _DASH
    n = int(Decimal(str(v)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return "$" + f"{n:,}".replace(",", ".")


def _ratio(v) -> str:
    if v is None:
        return _DASH
    return f"{Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}".replace(".", ",")


def _pct(v) -> str:
    if v is None:
        return _DASH
    return f"{int((Decimal(str(v)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))}%"


def _day(iso: str | None) -> str:
    if not iso:
        return _DASH
    d = date.fromisoformat(iso)
    return f"{d.day} {_MESES[d.month - 1]}"


def _n(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} y {items[-1]}"


def _clean(name: str) -> str:
    return " ".join((name or "").split())


# ── veredicto ────────────────────────────────────────────────────────────────────

def _verdict(sc: dict) -> tuple[str, str]:
    """(headline, párrafo) del veredicto de la campaña: la decisión + su porqué."""
    c = sc["campaign"]
    v, t, m = c["verdict"], c["totals"], c["metrics"]
    be = f"${_ratio(sc['thresholds']['breakeven_roas'])}"
    roas = f"${_ratio(m['roas'])}"
    unconf = t["pending_sales"] + t["unverified_sales"]
    unconf_rev = t["pending_revenue_cop"] + t["unverified_revenue_cop"]
    action, reason = v["action"], v["reason"]
    if action == "scale_budget":
        head = "Puedes subir el presupuesto."
        body = (f"Cada $1 en anuncios trajo {roas} en ventas confirmadas (el mínimo para que se pague "
                f"sola es {be}). Súbelo de a poco (20–30%) y vuelve a revisar en una semana.")
    elif action == "hold_budget" and reason == "pending_confirmation":
        head = "No subas el presupuesto todavía."
        faltan = "los " + _n(unconf, "pedido que falta", "pedidos que faltan") if unconf > 1 else "el pedido que falta"
        body = (f"Con las ventas confirmadas, cada $1 en anuncios trajo {roas} en ventas; para que la "
                f"pauta se pague sola necesitas al menos {be}. Si se confirman {faltan} "
                f"({_money(unconf_rev)}), llegaría a ${_ratio(c['potential_roas'])}.")
    elif action == "hold_budget":
        head = "Mantén el presupuesto como está."
        body = (f"La pauta se paga sola (cada $1 trajo {roas}; el mínimo es {be}), pero todavía no hay "
                f"margen para escalar: conviene subir desde ${_ratio(sc['thresholds']['scale_roas'])}.")
    elif action == "fix_before_scaling":
        head = "No escales: primero corrige."
        body = (f"Cada $1 en anuncios trajo {roas} en ventas confirmadas y el mínimo es {be}. "
                "Las acciones de abajo dicen dónde se pierde.")
    elif action == "reduce_budget":
        head = "Baja el presupuesto."
        body = (f"Cada $1 en anuncios trajo {roas} en ventas confirmadas: la pauta está costando más "
                "de lo que vende.")
    else:
        head = "Todavía no hay datos suficientes."
        body = (f"Llegaron {_n(t['chats'], 'chat', 'chats')}; para juzgar por ventas hacen falta al "
                f"menos {sc['thresholds']['min_chats']}.")
    return head, f"**{head}** {body}"


# ── acciones ─────────────────────────────────────────────────────────────────────

def _sales_phrase(e: dict) -> str:
    paid, unconf = e["paid_sales"], e["unconfirmed_sales"]
    if paid == 0 and unconf == 0:
        return "sin ventas"
    parts = []
    if paid:
        parts.append(_n(paid, "venta confirmada", "ventas confirmadas"))
    if unconf:
        parts.append(f"{unconf} por confirmar")
    return "con " + " y ".join(parts)


def _where(target: dict) -> str:
    return f"{target['label']} en {target['segment']}" if target.get("segment") else target["label"]


def _action_text(f: dict, sc: dict) -> str | None:
    """El texto de UNA acción (sin el sufijo de confianza). None = no es una acción numerada."""
    kind, e, tg = f["kind"], f["evidence"], f["target"]
    camp = sc["campaign"]
    if kind == "replace_card":
        return (f"**Cambia la tarjeta «{tg['label']}» del anuncio {tg['ad_label']} ({tg['segment']}).** "
                f"Trajo {e['chats']} chats y ningún pedido; la tarjeta «{e['sibling_headline']}» del mismo "
                f"anuncio trajo {e['sibling_chats']} chats y {_n(e['sibling_orders'], 'pedido', 'pedidos')}. "
                "Reemplázala por un producto con precio o quítala.")
    if kind == "follow_up":
        by = ", ".join(f"{label} {n}" for label, n in e["by_segment"])
        return (f"**Dale seguimiento a {e['stale_open_chats']} conversaciones abiertas de hace más de 2 días** "
                f"({by}). Son personas que preguntaron y no cerraron: escribirles cuesta menos que "
                "conseguir un chat nuevo.")
    if kind == "compare_pieces":
        text = (f"**Dale más espacio a {e['best_label']}.** Cierra el {_pct(e['best_chat_to_sale'])} de sus "
                f"chats ({e['best_paid_sales']} de {e['best_chats']}) contra el {_pct(e['worse_chat_to_sale'])} "
                f"de {e['worse_label']} ({e['worse_paid_sales']} de {e['worse_chats']})")
        if e["segments_without_best"]:
            return (f"{text}, pero Meta casi no lo muestra en {_join(e['segments_without_best'])}. "
                    f"Para probarlo, pausa {e['worse_label']} unos días en uno de esos segmentos.")
        return f"{text}."
    if kind == "watch":
        text = (f"**Vigila {_where(tg)}.** Lleva {_money(e['spend_cop'])} y {e['chats']} chats, "
                f"{_sales_phrase(e)}; con menos de {sc['thresholds']['min_chats']} chats todavía es "
                "pronto para juzgar.")
        if e.get("pause_at_cop"):
            text += f" Si llega a {_money(e['pause_at_cop'])} sin vender, páusalo."
        return text
    if kind == "pause":
        return (f"**Pausa {_where(tg)}.** Gastó {_money(e['spend_cop'])} sin ninguna venta: más de "
                f"{_ratio(sc['thresholds']['pause_multiple_of_cpa'])} veces lo que cuesta una venta en esta "
                f"campaña ({_money(camp['metrics']['cost_per_sale_cop'])}).")
    if kind == "rotate_creative":
        if f["reason"] == "frequency":
            why = f"En promedio, cada persona ya lo vio {_ratio(e['frequency'])} veces"
        elif f["reason"] == "cost_trend":
            why = (f"El costo por conversación subió {_pct(Decimal(e['cost_trend']) - 1)} entre la primera "
                   "y la segunda mitad del periodo")
        else:
            why = (f"La tasa de clics cayó {_pct(1 - Decimal(e['ctr_trend']))} entre la primera y la "
                   "segunda mitad del periodo")
        return (f"**Cambia la pieza {_where(tg)}.** {why}. Prepara una versión nueva (otra imagen o "
                "video del mismo producto).")
    if kind == "fix_conversion":
        return (f"**Revisa qué pasa después del chat en {_where(tg)}.** Cierra el {_pct(e['chat_to_sale'] or 0)} "
                f"de sus chats contra el {_pct(camp['metrics']['chat_to_sale'] or 0)} de toda la campaña: "
                "los chats llegan pero no compran (precio, oferta o la respuesta del bot).")
    if kind == "fix_cost":
        return (f"**{_where(tg)} consigue chats caros.** Cada chat cuesta {_money(e['cost_per_chat_cop'])} "
                f"contra {_money(camp['metrics']['cost_per_chat_cop'])} en toda la campaña; revisa la "
                "audiencia o la pieza.")
    if kind == "scale":
        return (f"**Sube el presupuesto de {tg['label']} 20–30%.** Cada $1 trajo ${_ratio(e['roas'])} con "
                f"{_n(e['paid_sales'], 'venta confirmada', 'ventas confirmadas')}; revisa en una semana.")
    return None


def _confidence_suffix(f: dict) -> str:
    if f["kind"] == "watch":
        return ""
    if f["confidence"] == "baja":
        return " _Confianza: baja: todavía son pocas ventas._" if f["kind"] == "compare_pieces" else " _Confianza: baja._"
    return f" _Confianza: {f['confidence']}._"


def _actions(sc: dict) -> list[tuple[str, dict]]:
    out = []
    for f in sc["findings"]:
        text = _action_text(f, sc)
        if text is not None:
            out.append((text, f))
    return out


# ── tablas ───────────────────────────────────────────────────────────────────────

def _decision_short(d: dict, t: dict) -> str:
    action = d["action"]
    if action == "no_delivery":
        return "Sin entrega: Meta casi no lo muestra"
    if action == "rotate_creative":
        return "Cambiar la pieza (desgaste)"
    if action == "pause":
        return "Pausar"
    if action == "watch":
        if d.get("pause_at_cop"):
            return f"Vigilar: pausar si llega a {_money(d['pause_at_cop'])} sin vender"
        return "Vigilar: pocos chats para juzgar"
    if action == "scale":
        return "Subir 20–30%"
    if action == "keep":
        return "Mantener: de lo que más rinde" if d["reason"] == "top_performer" else "Mantener"
    if action == "keep_pending":
        n = t["pending_sales"] + t["unverified_sales"]
        return f"Mantener: depende de confirmar {_n(n, 'pedido', 'pedidos')}"
    if action == "fix_conversion":
        return "Revisar qué pasa después del chat"
    if action == "fix_cost":
        return "Chats caros: revisar audiencia o pieza"
    return action


def _roas_cell(e: dict) -> str:
    t = e["totals"]
    cell = _ratio(e["metrics"]["roas"])
    if t["pending_sales"] + t["unverified_sales"] and e["potential_roas"] != e["metrics"]["roas"]:
        cell += f" ({_ratio(e['potential_roas'])} si se confirma)"
    return cell


_BUDGET = {
    "campaign": "a nivel campaña (CBO): Meta lo reparte entre segmentos",
    "adset": "por segmento (ABO): puedes subir o bajar cada uno",
}


def _summary(sc: dict) -> list[str]:
    c = sc["campaign"]
    t, m = c["totals"], c["metrics"]
    unconf = t["pending_sales"] + t["unverified_sales"]
    rows = [
        ("Gasto en anuncios", _money(t["spend_cop"])),
        ("Chats que llegaron a WhatsApp", str(t["chats"])),
        ("Ventas confirmadas", f"{t['paid_sales']} · {_money(t['paid_revenue_cop'])}"),
    ]
    if unconf:
        rows.append(("Por confirmar", f"{unconf} · {_money(t['pending_revenue_cop'] + t['unverified_revenue_cop'])}"))
    if t["cancelled_sales"]:
        rows.append(("Canceladas (no cuentan)", f"{t['cancelled_sales']} · {_money(t['cancelled_revenue_cop'])}"))
    share = m["ad_share_of_revenue"]
    ret = (f"{_ratio(m['roas'])} · de cada $100 vendidos, {_money(Decimal(share) * 100)} se fueron en anuncios"
           if share is not None else f"{_ratio(m['roas'])} · sin ventas confirmadas todavía")
    rows += [
        ("Retorno confirmado", ret),
        ("Costo por venta · ticket promedio", f"{_money(m['cost_per_sale_cop'])} · {_money(m['avg_ticket_cop'])}"),
        ("Presupuesto", _BUDGET.get(c["budget_level"], "no se pudo leer de Meta")),
    ]
    return ["### Cómo va la campaña", "", "| Dato | Valor |", "|---|---|",
            *[f"| {k} | {v} |" for k, v in rows]]


def _segments_table(sc: dict) -> list[str]:
    lines = ["### Segmentos", "",
             "| Segmento | Gasto | % del gasto | Chats | Ventas confirmadas | Por confirmar | Retorno | Qué hacer |",
             "|---|--:|--:|--:|--:|--:|--:|---|"]
    for s in sc["segments"]:
        t = s["totals"]
        lines.append(
            f"| {s['label']} | {_money(t['spend_cop'])} | {_pct(s['share_of_spend'])} | {t['chats']} | "
            f"{t['paid_sales']} | {t['pending_sales'] + t['unverified_sales']} | {_roas_cell(s)} | "
            f"{_decision_short(s['decision'], t)} |")
    return lines


def _ads_table(sc: dict) -> list[str]:
    shown = [a for a in sc["ads"] if a["decision"]["action"] != "no_delivery"]
    hidden = len(sc["ads"]) - len(shown)
    lines = ["### Anuncios", "",
             "| Anuncio | Segmento | Gasto | Chats | Ventas confirmadas | Frecuencia | Qué hacer |",
             "|---|---|--:|--:|--:|--:|---|"]
    for a in shown:
        t = a["totals"]
        lines.append(
            f"| {a['label']} | {a['segment_label']} | {_money(t['spend_cop'])} | {t['chats']} | "
            f"{t['paid_sales']} | {_ratio(a['fatigue']['frequency'])} | {_decision_short(a['decision'], t)} |")
    if hidden:
        lines += ["", f"_{_n(hidden, 'anuncio casi no se mostró', 'anuncios casi no se mostraron')} (menos del "
                      "3% del gasto cada uno): Meta decidió no mostrarlos y no hace falta tocarlos._"]
    return lines


def _cards_table(sc: dict) -> list[str]:
    if not sc["cards"]:
        return []
    lines = ["### Tarjetas del carrusel", "", "| Tarjeta | Anuncio | Chats | Pedidos |", "|---|---|--:|--:|"]
    for c in sc["cards"]:
        lines.append(f"| «{c['headline']}» | {c['ad_label']} ({c['segment_label']}) | {c['chats']} | {c['orders']} |")
    return lines


def _warning_text(w: dict) -> str | None:
    code, d = w["code"], w["detail"]
    if code == "orders_stale":
        return "Orders no respondió por algunos pedidos en este momento; sus montos salen de lo registrado en el chat."
    if code == "pending_confirmation":
        parts = []
        if d["pending_sales"]:
            parts.append(f"{_n(d['pending_sales'], 'pedido pendiente', 'pedidos pendientes')} de pago "
                         f"({_money(d['pending_revenue_cop'])})")
        if d["unverified_sales"]:
            parts.append(f"{d['unverified_sales']} sin verificar en Orders ({_money(d['unverified_revenue_cop'])})")
        verb = "cuentan" if d["pending_sales"] + d["unverified_sales"] > 1 else "cuenta"
        return f"{' y '.join(parts)} no {verb} en el retorno hasta confirmarse."
    if code == "cancelled_excluded":
        n = d["cancelled_sales"]
        if n == 1:
            return f"1 pedido cancelado ({_money(d['cancelled_revenue_cop'])}) no cuenta, aunque el chat lo marque como ganado."
        return f"{n} pedidos cancelados ({_money(d['cancelled_revenue_cop'])}) no cuentan, aunque el chat los marque como ganados."
    if code == "recent_open":
        return (f"{_n(d['chats'], 'chat', 'chats')} de los últimos 2 días (desde el {_day(d['since'])}) "
                "siguen abiertos: todavía pueden llegar ventas.")
    if code == "no_daily_series":
        return f"{_n(d['ads'], 'anuncio', 'anuncios')} sin serie diaria: el desgaste se juzgó solo por la frecuencia."
    return None


def _warnings(sc: dict) -> list[str]:
    items = [t for w in sc["warnings"] if (t := _warning_text(w))]
    items.append(f"Retorno mínimo usado: {_ratio(sc['thresholds']['breakeven_roas'])} (ajústalo a tu margen).")
    return ["### Tener en cuenta", "", *[f"- {t}" for t in items]]


def _daily_appendix(days: list) -> list[str]:
    if not days:
        return []
    lines = ["### Detalle por día", "", "_Ventas de los chats que empezaron ese día._", "",
             "| Fecha | Gasto | Conversaciones | Ventas | Retorno |", "|---|--:|--:|--:|--:|"]
    for d in days:
        lines.append(f"| {_day(d['date'])} | {_money(d['spend_cop'])} | {d['conversations_started']} | "
                     f"{d['total_orders']} | {_ratio(d['metrics'].get('mer'))} |")
    return lines


def _render_scorecard(input: dict) -> dict:
    sc = input["scorecard"]
    c = sc["campaign"]
    qa_passed = input.get("qa_passed")
    headline, paragraph = _verdict(sc)
    lines = [f"## {_clean(c['name'])}",
             f"Del {_day(c['since'])} al {_day(c['until'])} · solo ventas atribuidas a esta campaña.", ""]
    if qa_passed is False:
        lines += ["> **[ALERTA] QA NO RECONCILIA — números no confiables; no actúes sobre este reporte.**", ""]
    lines += [paragraph, "", "### Qué hacer, en orden", ""]
    actions = _actions(sc)
    if actions:
        lines += [f"{i}. {text}{_confidence_suffix(f)}" for i, (text, f) in enumerate(actions, start=1)]
    else:
        lines.append("No hay acciones urgentes: mantén la campaña como está y vuelve a revisar en unos días.")
    for block in (_summary(sc), _segments_table(sc), _ads_table(sc), _cards_table(sc), _warnings(sc),
                  _daily_appendix(input.get("days", []))):
        if block:
            lines += ["", *block]
    if qa_passed is not None:
        lines += ["", f"**QA (no-self-review):** {'los números reconcilian' if qa_passed else 'NO reconcilian — revisar'}"]
    return {"markdown": "\n".join(lines), "verdict": c["verdict"]["action"], "headline": headline,
            "qa_passed": qa_passed}


def _narrate_user_scorecard(input: dict) -> str:
    """Lo que el LLM necesita para EXPLICAR: el veredicto, los números clave ya formateados y las
    acciones priorizadas con su confianza. NO la tabla diaria (el LLM la repetía en prosa)."""
    sc = input["scorecard"]
    c = sc["campaign"]
    t, m = c["totals"], c["metrics"]
    _, paragraph = _verdict(sc)
    unconf = t["pending_sales"] + t["unverified_sales"]
    key = [f"gasto {_money(t['spend_cop'])}", f"{t['chats']} chats",
           f"{t['paid_sales']} ventas confirmadas por {_money(t['paid_revenue_cop'])}"]
    if unconf:
        key.append(f"{unconf} por confirmar por {_money(t['pending_revenue_cop'] + t['unverified_revenue_cop'])}")
    if t["cancelled_sales"]:
        key.append(f"{t['cancelled_sales']} canceladas por {_money(t['cancelled_revenue_cop'])}")
    key += [f"retorno confirmado {_ratio(m['roas'])} (mínimo {_ratio(sc['thresholds']['breakeven_roas'])}, "
            f"se escala desde {_ratio(sc['thresholds']['scale_roas'])})",
            f"costo por venta {_money(m['cost_per_sale_cop'])}", f"ticket promedio {_money(m['avg_ticket_cop'])}"]
    lines = [f"Campaña: {_clean(c['name'])}. Periodo: del {_day(c['since'])} al {_day(c['until'])}.",
             f"Veredicto: {paragraph.replace('**', '')}",
             f"Números clave: {'; '.join(key)}.",
             "Acciones en orden de prioridad:"]
    actions = _actions(sc)
    lines += [f"{i}. {text.replace('**', '')} (confianza {f['confidence']})"
              for i, (text, f) in enumerate(actions, start=1)] or ["(ninguna urgente)"]
    lines.append("Tener en cuenta:")
    lines += [f"- {w}" for w in (_warning_text(x) for x in sc["warnings"]) if w]
    qa = input.get("qa_passed")
    lines.append(f"QA: {'los números reconcilian' if qa else 'los números NO reconcilian' if qa is False else 'sin QA'}.")
    return "\n".join(lines)


def _render_legacy(input: dict) -> dict:
    days = input.get("days", [])
    period = input.get("period")
    unmatched = input.get("unmatched", {"meta_only": [], "sales_only": []})
    qa_passed = input.get("qa_passed")  # MF-4: el verdict del no-self-review gobierna la CONFIANZA
    campaigns = input.get("campaigns", [])  # el embudo por-campaña (complementado); opcional

    lines = ["## Hubara — Ads Analytics (CTWA)", ""]
    if qa_passed is False:  # el QA detectó que los números no reconcilian → marcar el reporte
        lines += ["> **[ALERTA] QA NO RECONCILIA — números no confiables; no actúes sobre este reporte.**", ""]
    lines.append("| Fecha | Spend | Conv | Drop-off | MER | Órdenes | Recomendación |")
    lines.append("|---|--:|--:|--:|--:|--:|---|")
    for d in days:
        m = d["metrics"]
        lines.append(
            f"| {d['date']} | {d['spend_cop']} | {d['conversations_started']} | "
            f"{_fmt(m['drop_off_rate'])} | {_fmt(m['mer'])} | {d['total_orders']} | "
            f"{d['diagnosis']['recommendation']} |"
        )

    verdict = period["diagnosis"]["recommendation"] if period else "insufficient_data"
    if period:
        m = period["metrics"]
        lines += ["", f"**Diagnóstico del periodo:** {verdict} "
                  f"(MER {_fmt(m['mer'])}, drop-off {_fmt(m['drop_off_rate'])})"]
    else:
        lines += ["", "**Diagnóstico del periodo:** sin días en común (no hay blend posible)."]

    if unmatched["meta_only"] or unmatched["sales_only"]:
        lines += ["", f"**Fechas sin match (excluidas del blend):** "
                  f"solo-Meta {unmatched['meta_only']} · solo-Ventas {unmatched['sales_only']}"]

    if campaigns:  # el embudo por-campaña: cada fila con su FUENTE auditable (no oculta el complemento)
        lines += ["", "## Embudo por campaña (CTWA)", "",
                  "| Campaña | Objetivo | Spend | Clicks | Conversaciones | Fuente |",
                  "|---|---|--:|--:|--:|---|"]
        for c in campaigns:
            src = c.get("conversation_source", "none")
            flag = "" if src in ("insights", "entities") else " ⚠ sin señal"
            lines.append(
                f"| {c.get('campaign_name', '')} | {c.get('objective', '')} | "
                f"{c.get('spend_cop', 0)} | {c.get('link_clicks', 0)} | "
                f"{c.get('conversations', 0)} | {src}{flag} |"
            )

    if qa_passed is not None:
        lines += ["", f"**QA (no-self-review):** {'reconcilia' if qa_passed else 'NO reconcilia — revisar'}"]
    return {"markdown": "\n".join(lines), "verdict": verdict, "qa_passed": qa_passed}


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    out = _render_scorecard(input) if input.get("scorecard") else _render_legacy(input)

    # Nodo LLM por el PORT (G-PORT, mismo patrón que meta-insights.run()→ports['meta_marketing_api']):
    # si el loader inyecta el port `llm` (porque el manifest lo `consumes:`), tejemos la narrativa
    # interpretativa. El LLM NO computa — cita los números del analyzer; el guard `invented_numbers`
    # DESCARTA la prosa si inventa una cifra financiera ausente (seguridad del LLM real: el cliente
    # nunca ve un número alucinado). Sin port → render puro (G-DET; el port es opt-in). La lógica
    # vive UNA vez acá → `build()` la corre por el mismo `run()` (sin drift run↔build, L-25).
    llm = (ports or {}).get("llm")
    if llm is not None:
        try:
            narrative = narrate(input, llm=llm)["narrative"]
        except Exception as e:  # noqa: BLE001 — la narrativa es OPCIONAL y best-effort: una falla del
            # LLM (proxy inalcanzable, timeout, respuesta malformada) NO debe tumbar el reporte
            # determinista (G-DET: el render de arriba es PURO, no toca red). Degradamos VISIBLE: un
            # marcador para el cliente + el error COMPLETO en `narrative_error` (trace/observabilidad).
            # No se traga — se reubica de fatal a diagnóstico, igual que `narrative_invented` reubica una
            # alucinación. Run real en AWS: `[Errno 99]` porque el proxy LiteLLM vive en otra caja EC2 y
            # `localhost:4000` no resuelve desde la de GraphAgents (L-26).
            out["narrative"] = "[narrativa no disponible — el modelo no respondió (ver narrative_error)]"
            out["narrative_error"] = f"{type(e).__name__}: {e}"
            return out
        invented = invented_numbers(narrative, _narrate_user(input))
        out["narrative"] = (
            narrative if not invented
            # NO echamos las cifras inventadas al cliente (las veía como si fueran reales); el detalle
            # va al guard/observabilidad. El cliente solo ve que la narrativa no es confiable.
            else "[narrativa interpretativa descartada — el modelo citó cifras ausentes del análisis]"
        )
        out["narrative_invented"] = invented  # diagnóstico (qué inventó) — para el trace, no para el cliente
    return out


def build(*, llm=None):
    """`StateGraph` LangGraph. El nodo `report` corre el `run()` — render determinista (tabla +
    verdict + embudo, o el scorecard explicado) y, si se le pasa el port `llm`, la narrativa
    interpretativa (el nodo LLM por el PORT, temperature=0, que cita los números y NO computa; el
    guard `invented_numbers` la descarta si inventa una cifra). La lógica del LLM vive UNA vez en
    `run()` (sin drift run↔build, L-24): `build()` solo THREAD-ea el port. **Opt-in**: sin `llm` el
    grafo es el render puro (G-DET; el supervisor compuesto y el golden del render no cambian). El
    vendor real (`LiteLLMProxy`, deepseek-flash vía el proxy del central) lo inyecta el runtime durable."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    class State(TypedDict, total=False):
        days: list          # blended-economics
        period: dict        # blended-economics (None si no hay días en común)
        unmatched: dict     # blended-economics
        qa_passed: bool     # numbers-qa (gobierna la CONFIANZA del reporte, MF-4)
        campaigns: list     # ctwa-campaign-funnel (el embudo por-campaña; opcional)
        scorecard: dict     # ctwa-scorecard (el drill-down de UNA campaña; None = cuenta completa)
        markdown: str       # ← run()
        verdict: str        # ← run()
        headline: str       # ← run() (solo con scorecard: el veredicto en una frase)
        narrative: str      # ← run() vía el port llm (solo si se inyecta)

    def report(state: State) -> dict:
        return run(dict(state), ports={"llm": llm} if llm is not None else None)

    g = StateGraph(State)
    g.add_node("report", report)
    g.add_edge(START, "report")
    g.add_edge("report", END)
    return g.compile(name="ctwa-report")
