"""The real conversation behind a Ciclo Colossus card, masked (2026-11-06).

The workshop (the Taller of Ciclo Colossus) fixes what a card reports, and a
card cites its cases the way Calidad LLM shows them: `···4567 ep_003 t4`.
This reads those cases from the vault so the workshop sees what the customer
said and what the bot did, step by step, instead of guessing from the code.

Personal data never leaves the box: names (the WhatsApp profile, the order
draft and the shipping form), phones, addresses, emails and form fields come out masked with
the same anonymizer that guards what goes to Jev, and the conversation is
shown only by its last 4 digits. What reproduces the case stays: products,
prices, quantities and cities. It reads a few cases per call (`MAX_REFS`),
and the texts only of a named episode, so it never becomes a bulk export.

In production, inside the `sales_eval` worker:

    python -m src.plugins.chats.agent.sales_eval.case 4567:ep_003:t4 [1234:ep_001 …]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.context import redact_terms_from_slots
from src.plugins.chats.agent.sales.decisions.decision_log import read_decisions
from src.plugins.chats.agent.sales_eval.quality_view import episode_slice, production_thread, turn_decisions
from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.turn_view import trace_view, turn_key_of
from src.sdk.connectorkit import anonymize_text

#: Cases per call: enough for a card's examples, never a sweep of the vault.
MAX_REFS = 5
#: Conversations shown when several end in the same 4 digits.
MAX_CANDIDATES = 3
#: A longer text keeps its start (a step can carry a whole prompt).
MAX_TEXT = 2_000
#: Fields that only identify the customer or the message, never the case.
_DROP_KEYS = frozenset({"wamid", "message_id", "session_id", "turn_key", "wa_id", "from", "to", "phone", "bsuid", "user_id"})
_NAME_TOKEN_RE = re.compile(r"[^\W\d_]{3,}")
# Who receives, as the shipping form says it (`receiver_name=…`, `nombre_recibe=…`):
# the conversation may name them again elsewhere, in any case.
_FORM_NAME_RE = re.compile(
    r"\b(?:(?:receiver|recipient|customer|contact|full|first|last)_name|name|\w{0,20}nombre\w{0,20}|\w{0,20}recibe\w{0,20})"
    r"=(?P<value>[^;\n]+)",
    re.IGNORECASE,
)
_EP_RE = re.compile(r"ep_\d+")
_TURN_RE = re.compile(r"t(\d+)")


def _vault_dir() -> Path:
    from src.plugins.chats.agent.sales_eval.evals.composition import get_vault_dir

    return Path(get_vault_dir())


def _parse(ref: str) -> tuple[str, str | None, int | None] | None:
    tokens = [t for t in re.split(r"[\s:]+", ref.replace("·", " ").strip()) if t]
    digits = re.sub(r"\D", "", tokens[0]) if tokens else ""
    if len(digits) < 4:
        return None
    episode = next((t for t in tokens[1:] if _EP_RE.fullmatch(t)), None)
    turn = next((int(m.group(1)) for t in tokens[1:] if (m := _TURN_RE.fullmatch(t))), None)
    return digits[-4:], episode, turn


def _shown_ref(last4: str, episode: str | None, turn: int | None) -> str:
    return " ".join(p for p in (f"···{last4}", episode, f"t{turn}" if turn is not None else None) if p)


def _metadata(vault_dir: Path, session_id: str) -> dict[str, Any]:
    try:
        data = json.loads((vault_dir / session_id / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _sessions(vault_dir: Path, last4: str) -> list[str]:
    try:
        children = sorted(p for p in vault_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    return [p.name for p in children if p.name.endswith(last4) and (p / "metadata.json").exists()]


def _episode_ids(metadata: Mapping[str, Any], traces: Iterable[Mapping[str, Any]]) -> list[str]:
    ids = {str(e.get("episode_id")) for e in metadata.get("episodes") or [] if isinstance(e, dict) and e.get("episode_id")}
    ids |= {str(t.get("episode_id")) for t in traces if t.get("episode_id")}
    return sorted(ids)


def _terms(metadata: Mapping[str, Any]) -> list[str]:
    """This customer's own names and data: the WhatsApp profile and every order draft."""
    terms: set[str] = set()
    profile = metadata.get("profile") if isinstance(metadata.get("profile"), dict) else {}
    name = str(profile.get("name") or "").strip()
    if name:
        terms.add(name)
        terms.update(_NAME_TOKEN_RE.findall(name))
    for episode in metadata.get("episodes") or []:
        draft = episode.get("order_draft") if isinstance(episode, dict) else None
        slots = draft.get("slots") if isinstance(draft, dict) else None
        if isinstance(slots, Mapping):
            terms.update(redact_terms_from_slots(slots))
    return sorted(terms)


def _form_terms(messages: Iterable[Mapping[str, Any]]) -> list[str]:
    """The names this conversation's shipping forms carry, and their words."""
    terms: set[str] = set()
    for message in messages:
        for match in _FORM_NAME_RE.finditer(str(message.get("text") or "")):
            value = match.group("value").strip()
            if value:
                terms.add(value)
                terms.update(_NAME_TOKEN_RE.findall(value))
    return sorted(terms)


def _clean(value: Any, terms: list[str], session_id: str, masked: str) -> Any:
    if isinstance(value, Mapping):
        return {k: _clean(v, terms, session_id, masked) for k, v in value.items() if k not in _DROP_KEYS}
    if isinstance(value, list):
        return [_clean(v, terms, session_id, masked) for v in value]
    if isinstance(value, str):
        text = value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
        return anonymize_text(text.replace(session_id, masked), redact=terms)
    return value


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _who(message: Mapping[str, Any]) -> str | None:
    if message.get("role") == "user":
        return "customer"
    if message.get("role") == "assistant":
        return "team" if message.get("sender") == "human" else "bot"
    return None


def _messages(thread_slice: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in thread_slice.get("messages") or []:
        who = _who(m)
        if who is None:
            continue
        row: dict[str, Any] = {"who": who, "at": m.get("timestamp"), "text": m.get("content") or ""}
        sender = m.get("sender")
        if who == "bot" and sender not in (None, "bot"):
            row["via"] = sender
        if m.get("component_kind"):
            row["card"] = m["component_kind"]
        if m.get("has_image"):
            row["image"] = True
        out.append(row)
    return out


def _turns(session_id: str, traces: list[dict[str, Any]], decisions: list[dict[str, Any]], episode: str, turn: int | None) -> list[dict[str, Any]]:
    out = []
    for trace in traces:
        if trace.get("episode_id") != episode or (turn is not None and trace.get("turn") != turn):
            continue
        out.append({
            "turn": trace.get("turn"),
            "trigger": trace.get("trigger") or "customer",
            "at": _iso(trace.get("turn_started_ms")),
            "steps": trace_view(trace)["steps"],
            "decisions": turn_decisions(traces, decisions, turn_key_of(trace, session_id), session_id=session_id),
        })
    return out


def _conversation(vault_dir: Path, session_id: str, last4: str, episode: str | None, turn: int | None) -> dict[str, Any]:
    metadata = _metadata(vault_dir, session_id)
    traces = turn_traces.read_traces(vault_dir, session_id)
    masked = f"···{last4}"
    if episode is None:
        starts = {e.get("episode_id"): e.get("started_at_ms") for e in metadata.get("episodes") or [] if isinstance(e, dict)}
        episodes = [
            {"episode": ep, "started": _iso(starts.get(ep)), "bot": episode_bot(t for t in traces if t.get("episode_id") == ep)}
            for ep in _episode_ids(metadata, traces)
        ]
        return {"conversation": masked, "episodes": episodes}
    thread_slice = episode_slice(production_thread(vault_dir, session_id), episode) or {"messages": []}
    conv = {
        "conversation": masked,
        "episode": episode,
        "bot": episode_bot(t for t in traces if t.get("episode_id") == episode),
        "messages": _messages(thread_slice),
        "turns": _turns(session_id, traces, read_decisions(vault_dir, session_id), episode, turn),
    }
    return _clean(conv, sorted({*_terms(metadata), *_form_terms(conv["messages"])}), session_id, masked)


def _read_case(vault_dir: Path, ref: str) -> dict[str, Any]:
    parsed = _parse(ref)
    if parsed is None:
        return {"ref": ref, "status": "invalid", "conversations": []}
    last4, episode, turn = parsed
    shown = _shown_ref(last4, episode, turn)
    sessions = _sessions(vault_dir, last4)
    if episode is not None:
        sessions = [
            s for s in sessions
            if episode in _episode_ids(_metadata(vault_dir, s), turn_traces.read_traces(vault_dir, s))
        ]
    if not sessions:
        return {"ref": shown, "status": "not_found", "conversations": []}
    return {
        "ref": shown,
        "status": "ok" if len(sessions) == 1 else "ambiguous",
        "conversations": [_conversation(vault_dir, s, last4, episode, turn) for s in sessions[:MAX_CANDIDATES]],
    }


def read_cases(vault_dir: Path, refs: Iterable[str]) -> dict[str, Any]:
    """The cases a card cites, masked. More than `MAX_REFS` at once is refused."""
    refs = list(refs)
    if len(refs) > MAX_REFS:
        raise ValueError(f"At most {MAX_REFS} cases per call; got {len(refs)}.")
    return {"cases": [_read_case(Path(vault_dir), ref) for ref in refs]}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The conversations behind a card, with personal data masked.")
    parser.add_argument("refs", nargs="+", help="cases like 4567:ep_003:t4 (last 4 digits, episode, turn)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = read_cases(_vault_dir(), args.refs)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
