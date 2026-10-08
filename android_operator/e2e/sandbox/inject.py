#!/usr/bin/env python3
"""Runtime mutations of the sandbox while the API runs (stdlib only).

    python3 inject.py fire                         # NEW customer "Mateo Prueba" (wa_000000000107) asking for a
                                                   #   human, 2 unanswered msgs, first one 11 min ago → GRAVE fire
    python3 inject.py fire --name "Lucía Prueba" --session wa_000000000108 --waiting-min 15
    python3 inject.py fire --flip wa_000000000101  # flip an EXISTING chat: human route + EXPLICIT_REQUEST +
                                                   #   a backdated unanswered msg → GRAVE fire
    python3 inject.py reply wa_000000000101 "Quiero el de lavanda y el de coco"
    python3 inject.py reset [--image-base URL]     # rebuild the whole seed (timestamps re-based to now)
    python3 inject.py screen mas.json --from ../screens/mas.json
                                                   # serve this App Operador screen instead of the repo's one
                                                   #   (what frontend-deploy would publish); reset removes it

Everything goes through files, exactly where the real ingest writes: the
session JSONL + metadata.json (atomic write, same ``metadata.json.lock`` flock
as ``FilesystemMetadataStore.update``). The running API picks it up by itself:
REST reads the vault on every request, and the SSE sampler (2.5 s) publishes
``chats.sessions_snapshot`` + ``chats.session_updated``. A reset also rewrites
the fake Medusa store; the launcher's watcher turns that into ``orders.changed``.

Note: no Temporal workers run in the sandbox — nobody answers a customer reply
automatically, and the ingest-derived signals (purchase "sí" / deferral) are
not computed for injected replies.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sandbox_common import (  # noqa: E402
    DAY_MS,
    DEFAULT_IMAGE_BASE,
    MIN_MS,
    MOBILE_CONFIG,
    MOBILE_SCREENS,
    PHONE_NUMBER_ID,
    PUSH_CURSOR,
    PUSH_OUTBOX,
    SANDBOX_DIR,
    VAULT_DIR,
    append_jsonl,
    atomic_write_json,
    iso_utc,
    locked_update,
    now_ms,
    session_paths,
)

_SESSION_RE = re.compile(r"wa_[0-9]{8,15}")


def _wamid() -> str:
    return f"wamid.SBX{uuid.uuid4().hex[:20].upper()}"


def _require_session(session_id: str) -> Path:
    if not _SESSION_RE.fullmatch(session_id):
        sys.exit(f"invalid session id {session_id!r} (expected wa_ + 8-15 digits)")
    meta, _jsonl, _media = session_paths(session_id)
    if not meta.exists():
        sys.exit(f"session {session_id} does not exist in {VAULT_DIR}")
    return meta


def _status(tag: str, motivo: str, route: str, at: int, **extra: Any) -> dict[str, Any]:
    return {"tag": tag, "motivo": motivo, "active_route": route, "timestamp": at / 1000.0, **extra}


# ── fire ─────────────────────────────────────────────────────────────────────


def fire_new(session_id: str, name: str, waiting_min: int, *, just_handed_off: bool = False) -> None:
    if not _SESSION_RE.fullmatch(session_id):
        sys.exit(f"invalid session id {session_id!r}")
    t = now_ms()
    first = name.split()[0]
    first_unanswered = t - waiting_min * MIN_MS
    last_inbound = t - 1 * MIN_MS
    last_wamid = _wamid()
    events = [
        {"role": "user", "content": "Hola, buenas. Compré una vela la semana pasada y me llegó partida 😞",
         "timestamp": iso_utc(first_unanswered - 3 * MIN_MS), "wamid": _wamid()},
        {"role": "assistant", "content": f"¡Hola {first}! Qué pena contigo 🙏 Te comunico con una asesora del "
                                         "equipo para ayudarte con eso.",
         "timestamp": iso_utc(first_unanswered - 3 * MIN_MS + 30_000), "tools_used": ["escalate_to_human"]},
        {"role": "user", "content": "¿Hay alguien? Necesito hablar con una persona, por favor",
         "timestamp": iso_utc(first_unanswered), "wamid": _wamid()},
        {"role": "user", "content": "Por favor respóndanme, es un regalo para mañana 🙏",
         "timestamp": iso_utc(last_inbound), "wamid": last_wamid},
    ]
    if just_handed_off:
        # El bot acaba de pasar a humano y el cliente NO volvió a escribir (prueba del operador, 2026-10-07): el
        # traspaso del bot no es respuesta; es un incendio grave al instante.
        first_unanswered = last_inbound = t - 30_000
        events = [
            {"role": "user", "content": "Necesito hablar con alguien urgente por favor",
             "timestamp": iso_utc(last_inbound), "wamid": last_wamid},
            {"role": "assistant", "content": f"¡Claro, {first}! Te comunico con una persona del equipo 🙏",
             "timestamp": iso_utc(last_inbound + 5_000), "tools_used": ["escalate_to_human"]},
        ]
    motivo = f"{first} pidió hablar con una persona: su vela llegó partida"
    metadata = {
        "phone_number_id": PHONE_NUMBER_ID,
        "profile": {"name": name},
        "active_route": "humano",
        "tag": "HUMANO",
        "motivo": motivo,
        "escalation_reason": "EXPLICIT_REQUEST",
        "last_inbound_at_ms": last_inbound,
        "last_inbound_message_id": last_wamid,
        "service_window_expires_at_ms": last_inbound + DAY_MS,
        "status_history": [_status("HUMANO", motivo, "humano", first_unanswered - 3 * MIN_MS + 30_000,
                                   reason_category="EXPLICIT_REQUEST")],
        "episodes": [{"episode_id": "ep_001", "started_at_ms": first_unanswered - 3 * MIN_MS,
                      "closed_at_ms": None, "msgs_count_at_start": 0}],
    }
    # Build it under a name the sampler ignores (not "wa_*"), then rename
    # atomically: the API never sees a half-written session.
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    staging = VAULT_DIR / f".staging-{session_id}-{uuid.uuid4().hex[:6]}"
    (staging / "sessions").mkdir(parents=True)
    for ev in events:
        append_jsonl(staging / "sessions" / f"{session_id}.jsonl", ev)
    atomic_write_json(staging / "metadata.json", metadata)
    target = VAULT_DIR / session_id
    if target.exists():
        shutil.rmtree(target)
    os.rename(staging, target)
    print(f"fire: new customer {name} ({session_id}) asked for a human; "
          f"2 unanswered msgs, the first {waiting_min} min ago → expect a GRAVE 'wants_human' fire")


def fire_flip(session_id: str, waiting_min: int) -> None:
    meta_path = _require_session(session_id)
    _meta, jsonl, _media = session_paths(session_id)
    t = now_ms()
    backdated = t - waiting_min * MIN_MS
    wamid = _wamid()
    # Backdated on purpose (the fire is GRAVE only after 10 min without reply).
    # `unanswered_since` dates the wait by the FIRST unanswered message in file
    # order, so the bot's hand-off line goes first: it closes any earlier
    # unanswered block and the backdated ask becomes the start of the wait.
    append_jsonl(jsonl, {"role": "assistant", "content": "Te comunico con una asesora del equipo, en un momento "
                                                         "te escribe 🙌",
                         "timestamp": iso_utc(backdated - 30_000), "tools_used": ["escalate_to_human"]})
    append_jsonl(jsonl, {"role": "user", "content": "¿Me pueden comunicar con una persona, por favor?",
                         "timestamp": iso_utc(backdated), "wamid": _wamid()})
    append_jsonl(jsonl, {"role": "user", "content": "¿Hola? Sigo esperando 🙏",
                         "timestamp": iso_utc(t), "wamid": wamid})
    motivo = "El cliente pidió hablar con una persona (inyectado por inject.py)"

    def mutate(data: Any) -> Any:
        if not isinstance(data, dict) or not data:
            return None
        data.update({"active_route": "humano", "tag": "HUMANO", "motivo": motivo,
                     "escalation_reason": "EXPLICIT_REQUEST", "last_inbound_at_ms": t,
                     "last_inbound_message_id": wamid, "service_window_expires_at_ms": t + DAY_MS})
        data.setdefault("status_history", []).append(
            _status("HUMANO", motivo, "humano", t, reason_category="EXPLICIT_REQUEST", source="inject.py"))
        return data

    locked_update(meta_path, mutate, default={})
    print(f"fire: {session_id} flipped to human route (EXPLICIT_REQUEST), unanswered since "
          f"{waiting_min} min ago → expect a GRAVE fire")


# ── reply ────────────────────────────────────────────────────────────────────


def reply(session_id: str, text: str) -> None:
    meta_path = _require_session(session_id)
    _meta, jsonl, _media = session_paths(session_id)
    t = now_ms()
    wamid = _wamid()
    append_jsonl(jsonl, {"role": "user", "content": text, "timestamp": iso_utc(t), "wamid": wamid})

    def mutate(data: Any) -> Any:
        if not isinstance(data, dict) or not data:
            return None
        # What IngestInboundMessage stamps on every inbound (window reopens 24 h).
        data.update({"last_inbound_at_ms": t, "last_inbound_message_id": wamid,
                     "service_window_expires_at_ms": t + DAY_MS})
        return data

    locked_update(meta_path, mutate, default={})
    print(f"reply: {session_id} ← {text!r} (window open until +24 h)")


# ── reset ────────────────────────────────────────────────────────────────────


def mobile_config(min_version: int) -> None:
    MOBILE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    MOBILE_CONFIG.write_text(json.dumps({"min_version_code": min_version}), encoding="utf-8")
    print(f"config: min_version_code={min_version}")


def mobile_screen(name: str, source: str) -> None:
    if not re.fullmatch(r"[a-z0-9_]{1,64}\.json", name):
        sys.exit(f"invalid screen file {name!r} (expected <id>.json)")
    src = Path(source)
    if not src.is_absolute():
        src = (Path(__file__).resolve().parent / source).resolve()
    json.loads(src.read_text(encoding="utf-8"))  # que sea JSON (la app igual lo valida)
    MOBILE_SCREENS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, MOBILE_SCREENS / name)
    print(f"screen: {name} ← {src}")


def push_device(token: str) -> None:
    """Registra un teléfono para avisos, como lo haría la app con su token de Firebase (`POST /mobile/devices`)."""
    t = now_ms()
    registry = VAULT_DIR / "_mobile" / "devices.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(registry, {"operators": {"e2e@emulador.test": [{
        "token": token, "platform": "android", "app_version": "e2e", "registered_at_ms": t, "updated_at_ms": t,
    }]}})
    print(f"push-device: {token} registrado para avisos")


def push_relay() -> None:
    """Los pushes que el backend mandó desde la última vez (una línea JSON cada uno)."""
    lines = PUSH_OUTBOX.read_text(encoding="utf-8").splitlines() if PUSH_OUTBOX.exists() else []
    start = int(PUSH_CURSOR.read_text(encoding="utf-8") or 0) if PUSH_CURSOR.exists() else 0
    PUSH_CURSOR.write_text(str(len(lines)), encoding="utf-8")
    for line in lines[start:]:
        print(line)


def reset(image_base: str) -> None:
    import seed

    MOBILE_CONFIG.unlink(missing_ok=True)  # la configuración de la app vuelve a la de siempre
    PUSH_OUTBOX.unlink(missing_ok=True)    # ni pushes viejos por reenviar
    PUSH_CURSOR.unlink(missing_ok=True)
    shutil.rmtree(MOBILE_SCREENS, ignore_errors=True)  # y las pantallas, las del repo

    info = seed.build(image_base=image_base)
    for name in ("sent.log", "temporal.log", "medusa.log"):
        path = SANDBOX_DIR / name
        if path.exists():
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f'{{"ts": "{info["seeded_at"]}", "event": "sandbox reset (seed rebuilt)"}}\n')
    print(f"reset: seed rebuilt at {info['seeded_at']} ({len(info['sessions'])} sessions, "
          f"{len(info['orders'])} orders)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fire = sub.add_parser("fire", help="make a new GRAVE fire appear")
    p_fire.add_argument("--name", default="Mateo Prueba")
    p_fire.add_argument("--session", default="wa_000000000107")
    p_fire.add_argument("--waiting-min", type=int, default=11)
    p_fire.add_argument("--flip", metavar="SESSION", help="flip an existing session instead of creating one")
    p_fire.add_argument("--just-handed-off", action="store_true",
                        help="the bot just handed off and the customer did not write again")
    p_reply = sub.add_parser("reply", help="append a customer message")
    p_reply.add_argument("session")
    p_reply.add_argument("text")
    p_config = sub.add_parser("config", help="change the App Operador server config (mobile/config.json)")
    p_config.add_argument("--min-version", type=int, required=True)
    p_screen = sub.add_parser("screen", help="serve an App Operador screen instead of the repo's one")
    p_screen.add_argument("name", help="<id>.json")
    p_screen.add_argument("--from", dest="source", required=True, help="JSON file (relative to sandbox/)")
    p_device = sub.add_parser("push-device", help="register a phone token for push notices")
    p_device.add_argument("token")
    sub.add_parser("push-relay", help="print the pushes the backend sent since the last call (JSON lines)")
    p_reset = sub.add_parser("reset", help="restore the seed")
    p_reset.add_argument("--image-base", default=DEFAULT_IMAGE_BASE)
    args = parser.parse_args()

    if args.cmd == "fire":
        if args.flip:
            fire_flip(args.flip, args.waiting_min)
        else:
            fire_new(args.session, args.name, args.waiting_min, just_handed_off=args.just_handed_off)
    elif args.cmd == "reply":
        reply(args.session, args.text)
    elif args.cmd == "config":
        mobile_config(args.min_version)
    elif args.cmd == "screen":
        mobile_screen(args.name, args.source)
    elif args.cmd == "push-device":
        push_device(args.token)
    elif args.cmd == "push-relay":
        push_relay()
    else:
        reset(args.image_base)


if __name__ == "__main__":
    main()
