"""Checks de código de la familia `variantes` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re
from collections import Counter

from src.plugins.chats.agent.sales.variant_enumeration import find_enumerated_variants
from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    PRICE_RE,
    failed,
    is_legacy,
    not_applicable,
    passed,
    quote,
    sent_texts,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

_PICKER_ERRORS = ("invalid_options", "not_enough_options", "no_rows")
_QUANTITY_Q = re.compile(r"cu[aá]nt[oa]s\b|\bcantidad\b", re.IGNORECASE)
_PRICE_INTENTS = ("products_list", "product_detail", "order_confirmation")


@code_check("VAR-01")
def var_01(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    texts = list(sent_texts(traj))
    if not texts:
        return not_applicable("VAR-01", "el bot no envió texto")
    if not ctx.catalog_available:
        return unknown("VAR-01", "catálogo no disponible para reconocer aromas y colores")
    for t, text in texts:
        hit = find_enumerated_variants(text, aromas=list(ctx.aromas), colors=list(ctx.colors))
        if hit:
            kind, labels = hit
            label = "aromas" if kind == "scent" else "colores"
            return failed(
                "VAR-01", t.turn,
                f"turno {t.turn}: {len(labels)} {label} enumerados en texto plano {quote(text)}",
            )
    return passed("VAR-01", "ningún texto enumera variantes")


@code_check("VAR-01b")
def var_01b(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("VAR-01b", "sin trazas: no se ven las guardas")
    for t in traj.turns:
        if "variant_enumeration_guard" in t.guards:
            return failed(
                "VAR-01b", t.turn,
                f"turno {t.turn}: el LLM enumeró variantes en texto y la guarda lo cambió por el picker {quote(t.llm_text)}",
            )
    return passed("VAR-01b", "la guarda de variantes no tuvo que actuar")


@code_check("VAR-02")
def var_02(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    calls = [(t, c) for t in traj.turns for c in t.tools_named("present_variant_picker")]
    if not calls:
        return not_applicable("VAR-02", "sin picker de variantes")
    if is_legacy(traj):
        return unknown("VAR-02", "sin trazas: no se ve el resultado del picker")
    for t, c in calls:
        if c.ok is False and (c.error in _PICKER_ERRORS or c.error):
            return failed("VAR-02", t.turn, f"turno {t.turn}: picker rechazado ({c.error})")
    return passed("VAR-02", f"{len(calls)} picker(s) válidos")


@code_check("VAR-03")
def var_03(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_picker = False
    for t in traj.turns:
        pickers = [c for c in t.tools_named("present_variant_picker") if c.ok is not False]
        any_picker = any_picker or bool(pickers)
        repeated = [k for k, n in Counter(str(c.args.get("variant_type") or "?") for c in pickers).items() if n > 1]
        if repeated:
            return failed("VAR-03", t.turn, f"turno {t.turn}: picker de {repeated[0]} duplicado")
    if not any_picker:
        return not_applicable("VAR-03", "sin picker de variantes")
    return passed("VAR-03", "sin pickers duplicados")


@code_check("VAR-04")
def var_04(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    choices = []
    for prev, t in zip(traj.turns, traj.turns[1:]):
        if "variant_picker" in prev.intents and t.inbound_text.startswith("[el cliente seleccionó:"):
            choices.append(t)
    if not choices:
        return not_applicable("VAR-04", "el cliente no eligió desde un picker")
    legacy = is_legacy(traj)
    for t in choices:
        recorded = t.tool_ok("set_order_slot") or (legacy and t.tool_attempted("set_order_slot"))
        if not recorded:
            if legacy:
                return unknown("VAR-04", "sin trazas: el turno no registró tools")
            return failed(
                "VAR-04", t.turn,
                f"turno {t.turn}: el cliente eligió {quote(t.inbound_text, 60)} y no quedó en el pedido",
            )
    return passed("VAR-04", f"{len(choices)} elección(es) registradas")


@code_check("VAR-06")
def var_06(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("VAR-06", "sin trazas: no se ve el pedido en cada turno")
    known = False
    for prev, t in zip(traj.turns, traj.turns[1:]):
        known = known or bool((prev.draft or {}).get("cantidad"))
        if not known:
            continue
        for text in t.sent_texts:
            if "?" in text and _QUANTITY_Q.search(text):
                return failed("VAR-06", t.turn, f"turno {t.turn}: vuelve a preguntar la cantidad {quote(text)}")
    if not known and not any((t.draft or {}).get("cantidad") for t in traj.turns):
        return not_applicable("VAR-06", "el pedido nunca tuvo cantidad")
    return passed("VAR-06", "no re-preguntó la cantidad")


@code_check("VAR-07")
def var_07(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    requests = [t for t in traj.turns if "shipping_flow" in t.intents or "order_confirmation" in t.intents]
    if not requests:
        return not_applicable("VAR-07", "no pidió confirmación ni datos de envío")
    first = requests[0]
    for t in traj.turns:
        if t.turn > first.turn:
            break
        same_turn_summary = t.turn == first.turn and "order_confirmation" in t.intents
        earlier = t.turn < first.turn
        if same_turn_summary or (
            earlier and (any(PRICE_RE.search(x) for x in t.sent_texts) or set(t.intents) & set(_PRICE_INTENTS))
        ):
            return passed("VAR-07", f"precio visible en el turno {t.turn}", first.turn)
    return failed(
        "VAR-07", first.turn,
        f"turno {first.turn}: pidió {'datos de envío' if 'shipping_flow' in first.intents else 'confirmación'} sin mostrar precio antes",
    )


@code_check("VAR-09")
def var_09(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    calls = [(t, c) for t in traj.turns for c in t.tools_named("send_quick_replies")]
    if not calls:
        return not_applicable("VAR-09", "sin botones rápidos")
    if is_legacy(traj):
        return unknown("VAR-09", "sin trazas: no se ven los botones rechazados")
    for t, c in calls:
        if c.error == "catalog_choice_not_allowed" or c.note("rejected_buttons"):
            return failed("VAR-09", t.turn, f"turno {t.turn}: botones con selectores de catálogo rechazados")
    return passed("VAR-09", "botones rápidos válidos")
