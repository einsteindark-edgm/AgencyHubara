"""Rescate de costos de WhatsApp de mensajes de TEXTO desde el 9-sep-2026.

Por qué: `send_message_to_session` registraba los textos con `wa_message_id=""`
→ el webhook `message_status` de Meta (que trae el `pricing`) caía a la cola
muerta `_orphan_delivery_statuses.jsonl` como `not_found`.

Cómo: la cola muerta no guardó hora ni sesión, pero el log analítico diario
(`_analytics/<día>.jsonl`) tiene UN evento por status con hora + wamid. Cada
burbuja se empareja POR TIEMPO con el outbound pendiente (texto, wamid vacío)
que salió justo antes. Conservador: si dos sesiones enviaron en la misma
ventana (ambiguo) NO se toca ninguna.

Modo: sin args = ENSAYO EN SECO (no escribe). `--apply` = escribe, con backup
`metadata.json.bak-rescue-20260918` por sesión y bajo el flock del store.
Imprime SOLO agregados (sin teléfonos ni textos).
"""
import json
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.composition import get_current_rate_card
from src.platform.whatsapp.cost import (
    PricingSnapshot,
    compute_message_cost_micros,
)
from src.plugins.chats.agent.sales.use_cases import ingest_delivery_status as ids

APPLY = "--apply" in sys.argv

# Preflight: el código DESPLEGADO tiene que traer todo lo que el apply usa —
# fallar acá, antes de tocar nada, y no a mitad de una escritura.
_missing = [
    name
    for name in (
        "_pricing_snapshot_from_dict", "_summary_from_episode", "_summary_to_dict",
        "_outbound_log_entry_from_dict", "_outbound_log_entry_to_dict",
        "materialize_pending_in_summary",
    )
    if not hasattr(ids, name)
]
if not hasattr(FilesystemMetadataStore, "update"):
    _missing.append("FilesystemMetadataStore.update")
if _missing:
    print(json.dumps({"preflight": "FAILED", "missing": _missing}))
    sys.exit(2)
VAULT = Path("/app/hubara_vault")
SINCE_MS = int(datetime(2026, 9, 9, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)  # 9-sep 00:00 Bogotá
WINDOW_MS = 25_000      # burbujas de un mensaje: 1.5s entre chunks + latencia del webhook
SLACK_BEFORE_MS = 2_000  # relojes: el status puede "llegar" apenas antes del log
BACKUP_SUFFIX = ".bak-rescue-20260918"


def _event_ms(ev: dict):
    for key in ("occurred_at_ms", "timestamp_ms", "ts_ms"):
        if isinstance(ev.get(key), (int, float)):
            return int(ev[key])
    for key in ("occurred_at", "timestamp", "ts", "created_at", "emitted_at"):
        v = ev.get(key)
        if isinstance(v, (int, float)):
            return int(v * 1000) if v < 10_000_000_000 else int(v)
        if isinstance(v, str):
            try:
                return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                pass
    return None


# 1. Cola muerta: wamid → pricing (el primero que lo traiga).
orphan_pricing: dict[str, dict] = {}
for line in (VAULT / "_orphan_delivery_statuses.jsonl").read_text(encoding="utf-8").splitlines():
    try:
        d = json.loads(line)
    except Exception:
        continue
    w = d.get("wa_message_id")
    if w and isinstance(d.get("pricing"), dict) and w not in orphan_pricing:
        orphan_pricing[w] = d["pricing"]

# 2. Log analítico: wamid → primera hora vista (≈ hora de envío).
first_seen: dict[str, int] = {}
shape = Counter()
for f in sorted((VAULT / "_analytics").glob("2026-*.jsonl")):
    if f.stem < "2026-09-08":
        continue
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("kind") != "delivery_status":
            continue
        corr = ev.get("correlation") if isinstance(ev.get("correlation"), dict) else {}
        w = corr.get("wa_message_id")
        if not w or w not in orphan_pricing:
            continue
        if not shape:
            shape.update(list(ev.keys()))
        t = _event_ms(ev)
        if t is None:
            continue
        if w not in first_seen or t < first_seen[w]:
            first_seen[w] = t

bubbles = sorted((t, w) for w, t in first_seen.items() if t >= SINCE_MS)

# 3. Outbounds de texto pendientes (wamid vacío) desde el 9-sep.
pending = []  # (sent_at_ms, session_id, episode_id, ep_idx, entry_idx)
for meta in sorted(VAULT.glob("wa_*/metadata.json")):
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        continue
    for ep_idx, ep in enumerate(data.get("episodes") or []):
        if not isinstance(ep, dict):
            continue
        for e_idx, o in enumerate(ep.get("outbound_messages") or []):
            if (
                isinstance(o, dict)
                and o.get("kind") == "text"
                and not o.get("wa_message_id")
                and o.get("cost_usd_micros") is None
                and isinstance(o.get("sent_at_ms"), int)
                and o["sent_at_ms"] >= SINCE_MS
            ):
                pending.append((o["sent_at_ms"], meta.parent.name, ep.get("episode_id"), ep_idx, e_idx))
pending.sort()

# 4. Emparejar por tiempo. Ventana de cada mensaje: [sent_at - slack, min(sent_at+W, próximo envío)).
#    Ambiguo = la ventana se solapa con la de OTRA sesión → no se toca ninguna de las dos.
windows = []
for i, (sent_at, sid, ep_id, ep_idx, e_idx) in enumerate(pending):
    end = sent_at + WINDOW_MS
    for j in range(i + 1, len(pending)):
        if pending[j][1] == sid:
            end = min(end, pending[j][0] - 1)
            break
    windows.append([sent_at - SLACK_BEFORE_MS, end])
ambiguous = set()
for i in range(len(pending)):
    for j in range(i + 1, len(pending)):
        if windows[j][0] > windows[i][1]:
            break
        if pending[i][1] != pending[j][1]:
            ambiguous.add(i)
            ambiguous.add(j)

assigned: dict[int, list[str]] = defaultdict(list)
bi = 0
unassigned_bubbles = 0
for t, w in bubbles:
    hit = None
    for i, (lo, hi) in enumerate(windows):
        if lo <= t <= hi:
            hit = i
            break
        if lo > t:
            break
    if hit is None or hit in ambiguous:
        unassigned_bubbles += 1
        continue
    assigned[hit].append(w)

rate_card = get_current_rate_card()
cats, ptypes = Counter(), Counter()
total_micros = 0
plan: dict[str, list] = defaultdict(list)  # sid → [(ep_idx, entry_idx, [wamids])]
for i, wamids in assigned.items():
    _sent, sid, _ep_id, ep_idx, e_idx = pending[i]
    plan[sid].append((ep_idx, e_idx, wamids))
    for w in wamids:
        snap = ids._pricing_snapshot_from_dict(orphan_pricing[w])
        if snap is None:
            continue
        cats[snap.category] += 1
        ptypes[snap.pricing_type] += 1
        total_micros += compute_message_cost_micros(snap, rate_card)

report = {
    "mode": "APPLY" if APPLY else "DRY_RUN",
    "analytics_event_keys": sorted(shape),
    "orphans_with_pricing": len(orphan_pricing),
    "orphans_with_time_since_sep9": len(bubbles),
    "pending_text_msgs_since_sep9": len(pending),
    "msgs_matched": len(assigned),
    "msgs_ambiguous_skipped": len(ambiguous),
    "msgs_without_bubbles": len(pending) - len(assigned) - len([i for i in ambiguous if i not in assigned]),
    "bubbles_matched": sum(len(v) for v in assigned.values()),
    "bubbles_unassigned": unassigned_bubbles,
    "sessions_touched": len(plan),
    "by_category": dict(cats),
    "by_pricing_type": dict(ptypes),
    "rescued_total_usd_micros": total_micros,
    "rate_card": rate_card.version,
}

if APPLY:
    store = FilesystemMetadataStore(VAULT)
    written = 0
    for sid, items in plan.items():
        meta_path = VAULT / sid / "metadata.json"
        backup = meta_path.with_name(meta_path.name + BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(meta_path, backup)

        def _mutate(data, _items=items):
            episodes = data.get("episodes") or []
            for ep_idx, e_idx, wamids in _items:
                if ep_idx >= len(episodes):
                    continue
                episode = episodes[ep_idx]
                outs = list(episode.get("outbound_messages") or [])
                if e_idx >= len(outs):
                    continue
                base = ids._outbound_log_entry_from_dict(outs[e_idx])
                if base.wa_message_id or base.cost_usd_micros is not None:
                    continue  # ya lo resolvió otro (idempotente)
                summary = ids._summary_from_episode(episode)
                new_entries = []
                for n, w in enumerate(wamids):
                    snap = ids._pricing_snapshot_from_dict(orphan_pricing[w])
                    if snap is None:
                        continue
                    cost = compute_message_cost_micros(snap, rate_card)
                    entry = replace(
                        base, wa_message_id=w, pricing=snap,
                        cost_usd_micros=cost, rate_card_version=rate_card.version,
                    )
                    if n > 0:
                        # burbuja extra del mismo mensaje: antes no existía como
                        # entrada → entra al summary como pendiente y se materializa.
                        summary = replace(
                            summary,
                            messages_count=summary.messages_count + 1,
                            messages_pending_count=summary.messages_pending_count + 1,
                        )
                    summary = ids.materialize_pending_in_summary(summary, entry)
                    new_entries.append(ids._outbound_log_entry_to_dict(entry))
                if not new_entries:
                    continue
                outs[e_idx:e_idx + 1] = new_entries
                # los índices de este episodio se corrieron: recalcular para los que siguen
                shift = len(new_entries) - 1
                if shift:
                    for k, (o_ep, o_e, o_w) in enumerate(_items):
                        if o_ep == ep_idx and o_e > e_idx:
                            _items[k] = (o_ep, o_e + shift, o_w)
                episode["outbound_messages"] = outs
                episode["cost_summary"] = ids._summary_to_dict(summary)
            return data

        items.sort(key=lambda it: (it[0], it[1]))
        if store.update(sid, _mutate) is not None:
            written += 1
    report["sessions_written"] = written

print(json.dumps(report, ensure_ascii=False))
