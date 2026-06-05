"""Golden-eval runner — maneja al AGENTE DE VENTAS REAL a traves de los escenarios
de `tests/evals/goldens/sales/golden_scenarios.json` y lo califica con doble lente.

Fidelidad (best practice "cerebro real, efectos sandboxeados"):
  * REAL: cerebro (DeepSeek via litellm), workspace/*.md (build_prompt), tool-loop
    de produccion (execute_tool: search_products lee el snapshot del catalogo,
    el LLM elige y ejecuta tools de verdad), y el JUEZ (el mismo build_judge() de SigNoz).
  * NEUTRALIZADO (sin tocar la realidad): NO se setean MEDUSA_REGION_ID /
    MEDUSA_SALES_CHANNEL_ID -> get_order_registration_port() devuelve
    StubOrderRegistration (no crea orden real). NO se corre
    flush_pending_ui_intents_activity -> los intents de UI/WhatsApp quedan en
    metadata, no se envia nada. El mensaje final lo calificamos del texto devuelto
    (manejamos run_agent_turn's loop, no el workflow que enviaria).

Doble lente de calificacion sobre la MISMA corrida real:
  1. Ledger conductual determinista (expected_behaviors vs tools usadas + texto).
  2. Juez LLM (judge_metrics) — script_adherence, no_hallucination, handoff, etc.

Uso (LOCAL, contra el litellm vivo del stack, SIN secrets):
  docker cp local-hubara-worker-chats-sales:/app/hubara_vault/catalog/snapshot.json \\
      /tmp/golden_eval_vault/catalog/snapshot.json
  cd hubara_agency && uv run --extra evals python scripts/golden_eval.py --limit 1
  flags: --scenario <id> | --limit N | --no-judge | --category <cat>
"""
from __future__ import annotations

# ruff: noqa: E702  — estilo compacto intencional en los checks de comportamiento

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# --- env ANTES de cualquier import del codigo de produccion -----------------
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_VAULT = os.environ.get("GOLDEN_EVAL_VAULT", "/tmp/golden_eval_vault")
os.environ.setdefault("WORKSPACE_VAULT_DIR", _VAULT)
os.environ.setdefault("CATALOG_SNAPSHOT_DIR", str(Path(_VAULT) / "catalog"))
os.environ.setdefault("API_BASE_LLMLITE", "http://localhost:4000")
os.environ.setdefault("LITELLM_API_KEY", "sk-litellm-proxy-local")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
# Juez del eval: Gemini Pro 3.1 — frontier INDEPENDIENTE del agente (DeepSeek).
# El flash-lite daba falsos negativos; usar el modelo del agente como juez
# (deepseek) tiene sesgo de auto-evaluacion. Override con EVAL_JUDGE_MODEL.
os.environ.setdefault("EVAL_JUDGE_MODEL", "litellm_proxy/gemini-pro-judge")
# Medusa dummy -> el modulo importa, pero el order port queda STUB (no creamos
# ordenes reales) porque NO seteamos REGION_ID / SALES_CHANNEL_ID, y forzamos su
# ausencia por si vinieran del entorno.
os.environ.setdefault("MEDUSA_BASE_URL", "http://localhost:1")
os.environ.setdefault("MEDUSA_ADMIN_TOKEN", "dummy-eval-token")
for _k in ("MEDUSA_REGION_ID", "MEDUSA_SALES_CHANNEL_ID"):
    os.environ.pop(_k, None)

Path(_VAULT, "catalog").mkdir(parents=True, exist_ok=True)

# Snapshot del catalogo como fixture COMMITTEADO -> el runner es self-contained
# (local + CI), no depende del vault vivo. Se copia al vault temp si no esta ya.
_SNAP_FIXTURE = Path(__file__).resolve().parents[1] / "tests/evals/goldens/sales/catalog_snapshot.json"
_SNAP_DST = Path(_VAULT, "catalog", "snapshot.json")
if _SNAP_FIXTURE.exists() and not _SNAP_DST.exists():
    _SNAP_DST.write_text(_SNAP_FIXTURE.read_text("utf-8"), encoding="utf-8")

_SCENARIOS = _ROOT / "tests/evals/goldens/sales/golden_scenarios.json"

# Metricas "full-funnel": necesitan ver el embudo completo. En escenarios cortos
# (apertura de 1 turno, handoff suelto) penalizan injustamente -> solo se corren
# si el escenario tiene >=3 turnos del cliente. (Calibracion baseline run 1.)
_FULL_FUNNEL = {"script_adherence", "conversion_progress"}


# --- marker mapping: convierte los markers del golden a texto del cliente -----
def marker_to_text(turn: str) -> str:
    t = turn.strip()
    if t.startswith("[BTN:"):
        return t[len("[BTN:"):].rstrip("]").strip()
    if t.startswith("[FLOW:"):
        return "Datos: " + t[len("[FLOW:"):].rstrip("]").strip()
    if t.startswith("[CART:"):
        return "Arma el carrito con " + t[len("[CART:"):].rstrip("]").strip()
    if t.startswith("[IMG]"):
        return "(el cliente envia una imagen)"
    return t


class _StubCheckoutVerification:
    """Verifier de eval: siempre verified=true, catalogo disponible, sin discrepancia.

    FIDELIDAD: en prod `verify_order_for_checkout` pega a Medusa LIVE y en el happy
    path devuelve verified=true (snapshot y live coinciden). En el eval Medusa es un
    dummy (MEDUSA_BASE_URL=http://localhost:1) -> verify_items falla -> el tool emite
    `catalog_unavailable` -> el agente ESCALA (CHECKOUT_VERIFY_FAILED) en vez de
    registrar la orden, rompiendo TODO escenario de cierre (register_order nunca se
    llama, CONFIRMADO_PAGO_PENDIENTE nunca se marca). Igual que StubOrderRegistration
    (que neutraliza register_order via ausencia de REGION_ID/SALES_CHANNEL_ID),
    stubbeamos el verifier para que el cierre pueda completarse. NO toca produccion."""

    async def verify_items(self, items):  # noqa: ANN001, ANN201
        from src.platform.catalog.checkout_port import (
            CheckoutVerification,
            VerifiedItem,
        )
        verified = [
            VerifiedItem(
                handle=it.handle, title=it.handle, snapshot_price=None,
                live_price=None, currency="COP", in_stock=True, discrepancy=False,
            )
            for it in items
        ]
        return CheckoutVerification(verified=True, catalog_available=True, items=verified)


def _install_eval_stubs() -> None:
    """Neutraliza la I/O live de Medusa en el path de cierre (verify + register).

    El worker `sales.py` setea `_checkout_verifier` y `_order_registration_port` a
    nivel modulo, y los lambdas de `register_tool_extension` los resuelven como
    globals en cada build de la tool — reasignarlos aca (DESPUES de importar el
    worker) hace que el eval use los stubs.

    Por que NO basta con popear MEDUSA_REGION_ID/SALES_CHANNEL_ID (como asumia el
    runner): `get_order_registration_port()` lee `get_medusa_settings()`, que carga
    el `.env` del repo (tiene region_id/sales_channel_id reales) DESPUES del pop ->
    selecciona MedusaOrderRegistration -> register_order pega al Medusa dummy
    (MEDUSA_BASE_URL=http://localhost:1) y devuelve success=False -> el agente cae al
    path ORDER_REGISTRATION_FAILED y NUNCA marca CONFIRMADO_PAGO_PENDIENTE. Forzar el
    StubOrderRegistration (success=True, provider='stub', sin red) es robusto."""
    import src.plugins.chats.workers.sales as _sw
    from src.platform.orders.stub import StubOrderRegistration
    _sw._checkout_verifier = _StubCheckoutVerification()
    _sw._order_registration_port = StubOrderRegistration()


def _read_projectable_draft(sid: str):
    """Lee el order_draft acumulado en metadata (lo que set_order_slot fue guardando).
    FIDELIDAD: en prod, ingest_inbound_message hace esto cada turno y re-inyecta el
    note al plugin_context para que el agente NO re-pregunte. Sin esto el eval daba
    knowledge_retention falso-bajo (el agente grababa el draft pero nunca lo veia)."""
    from src.plugins.chats.agent.sales.use_cases.order_draft import get_projectable_draft
    p = Path(_VAULT, sid, "metadata.json")
    if not p.exists():
        return None
    try:
        return get_projectable_draft(json.loads(p.read_text("utf-8")))
    except Exception:  # noqa: BLE001
        return None


async def drive_scenario(scn: dict) -> dict:
    """Maneja al agente real por los turnos del escenario. Devuelve turns (para el
    juez) + ledger (tools usadas) + responses + error."""
    from temporalio.testing import ActivityEnvironment

    from exoclaw_temporal.activities.conversation import BuildPromptInput, build_prompt
    from exoclaw_temporal.activities.llm import LLMChatInput, llm_chat
    from src.platform.temporal.activities import ExecuteToolInput, execute_tool

    from src.plugins.chats.agent.sales.activities.bootstrap_session import (
        bootstrap_sales_session_activity,
    )
    from src.plugins.chats.agent.sales.context import build_bogota_context_string
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput

    env = ActivityEnvironment()
    sid = "wa_golden_" + scn["id"][:28]
    session = await env.run(
        bootstrap_sales_session_activity,
        SalesSessionInput(session_id=sid, runtime_workspace_path=None),
    )
    bogota = build_bogota_context_string()

    turns: list[dict] = []
    ledger: list[dict] = []   # [{turn, name, args}]
    responses: list[str] = []
    messages: list[dict] | None = None

    cust_turns = [marker_to_text(t) for t in scn["customer_turns"]]
    for i, cust in enumerate(cust_turns):
        turns.append({"role": "user", "content": cust, "tools": []})
        if messages is None:
            messages = await env.run(
                build_prompt,
                BuildPromptInput(
                    session_id=sid, message=cust, channel="whatsapp", chat_id=sid,
                    llm=session.llm, workspace=session.workspace, media=None,
                    plugin_context=[bogota],
                ),
            )
        else:
            # FIDELIDAD (prod ingest_inbound_message): reconstruir el SYSTEM PROMPT
            # con el order_draft note (donde prod lo pone, via build_prompt+plugin_context),
            # preservando el history en memoria. Sin esto el agente re-preguntaba datos
            # que ya habia grabado con set_order_slot (knowledge_retention falso-bajo).
            draft = _read_projectable_draft(sid)
            pctx = [bogota]
            if draft:
                from src.plugins.chats.agent.sales.use_cases.order_draft import (
                    build_order_draft_note,
                )
                pctx.append(build_order_draft_note(draft))
            fresh = await env.run(
                build_prompt,
                BuildPromptInput(
                    session_id=sid, message=cust, channel="whatsapp", chat_id=sid,
                    llm=session.llm, workspace=session.workspace, media=None,
                    plugin_context=pctx,
                ),
            )
            messages[0] = fresh[0]  # system prompt actualizado (con el draft note)
            messages.append({"role": "user", "content": cust})

        turn_tools: list[str] = []
        turn_outputs: list[dict] = []   # resultado de cada tool (para que el juez vea el grounding)
        pre_tool: list[str] = []   # texto emitido JUNTO con tool calls (prod: pre_tool_messages)
        final = ""
        it = 0
        while it < session.llm.max_iterations:
            it += 1
            resp = await env.run(
                llm_chat,
                LLMChatInput(
                    messages=messages, llm=session.llm,
                    tool_definitions_json=session.tool_definitions_json, baggage=None,
                ),
            )
            if resp.has_tool_calls:
                # Fidelidad (bug llm_content_with_toolcalls_dropped): el LLM emite
                # content JUNTO con la tool call (ej. el saludo + send_quick_replies).
                # Prod lo captura en pre_tool_messages; el texto visible del turno =
                # pre_tool_messages + final_content. Sin esto perdiamos el saludo.
                if resp.content and resp.content.strip():
                    pre_tool.append(resp.content.strip())
                messages.append(resp.to_assistant_message())
                for tc in resp.tool_calls:
                    turn_tools.append(tc.name)
                    ledger.append({"turn": i, "name": tc.name, "args": tc.arguments})
                    try:
                        result = await env.run(
                            execute_tool,
                            ExecuteToolInput(
                                name=tc.name, params=tc.arguments, session_id=sid,
                                channel="whatsapp", chat_id=sid, workspace=session.workspace,
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 — tool falla (ej. verify live) -> registramos
                        result = json.dumps({"tool_error": str(exc)[:200]})
                    messages.append({
                        "role": "tool", "tool_call_id": getattr(tc, "id", tc.name),
                        "name": tc.name, "content": result,
                    })
                    # FIDELIDAD juez: el no_hallucination GEval solo ve CONTENT/ROLE/
                    # TOOLS_CALLED; sin el OUTPUT de search_products, un precio correcto
                    # (grounded) le parece inventado. Adjuntamos el resultado (capado)
                    # al ToolCall para que pueda verificar el grounding.
                    turn_outputs.append({"name": tc.name, "output": result[:1000]})
                continue
            final = (resp.content or "").strip()
            break

        visible = "\n".join([*pre_tool, final] if final else pre_tool).strip()
        responses.append(visible or "(turno solo-tool / sin texto)")
        turns.append({"role": "assistant", "content": visible, "tools": list(turn_tools),
                      "tool_outputs": turn_outputs})

    # pending_ui_intents capturados en metadata (no se enviaron)
    intents = []
    try:
        meta = json.loads(Path(_VAULT, sid, "metadata.json").read_text("utf-8"))
        intents = meta.get("pending_ui_intents") or []
    except Exception:  # noqa: BLE001
        pass

    return {"turns": turns, "ledger": ledger, "responses": responses, "intents": intents}


# --- lente 1: ledger conductual determinista --------------------------------
def check_behaviors(scn: dict, res: dict) -> list[dict]:
    from src.plugins.chats.agent.sales_eval.evals import script_rubric as R

    asst = [t for t in res["turns"] if t["role"] == "assistant"]
    first = asst[0]["content"] if asst else ""
    all_text = "\n".join(t["content"] for t in asst)
    tool_names = [e["name"] for e in res["ledger"]]

    def tag_called(tag: str) -> bool:
        for e in res["ledger"]:
            if "manage_conversation_tag" in e["name"]:
                a = e["args"] if isinstance(e["args"], dict) else {}
                if tag in json.dumps(a, ensure_ascii=False):
                    return True
        return False

    out = []
    for b in scn["expected_behaviors"]:
        bt = b["type"]; ok = None; detail = ""
        if bt == "greeting_first_turn":
            ok = bool(R.GREETING_RE.search(first) and R.BRAND_RE.search(first))
            detail = first[:80]
        elif bt == "forbidden_opener_absent":
            ok = not any(p.search(first) for p in R.FORBIDDEN_OPENERS)
        elif bt == "no_voseo":
            hits = [tok for tok, rr in zip(R.VOSEO_TOKENS, R.VOSEO_RES) if rr.search(all_text)]
            ok = not hits; detail = ",".join(hits)
        elif bt == "no_em_dash":
            ok = not R.DASH_RE.search(all_text)
        elif bt == "emoji_allowlist":
            bad = R.disallowed_emojis(all_text)
            ok = not bad; detail = "".join(bad)
        elif bt == "no_forbidden_closing":
            hit = [p.pattern for p in R.FORBIDDEN_CLOSINGS if p.search(all_text)]
            ok = not hit; detail = ";".join(hit)[:60]
        elif bt == "tool_called":
            ok = any(b["tool"] in n for n in tool_names); detail = b["tool"]
        elif bt == "tool_not_called":
            ok = not any(b["tool"] in n for n in tool_names); detail = b["tool"]
        elif bt == "escalate":
            ok = any("escalate_to_human" in n for n in tool_names); detail = b.get("trigger", "")
        elif bt == "no_escalation":
            ok = not any("escalate_to_human" in n for n in tool_names)
        elif bt == "register_order_attempted":
            ok = any("register_order" in n for n in tool_names)
        elif bt == "tag_set":
            ok = tag_called(b["tag"]); detail = b["tag"]
        elif bt == "tag_not_set":
            ok = not tag_called(b["tag"]); detail = b["tag"]
        else:
            detail = "(juez)"  # search_before_naming / no_hallucinated_product / no_reask / no_ai_reveal / single_closing_message
        out.append({"behavior": bt, "ok": ok, "detail": detail})
    return out


# --- lente 2: juez LLM -------------------------------------------------------
async def grade_judge(scn: dict, res: dict) -> dict:
    from src.plugins.chats.agent.sales_eval.evals import composition, metrics, reconstruct

    # Fix B (calibracion): filtrar turnos sin texto (solo-tool). Un turno con
    # content="" rompia KnowledgeRetentionMetric ("2 validation errors for Knowledge").
    graded = [t for t in res["turns"] if (t.get("content") or "").strip()]
    tc = reconstruct.build_conversational_test_case(graded, name=scn["id"])
    judge = composition.get_judge()
    by_key = {metrics.metric_key(m): m for m in metrics.all_sales_metrics(judge)}
    # Fix A (calibracion): full-funnel metrics solo si hay embudo (>=3 turnos cliente).
    ncust = len(scn.get("customer_turns", []))
    want = [k for k in scn.get("judge_metrics", [])
            if not (k in _FULL_FUNNEL and ncust < 3)]
    out = {}
    for k in want:
        m = by_key.get(k)
        if m is None:
            continue
        try:
            await m.a_measure(tc)
            out[k] = {"score": round(float(m.score or 0), 2), "ok": bool(m.is_successful()),
                      "reason": (m.reason or "")[:300]}
        except Exception as exc:  # noqa: BLE001
            out[k] = {"score": None, "ok": None, "reason": f"ERROR: {str(exc)[:100]}"}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario"); ap.add_argument("--category")
    ap.add_argument("--limit", type=int); ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--dump", action="store_true",
                    help="imprime la transcripcion completa (cliente + agente + tools) para diagnostico")
    ap.add_argument("--repeat", type=int, default=1,
                    help="corre cada escenario N veces (trending: agente+juez son no-deterministas)")
    args = ap.parse_args()

    data = json.loads(_SCENARIOS.read_text("utf-8"))
    scns = data["scenarios"]
    if args.scenario:
        wanted = {x.strip() for x in args.scenario.split(",") if x.strip()}
        scns = [s for s in scns if s["id"] in wanted]
    if args.category:
        scns = [s for s in scns if s["category"] == args.category]
    if args.limit:
        scns = scns[: args.limit]

    print(f"=== golden-eval · {len(scns)} escenario(s) · agente REAL + "
          f"{'juez REAL' if not args.no_judge else 'solo ledger'} ===\n")

    # registrar las tools de dominio (las register_tool_extension de sales.py)
    import src.plugins.chats.workers.sales  # noqa: F401

    # neutralizar la I/O live de cierre (Medusa verify) -> el cierre puede completarse
    _install_eval_stubs()

    summary = []
    for idx, scn in enumerate(scns, 1):
        runs = []   # una entrada por corrida: {"behaviors": "x/y", "judge": {k: score}} | {"error": ...}
        for rep in range(1, args.repeat + 1):
            head = f"[{idx}/{len(scns)}] {scn['id']}  ({scn['category']})"
            head += f"  · run {rep}/{args.repeat}" if args.repeat > 1 else ""
            print(head)
            try:
                res = await drive_scenario(scn)
            except Exception as exc:  # noqa: BLE001
                print(f"   ERROR manejando el agente: {str(exc)[:200]}\n")
                runs.append({"error": str(exc)[:120]})
                continue

            if args.dump:
                for t in res["turns"]:
                    if t["role"] == "user":
                        print(f"   👤 {t['content'][:300]}")
                    else:
                        tg = f"  «{','.join(t['tools'])}»" if t["tools"] else ""
                        print(f"   🤖{tg}\n      {(t['content'] or '(sin texto)')[:600]}")

            behs = check_behaviors(scn, res)
            bpass = sum(1 for b in behs if b["ok"] is True)
            bcheck = sum(1 for b in behs if b["ok"] is not None)
            print(f"   turnos: {len(scn['customer_turns'])} · tools usadas: "
                  f"{','.join(sorted({e['name'].split('.')[-1] for e in res['ledger']})) or '(ninguna)'}")
            for b in behs:
                mark = "✅" if b["ok"] is True else ("❌" if b["ok"] is False else "·")
                print(f"     {mark} {b['behavior']:26s} {b['detail'][:50]}")

            judged = {}
            if not args.no_judge:
                judged = await grade_judge(scn, res)
                for k, v in judged.items():
                    mark = "✅" if v["ok"] else ("❌" if v["ok"] is False else "·")
                    sc = "n/a" if v["score"] is None else f"{v['score']:.2f}"
                    print(f"     {mark} judge:{k:20s} {sc}  {v['reason'][:160]}")
            print()
            runs.append({"behaviors": f"{bpass}/{bcheck}",
                         "judge": {k: v["score"] for k, v in judged.items()}})
        summary.append({"id": scn["id"], "runs": runs})

    print("=== RESUMEN ===")
    for s in summary:
        runs = s["runs"]
        oks = [r for r in runs if "judge" in r]
        errs = [r for r in runs if "error" in r]
        if not oks:
            print(f"  ERROR {s['id']:34s} {errs[0]['error'] if errs else '??'}")
            continue
        keys = []
        for r in oks:
            for k in r["judge"]:
                if k not in keys:
                    keys.append(k)
        parts = []
        for k in keys:
            vals = [r["judge"][k] for r in oks if r["judge"].get(k) is not None]
            if not vals:
                parts.append(f"{k}=n/a")
            elif len(vals) == 1:
                parts.append(f"{k}={vals[0]}")
            else:  # trending: media[min-max] sobre las corridas
                parts.append(f"{k}={round(sum(vals) / len(vals), 2)}[{min(vals)}-{max(vals)}]")
        nerr = f"  ({len(errs)} err)" if errs else ""
        print(f"  {s['id']:34s} beh {oks[-1]['behaviors']:7s} {'  '.join(parts)}{nerr}")


if __name__ == "__main__":
    asyncio.run(main())
