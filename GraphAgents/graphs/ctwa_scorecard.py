"""Capability `ctwa-scorecard` (analyzer) — el drill-down de UNA campaña CTWA:
campaña → segmento → anuncio → tarjeta, con una decisión por nivel. PURO (G-DET): las
métricas salen de la tool `entity-economics` (la misma con que numbers-qa reconcilia) y
las decisiones de tablas de umbrales fijas. El LLM no aparece acá — el reporter narra.

Por qué existe (Halloween, 2026-09-25): el pod decía `scale_budget` con MER 3.05, pero
mezclaba toda la cuenta con las ventas de toda la tienda. Con las ventas atribuidas y
confirmadas el retorno era 1.43 (2.15 si se confirmaban 2 pedidos). Reglas que fija:

1. Una campaña a la vez; solo ventas CONFIRMADAS (pagadas) en el retorno. Pendientes y
   sin verificar van aparte ("por confirmar"); canceladas se excluyen.
2. El presupuesto se lee de `budget_level`: en CBO nunca se "sube un segmento".
3. La tarjeta que no vende se detecta por el headline de cada chat.
4. Desgaste por anuncio: frecuencia + tendencia de la serie diaria, nunca el MER del día.
5. Pocos chats → "vigilar" con el umbral de pausa explícito, no una decisión.

- run(input, *, ports, tools) — PURA (G-RUN-SIG, golden-replay).
- build()                     — StateGraph LangGraph (G1+).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

#: Retorno (ventas/gasto) bajo el cual la pauta no se paga sola. Configurable por el
#: central (`breakdown.targets.breakeven_roas`) porque depende del margen del negocio.
DEFAULT_BREAKEVEN_ROAS = Decimal("2.0")
#: Escalar pide holgura sobre el equilibrio: escalar baja el retorno marginal.
SCALE_MULTIPLE = Decimal("1.5")
#: Chats mínimos para juzgar una entidad por sus VENTAS (con menos, 0 ventas es azar).
MIN_CHATS = 10
#: Pausar una entidad sin ventas cuando gastó 1.5× lo que cuesta una venta en la campaña.
PAUSE_MULTIPLE_OF_CPA = Decimal("1.5")
#: Un anuncio con menos del 3% del gasto = Meta casi no lo muestra; no hay nada que tocar.
MIN_DELIVERY_SHARE = Decimal("0.03")
#: Desgaste: la misma persona lo vio 3+ veces en promedio.
FATIGUE_FREQUENCY = Decimal("3.0")
#: Desgaste: el costo por conversación sube 30%+ entre la 1ra y la 2da mitad de la serie.
FATIGUE_COST_RISE = Decimal("1.3")
#: Desgaste: el CTR cae 30%+ entre mitades.
FATIGUE_CTR_DROP = Decimal("0.7")
#: Serie diaria mínima para medir tendencia (días y conversaciones por mitad).
TREND_MIN_DAYS = 4
TREND_MIN_CONV_PER_HALF = 3
#: Tarjeta del carrusel: chats mínimos por headline para compararla.
CARD_MIN_CHATS = 5
#: Seguimiento: chats abiertos de más de 2 días que justifican una acción.
FOLLOW_UP_MIN = 5
#: Comparar piezas: una convierte ≥1.5× la otra.
PIECE_MULTIPLE = Decimal("1.5")

_OPEN_STATES = frozenset({"calificado", "activo", "cotizado", "nuevo"})
_ORDER_KEYS = ("paid", "pending", "unverified", "cancelled")
_ADDITIVE = (
    "spend_cop", "impressions", "link_clicks", "conversations", "chats",
    "paid_sales", "paid_revenue_cop", "pending_sales", "pending_revenue_cop",
    "unverified_sales", "unverified_revenue_cop", "cancelled_sales", "cancelled_revenue_cop",
    "open_chats", "stale_open_chats",
)


# ── helpers puros ────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _segment_label(name: str) -> str:
    """'LILIANA / abierta / Conversiones / …' → 'Abierta' (la convención de nombres del
    operador: marca / audiencia / …). Sin esa forma, el nombre completo."""
    parts = [" ".join(p.split()) for p in (name or "").split("/")]
    return _cap(parts[1]) if len(parts) >= 3 and parts[1] else " ".join((name or "").split())


def _piece_label(name: str) -> str:
    """'Liliana / Presentación / Video / Decoración / …' → 'Video / Decoración'."""
    parts = [" ".join(p.split()) for p in (name or "").split("/")]
    return _cap(f"{parts[2]} / {parts[3]}") if len(parts) >= 4 else " ".join((name or "").split())


def _dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def _q(num: int, den: int) -> str | None:
    return None if not den else str(Decimal(num) / Decimal(den))


def _unconfirmed(t: dict) -> int:
    return t["pending_sales"] + t["unverified_sales"]


def _confidence(chats: int) -> str:
    return "alta" if chats >= 20 else "media" if chats >= MIN_CHATS else "baja"


# ── totales ──────────────────────────────────────────────────────────────────────

def _ad_totals(ad: dict, stale_cutoff: str) -> dict:
    meta = ad.get("meta") or {}
    t = {k: 0 for k in _ADDITIVE}
    t.update({
        "spend_cop": int(meta.get("spend_cop") or 0),
        "impressions": int(meta.get("impressions") or 0),
        "link_clicks": int(meta.get("link_clicks") or 0),
        "conversations": int(meta.get("conversations") or 0),
    })
    t["reach"] = meta.get("reach")
    chats = ad.get("chats") or []
    t["chats"] = len(chats)
    for c in chats:
        order = c.get("order")
        status = (order or {}).get("status")
        if status in _ORDER_KEYS:
            t[f"{status}_sales"] += 1
            t[f"{status}_revenue_cop"] += int(order.get("value_cop") or 0)
        elif order is None and c.get("state") in _OPEN_STATES:
            t["open_chats"] += 1
            if c.get("date", "") <= stale_cutoff:
                t["stale_open_chats"] += 1
    return t


def _sum(totals: list[dict]) -> dict:
    out = {k: sum(t[k] for t in totals) for k in _ADDITIVE}
    out["reach"] = None  # el alcance no se suma (personas repetidas entre anuncios)
    return out


def _economics(t: dict, metrics) -> dict:
    return metrics(payload={k: t[k] for k in (
        "spend_cop", "impressions", "reach", "link_clicks", "conversations",
        "chats", "paid_sales", "paid_revenue_cop")})


def _potential(t: dict) -> str | None:
    rev = t["paid_revenue_cop"] + t["pending_revenue_cop"] + t["unverified_revenue_cop"]
    return _q(rev, t["spend_cop"])


# ── desgaste ─────────────────────────────────────────────────────────────────────

def _fatigue(ad: dict, t: dict, metrics: dict) -> dict:
    daily = sorted(ad.get("daily") or [], key=lambda d: d["date"])
    out = {"frequency": metrics["frequency"], "cost_trend": None, "ctr_trend": None,
           "trend_evaluated": False}
    if len(daily) < TREND_MIN_DAYS:
        return out
    half = len(daily) // 2
    a, b = daily[:half], daily[half:]

    def tot(rows: list, k: str) -> int:
        return sum(int(r.get(k) or 0) for r in rows)

    s1, s2 = tot(a, "spend_cop"), tot(b, "spend_cop")
    c1, c2 = tot(a, "conversations"), tot(b, "conversations")
    k1, k2 = tot(a, "link_clicks"), tot(b, "link_clicks")
    i1, i2 = tot(a, "impressions"), tot(b, "impressions")
    if c1 >= TREND_MIN_CONV_PER_HALF and c2 >= TREND_MIN_CONV_PER_HALF and s1:
        out["cost_trend"] = str(Decimal(s2 * c1) / Decimal(s1 * c2))
        out["trend_evaluated"] = True
    if k1 and i2:
        out["ctr_trend"] = str(Decimal(k2 * i1) / Decimal(k1 * i2))
        out["trend_evaluated"] = True
    return out


def _fatigue_reason(f: dict) -> str | None:
    if f["frequency"] is not None and Decimal(f["frequency"]) >= FATIGUE_FREQUENCY:
        return "frequency"
    if f["cost_trend"] is not None and Decimal(f["cost_trend"]) >= FATIGUE_COST_RISE:
        return "cost_trend"
    if f["ctr_trend"] is not None and Decimal(f["ctr_trend"]) <= FATIGUE_CTR_DROP:
        return "ctr_trend"
    return None


# ── decisiones ───────────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, campaign_totals: dict, campaign_metrics: dict, breakeven: Decimal,
                 budget_level: str) -> None:
        self.spend = campaign_totals["spend_cop"]
        self.breakeven = breakeven
        self.scale = breakeven * SCALE_MULTIPLE
        self.budget_level = budget_level
        cpa = _dec(campaign_metrics["cost_per_sale_cop"])
        self.pause_at = (
            None if cpa is None
            else int((cpa * PAUSE_MULTIPLE_OF_CPA).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        )
        self.chat_to_sale = _dec(campaign_metrics["chat_to_sale"]) or Decimal(0)


def _share(t: dict, ctx: _Ctx) -> Decimal:
    return Decimal(t["spend_cop"]) / Decimal(ctx.spend) if ctx.spend else Decimal(0)


def _judge_by_sales(t: dict, m: dict, potential: str | None, ctx: _Ctx, *, can_scale: bool) -> dict:
    """La parte común de segmento y anuncio, una vez descartados entrega y desgaste."""
    no_sales = t["paid_sales"] == 0 and _unconfirmed(t) == 0
    if no_sales and ctx.pause_at is not None and t["spend_cop"] >= ctx.pause_at:
        return {"action": "pause", "reason": "no_sales_after_spend"}
    if t["chats"] < MIN_CHATS:
        return {"action": "watch", "reason": "few_chats",
                "pause_at_cop": ctx.pause_at if no_sales else None}
    roas = _dec(m["roas"]) or Decimal(0)
    if roas >= ctx.scale:
        if can_scale:
            return {"action": "scale", "reason": "adset_budget"}
        return {"action": "keep", "reason": "top_performer"}
    if roas >= ctx.breakeven:
        return {"action": "keep", "reason": "profitable"}
    if potential is not None and Decimal(potential) >= ctx.breakeven:
        return {"action": "keep_pending", "reason": "pending_confirmation"}
    c2s = _dec(m["chat_to_sale"]) or Decimal(0)
    if c2s < ctx.chat_to_sale:
        return {"action": "fix_conversion", "reason": "chats_do_not_buy"}
    return {"action": "fix_cost", "reason": "expensive_chats"}


def _segment_decision(t: dict, m: dict, potential: str | None, ctx: _Ctx) -> dict:
    if _share(t, ctx) < MIN_DELIVERY_SHARE:
        return {"action": "no_delivery", "reason": "meta_barely_shows_it"}
    return _judge_by_sales(t, m, potential, ctx, can_scale=ctx.budget_level == "adset")


def _ad_decision(t: dict, m: dict, potential: str | None, fatigue: dict, ctx: _Ctx) -> dict:
    if _share(t, ctx) < MIN_DELIVERY_SHARE:
        return {"action": "no_delivery", "reason": "meta_barely_shows_it"}
    reason = _fatigue_reason(fatigue)
    if reason is not None:
        return {"action": "rotate_creative", "reason": reason}
    return _judge_by_sales(t, m, potential, ctx, can_scale=False)


def _campaign_verdict(t: dict, m: dict, potential: str | None, ctx: _Ctx) -> dict:
    if t["chats"] < MIN_CHATS:
        return {"action": "insufficient_data", "reason": "few_chats"}
    roas = _dec(m["roas"])
    if roas is None:
        return {"action": "insufficient_data", "reason": "no_spend"}
    if roas >= ctx.scale and t["paid_sales"] >= 5:
        return {"action": "scale_budget", "reason": "profitable"}
    if roas >= ctx.breakeven:
        return {"action": "hold_budget", "reason": "profitable_not_scalable"}
    if potential is not None and Decimal(potential) >= ctx.breakeven:
        return {"action": "hold_budget", "reason": "pending_confirmation"}
    if roas >= 1:
        return {"action": "fix_before_scaling", "reason": "below_breakeven"}
    return {"action": "reduce_budget", "reason": "losing_money"}


# ── tarjetas (headline de cada chat) ─────────────────────────────────────────────

def _cards(ad: dict) -> list[dict]:
    groups: dict[str, dict] = {}
    for c in ad.get("chats") or []:
        key = _norm(c.get("headline") or "")
        if not key:
            continue
        g = groups.setdefault(key, {"headline": " ".join((c.get("headline") or "").split()),
                                    "chats": 0, "orders": 0, "paid_sales": 0})
        g["chats"] += 1
        status = (c.get("order") or {}).get("status")
        if status in ("paid", "pending", "unverified"):
            g["orders"] += 1
        if status == "paid":
            g["paid_sales"] += 1
    if len(groups) < 2:
        return []
    return sorted(groups.values(), key=lambda g: (-g["chats"], g["headline"]))


# ── run ──────────────────────────────────────────────────────────────────────────

def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    breakdown = input.get("breakdown")
    if not breakdown:
        return {"scorecard": None}
    metrics = (tools or {})["entity-economics"]

    camp = breakdown.get("campaign") or {}
    window = breakdown.get("window") or {}
    until = date.fromisoformat(window["until"])
    stale_cutoff = (until - timedelta(days=2)).isoformat()
    recent_since = (until - timedelta(days=1)).isoformat()
    breakeven = Decimal(str((breakdown.get("targets") or {}).get("breakeven_roas", DEFAULT_BREAKEVEN_ROAS)))
    budget_level = camp.get("budget_level") or "unknown"

    raw_ads = breakdown.get("ads") or []
    ad_totals = [_ad_totals(a, stale_cutoff) for a in raw_ads]
    c_tot = _sum(ad_totals)
    c_met = _economics(c_tot, metrics)
    c_pot = _potential(c_tot)
    ctx = _Ctx(c_tot, c_met, breakeven, budget_level)

    # anuncios
    ads = []
    for raw, t in zip(raw_ads, ad_totals):
        m = _economics(t, metrics)
        pot = _potential(t)
        fat = _fatigue(raw, t, m)
        ads.append({
            "id": raw["ad_id"], "name": raw.get("ad_name", ""), "label": _piece_label(raw.get("ad_name", "")),
            "segment_id": raw.get("adset_id"), "segment_label": _segment_label(raw.get("adset_name", "")),
            "totals": t, "metrics": m, "potential_roas": pot,
            "share_of_spend": _q(t["spend_cop"], ctx.spend), "fatigue": fat,
            "decision": _ad_decision(t, m, pot, fat, ctx),
            "has_daily": bool(raw.get("daily")),
            "_chats": raw.get("chats") or [],
        })
    ads.sort(key=lambda a: (-a["totals"]["spend_cop"], a["id"]))

    # segmentos
    seg_order: list[str] = []
    seg_names: dict[str, str] = {}
    for raw in raw_ads:
        sid = raw.get("adset_id")
        if sid not in seg_names:
            seg_order.append(sid)
            seg_names[sid] = raw.get("adset_name", "")
    segments = []
    for sid in seg_order:
        members = [a for a in ads if a["segment_id"] == sid]
        t = _sum([a["totals"] for a in members])
        m = _economics(t, metrics)
        pot = _potential(t)
        segments.append({
            "id": sid, "name": seg_names[sid], "label": _segment_label(seg_names[sid]),
            "totals": t, "metrics": m, "potential_roas": pot,
            "share_of_spend": _q(t["spend_cop"], ctx.spend),
            "decision": _segment_decision(t, m, pot, ctx),
        })
    segments.sort(key=lambda s: (-s["totals"]["spend_cop"], s["id"]))
    seg_by_id = {s["id"]: s for s in segments}

    # piezas (el mismo creativo en varios segmentos)
    piece_groups: dict[str, list[dict]] = {}
    for a in ads:
        piece_groups.setdefault(_norm(a["name"]), []).append(a)
    pieces = []
    for members in piece_groups.values():
        t = _sum([a["totals"] for a in members])
        pieces.append({
            "label": members[0]["label"], "name": members[0]["name"],
            "ad_ids": [a["id"] for a in members],
            "totals": t, "metrics": _economics(t, metrics), "potential_roas": _potential(t),
            "delivered_in": [a["segment_label"] for a in members
                             if a["decision"]["action"] != "no_delivery"],
        })
    pieces.sort(key=lambda p: (-p["totals"]["spend_cop"], p["label"]))

    # tarjetas
    cards = []
    for a in ads:
        for g in _cards({"chats": a["_chats"]}):
            cards.append({"ad_id": a["id"], "ad_label": a["label"], "segment_label": a["segment_label"], **g})

    verdict = _campaign_verdict(c_tot, c_met, c_pot, ctx)
    campaign = {
        "id": camp.get("id"), "name": camp.get("name", ""), "budget_level": budget_level,
        "since": window.get("since"), "until": window.get("until"),
        "totals": c_tot, "metrics": c_met, "potential_roas": c_pot, "verdict": verdict,
    }
    findings = _findings(campaign, segments, ads, pieces, cards, seg_by_id, ctx)
    warnings = _warnings(breakdown, c_tot, ads, raw_ads, recent_since)
    for a in ads:
        a.pop("_chats")
    return {"scorecard": {
        "campaign": campaign, "segments": segments, "ads": ads, "pieces": pieces, "cards": cards,
        "findings": findings, "warnings": warnings,
        "thresholds": {
            "breakeven_roas": str(breakeven), "scale_roas": str(ctx.scale), "min_chats": MIN_CHATS,
            "pause_multiple_of_cpa": str(PAUSE_MULTIPLE_OF_CPA), "pause_at_cop": ctx.pause_at,
            "min_delivery_share": str(MIN_DELIVERY_SHARE), "fatigue_frequency": str(FATIGUE_FREQUENCY),
            "fatigue_cost_rise": str(FATIGUE_COST_RISE), "fatigue_ctr_drop": str(FATIGUE_CTR_DROP),
        },
    }}


# ── hallazgos priorizados ────────────────────────────────────────────────────────

def _entity_target(kind: str, e: dict) -> dict:
    t = {"type": kind, "id": e["id"], "label": e["label"]}
    if kind == "ad":
        t["segment"] = e["segment_label"]
    return t


def _entity_evidence(e: dict) -> dict:
    t, m = e["totals"], e["metrics"]
    return {"spend_cop": t["spend_cop"], "chats": t["chats"], "paid_sales": t["paid_sales"],
            "paid_revenue_cop": t["paid_revenue_cop"], "unconfirmed_sales": _unconfirmed(t),
            "roas": m["roas"], "potential_roas": e["potential_roas"],
            "cost_per_chat_cop": m["cost_per_chat_cop"], "chat_to_sale": m["chat_to_sale"],
            "pause_at_cop": e["decision"].get("pause_at_cop")}


def _findings(campaign: dict, segments: list, ads: list, pieces: list, cards: list,
              seg_by_id: dict, ctx: _Ctx) -> list[dict]:
    t, m = campaign["totals"], campaign["metrics"]
    camp_target = {"type": "campaign", "id": campaign["id"], "label": campaign["name"]}
    out: list[dict] = [{
        "kind": "campaign_verdict", "level": "campaign", "target": camp_target,
        "action": campaign["verdict"]["action"], "reason": campaign["verdict"]["reason"],
        "evidence": {
            "spend_cop": t["spend_cop"], "chats": t["chats"], "paid_sales": t["paid_sales"],
            "paid_revenue_cop": t["paid_revenue_cop"], "roas": m["roas"],
            "potential_roas": campaign["potential_roas"], "unconfirmed_sales": _unconfirmed(t),
            "unconfirmed_revenue_cop": t["pending_revenue_cop"] + t["unverified_revenue_cop"],
            "cost_per_sale_cop": m["cost_per_sale_cop"], "avg_ticket_cop": m["avg_ticket_cop"],
            "ad_share_of_revenue": m["ad_share_of_revenue"], "breakeven_roas": str(ctx.breakeven),
            "scale_roas": str(ctx.scale), "budget_level": campaign["budget_level"],
        },
        "confidence": _confidence(t["chats"]),
    }]

    def entity_findings(action: str, kind: str) -> list[dict]:
        found = []
        for s in segments:
            if s["decision"]["action"] == action:
                found.append({"kind": kind, "level": "segment", "target": _entity_target("segment", s),
                              "action": action, "reason": s["decision"]["reason"],
                              "evidence": _entity_evidence(s), "confidence": _confidence(s["totals"]["chats"])})
        for a in ads:
            seg_action = seg_by_id[a["segment_id"]]["decision"]["action"]
            if a["decision"]["action"] == action and seg_action != action and seg_action != "pause":
                ev = _entity_evidence(a)
                if action == "rotate_creative":
                    ev.update({k: a["fatigue"][k] for k in ("frequency", "cost_trend", "ctr_trend")})
                found.append({"kind": kind, "level": "ad", "target": _entity_target("ad", a),
                              "action": action, "reason": a["decision"]["reason"], "evidence": ev,
                              "confidence": _confidence(a["totals"]["chats"])})
        return found

    out += [f for f in entity_findings("scale", "scale") if f["level"] == "segment"]
    out += entity_findings("pause", "pause")
    out += entity_findings("rotate_creative", "rotate_creative")

    # tarjeta que no vende (headline con chats y 0 pedidos mientras otra del mismo anuncio vende)
    by_ad: dict[str, list] = {}
    for c in cards:
        by_ad.setdefault(c["ad_id"], []).append(c)
    for ad_id, group in by_ad.items():
        for c in group:
            siblings = [s for s in group if s is not c and s["orders"] > 0]
            if c["chats"] >= CARD_MIN_CHATS and c["orders"] == 0 and siblings:
                best = max(siblings, key=lambda s: (s["orders"], s["chats"]))
                out.append({
                    "kind": "replace_card", "level": "card",
                    "target": {"type": "card", "id": ad_id, "label": c["headline"],
                               "segment": c["segment_label"], "ad_label": c["ad_label"]},
                    "action": "replace_card", "reason": "card_does_not_sell",
                    "evidence": {"chats": c["chats"], "orders": c["orders"],
                                 "sibling_headline": best["headline"], "sibling_chats": best["chats"],
                                 "sibling_orders": best["orders"]},
                    "confidence": "media" if best["chats"] >= CARD_MIN_CHATS else "baja",
                })

    # seguimiento: conversaciones abiertas que se enfrían
    if t["stale_open_chats"] >= FOLLOW_UP_MIN:
        by_seg = sorted(([s["label"], s["totals"]["stale_open_chats"]] for s in segments
                         if s["totals"]["stale_open_chats"] > 0), key=lambda x: (-x[1], x[0]))
        out.append({
            "kind": "follow_up", "level": "campaign", "target": camp_target,
            "action": "follow_up_open_chats", "reason": "open_chats_cooling",
            "evidence": {"stale_open_chats": t["stale_open_chats"],
                         "recent_open_chats": t["open_chats"] - t["stale_open_chats"],
                         "by_segment": by_seg},
            "confidence": "alta",
        })

    out += entity_findings("fix_conversion", "fix_conversion")
    out += entity_findings("fix_cost", "fix_cost")

    # comparar piezas: una convierte ≥1.5× la otra
    judged = [p for p in pieces if p["totals"]["chats"] >= MIN_CHATS and p["metrics"]["chat_to_sale"] is not None]
    if len(judged) >= 2:
        best = max(judged, key=lambda p: (Decimal(p["metrics"]["chat_to_sale"]), p["label"]))
        worse = min(judged, key=lambda p: (Decimal(p["metrics"]["chat_to_sale"]), p["label"]))
        b, w = Decimal(best["metrics"]["chat_to_sale"]), Decimal(worse["metrics"]["chat_to_sale"])
        if best is not worse and b > 0 and b >= w * PIECE_MULTIPLE:
            weakest = min(best["totals"]["paid_sales"], worse["totals"]["paid_sales"])
            out.append({
                "kind": "compare_pieces", "level": "piece",
                "target": {"type": "piece", "id": best["ad_ids"][0], "label": best["label"]},
                "action": "test_best_piece", "reason": "piece_converts_better",
                "evidence": {
                    "best_label": best["label"], "best_chats": best["totals"]["chats"],
                    "best_paid_sales": best["totals"]["paid_sales"],
                    "best_chat_to_sale": best["metrics"]["chat_to_sale"],
                    "worse_label": worse["label"], "worse_chats": worse["totals"]["chats"],
                    "worse_paid_sales": worse["totals"]["paid_sales"],
                    "worse_chat_to_sale": worse["metrics"]["chat_to_sale"],
                    "segments_without_best": [s for s in worse["delivered_in"] if s not in best["delivered_in"]],
                },
                "confidence": "alta" if weakest >= 10 else "media" if weakest >= 5 else "baja",
            })

    watch = entity_findings("watch", "watch")
    watch.sort(key=lambda f: (f["evidence"]["paid_sales"] + f["evidence"]["unconfirmed_sales"] > 0,
                              -f["evidence"]["spend_cop"]))
    out += watch
    for i, f in enumerate(out, start=1):
        f["priority"] = i
    return out


def _warnings(breakdown: dict, t: dict, ads: list, raw_ads: list, recent_since: str) -> list[dict]:
    out: list[dict] = []
    if breakdown.get("orders_stale"):
        out.append({"code": "orders_stale", "detail": {}})
    if _unconfirmed(t):
        out.append({"code": "pending_confirmation", "detail": {
            "pending_sales": t["pending_sales"], "pending_revenue_cop": t["pending_revenue_cop"],
            "unverified_sales": t["unverified_sales"], "unverified_revenue_cop": t["unverified_revenue_cop"]}})
    if t["cancelled_sales"]:
        out.append({"code": "cancelled_excluded", "detail": {
            "cancelled_sales": t["cancelled_sales"], "cancelled_revenue_cop": t["cancelled_revenue_cop"]}})
    recent_open = t["open_chats"] - t["stale_open_chats"]
    if recent_open:
        out.append({"code": "recent_open", "detail": {"chats": recent_open, "since": recent_since}})
    missing = sum(1 for a in ads if not a["has_daily"] and a["decision"]["action"] != "no_delivery")
    if missing:
        out.append({"code": "no_daily_series", "detail": {"ads": missing}})
    return out


def build():
    """`StateGraph` LangGraph (G1) — single-node que REUSA el `run()` puro (la lógica vive
    UNA vez, G-DET). Durabilidad: el grafo entero como UNA task (L-11)."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    from tools.entity_economics.impl import run as entity_economics

    class State(TypedDict, total=False):
        breakdown: dict     # el drill-down de la campaña que deposita el central (o null)
        scorecard: dict     # ← run()

    def score(state: State) -> dict:
        return run(dict(state), tools={"entity-economics": entity_economics})

    g = StateGraph(State)
    g.add_node("score", score)
    g.add_edge(START, "score")
    g.add_edge("score", END)
    return g.compile(name="ctwa-scorecard")
