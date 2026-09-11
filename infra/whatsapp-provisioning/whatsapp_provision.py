#!/usr/bin/env python3
"""whatsapp_provision — CLI declarativo (estilo Terraform) para provisionar un
número de WhatsApp Cloud API + su catálogo Meta, para Hubara o cualquier tenant.

Filosofía: una config declarativa por tenant (`tenants/<tenant>.env`) describe el
ESTADO DESEADO; `plan` muestra el diff contra el estado real (vía Graph API) y
`apply` converge de forma IDEMPOTENTE. Re-correrlo es seguro.

Solo stdlib (urllib) — corre con cualquier `python3`, sin dependencias.

  python3 whatsapp_provision.py discover --config tenants/hubara.env
  python3 whatsapp_provision.py plan     --config tenants/hubara.env
  python3 whatsapp_provision.py apply    --config tenants/hubara.env
  python3 whatsapp_provision.py apply    --config tenants/hubara.env --code 123456
  python3 whatsapp_provision.py ads-token --config tenants/hubara.env
  python3 whatsapp_provision.py ssm-block --config tenants/hubara.env
  python3 whatsapp_provision.py mba-status  --config tenants/hubara.env   # Meta Business Agent: ¿listo?
  python3 whatsapp_provision.py mba-onboard --config tenants/hubara.env   # webhook fields + agent_onboarding

Pasos con human-in-the-loop (no automatizables): conseguir la línea, recibir el
código de verificación, el App Secret, y (Meta-side) Business Verification +
aprobación de display name. `apply` PAUSA pidiendo el código cuando hace falta.

Ver README.md para el runbook completo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com"
API = "v23.0"

# Campos del webhook `whatsapp_business_account` de la app. Meta Business Agent
# exige `standby` (mensajes mientras MBA tiene el hilo) y `messaging_handovers`
# (quién tiene el control); `messages` y el status de templates ya eran nuestros.
WEBHOOK_FIELDS = "messages,message_template_status_update,standby,messaging_handovers"

# Meta Business Agent Cloud API (host propio, sin versión en el path; el header
# X-API-Version manda). Mismo system user token que WhatsApp, con estos scopes.
MBA_HOST = "https://api.facebook.com"
MBA_API_VERSION = "2.0.0"
MBA_SCOPES = ("whatsapp_business_messaging", "whatsapp_business_management")
MBA_WEBHOOK_FIELDS = ("messages", "standby", "messaging_handovers")


# ── Config ───────────────────────────────────────────────────────────────────

CONFIG_KEYS = (
    "TENANT", "BUSINESS_ID", "APP_ID", "APP_SECRET", "WABA_ID",
    "SYSTEM_USER_TOKEN", "CATALOG_ID", "CALLBACK_URL", "VERIFY_TOKEN",
    "NEW_NUMBER_CC", "NEW_NUMBER", "DISPLAY_NAME", "PIN", "CODE_METHOD",
    "LANGUAGE", "API_VERSION", "PHONE_NUMBER_ID",
)


def load_config(path: str) -> dict:
    cfg = {k: "" for k in CONFIG_KEYS}
    if path and os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            # Comentario inline (`WABA_ID=123   # WABA "Hubara"`) — sin esto el id
            # viaja con espacios y Graph responde InvalidURL (ya nos pasó).
            cfg[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
    # El token y el secret pueden venir por env (no dejarlos en el archivo).
    cfg["SYSTEM_USER_TOKEN"] = (
        os.environ.get("META_SYSTEM_USER_TOKEN")
        or os.environ.get("WHATSAPP_ACCESS_TOKEN")
        or cfg["SYSTEM_USER_TOKEN"]
    )
    cfg["APP_SECRET"] = os.environ.get("WHATSAPP_APP_SECRET") or cfg["APP_SECRET"]
    cfg.setdefault("PIN", "")
    if not cfg.get("PIN"):
        cfg["PIN"] = "123456"
    cfg["CODE_METHOD"] = cfg.get("CODE_METHOD") or "SMS"
    cfg["LANGUAGE"] = cfg.get("LANGUAGE") or "es_ES"
    cfg["DISPLAY_NAME"] = cfg.get("DISPLAY_NAME") or "Hubara"
    if not cfg["SYSTEM_USER_TOKEN"]:
        sys.exit("FALTA SYSTEM_USER_TOKEN (en config o env META_SYSTEM_USER_TOKEN)")
    if cfg.get("API_VERSION"):
        global API
        API = cfg["API_VERSION"]
    return cfg


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _api(method: str, path: str, token: str, json_body=None, **fields):
    url = f"{GRAPH}/{API}/{path}"
    headers, data = {}, None
    if json_body is not None:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        data = json.dumps(json_body).encode()
    elif method == "GET":
        fields["access_token"] = token
        url += "?" + urllib.parse.urlencode(fields)
    else:
        fields["access_token"] = token
        data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"error": "non-json"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _repo_root() -> str:
    # infra/whatsapp-provisioning/ → raíz del repo (para resolver paths de flow json)
    return os.path.abspath(os.path.join(_script_dir(), "..", ".."))


def _load_defs(name: str) -> list:
    """Lee definitions/<name>.json — definiciones declarativas tenant-agnósticas
    (templates, flows). Se versionan en git; los IDs resueltos van al ssm-block."""
    path = os.path.join(_script_dir(), "definitions", f"{name}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _post_multipart(path: str, token: str, fields: dict, files: dict):
    """POST multipart/form-data con solo stdlib. files = {campo: (filename, bytes, ctype)}.
    Necesario para subir el FLOW_JSON como asset (Meta exige multipart)."""
    boundary = "----whatsappprovisionboundaryZ7xQ2mKp"
    body = b""
    for k, v in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        ).encode()
    for k, (fn, content, ctype) in files.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{GRAPH}/{API}/{path}", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"error": "non-json"}


# ── Estado real ──────────────────────────────────────────────────────────────

def actual_state(cfg: dict) -> dict:
    t = cfg["SYSTEM_USER_TOKEN"]
    out = {}
    _, dbg = _api("GET", "debug_token", t, input_token=t)
    out["token"] = dbg.get("data", {})
    _, subs = _api("GET", f"{cfg['WABA_ID']}/subscribed_apps", t)
    out["subscribed_app_ids"] = [
        d.get("whatsapp_business_api_data", {}).get("id")
        for d in (subs.get("data") or [])
    ]
    _, nums = _api("GET", f"{cfg['WABA_ID']}/phone_numbers", t,
                   fields="id,display_phone_number,verified_name,status,platform_type,code_verification_status")
    out["numbers"] = nums.get("data") or []
    if cfg.get("CATALOG_ID"):
        _, cat = _api("GET", cfg["CATALOG_ID"], t, fields="id,name,product_count")
        out["catalog"] = cat
    return out


def find_number(state: dict, cfg: dict):
    want = _digits(cfg.get("NEW_NUMBER_CC", "") + cfg.get("NEW_NUMBER", ""))
    for n in state.get("numbers", []):
        if want and _digits(n.get("display_phone_number")) == want:
            return n
    return None


# ── Steps idempotentes ───────────────────────────────────────────────────────

def step_add_number(cfg, state):
    n = find_number(state, cfg)
    if n:
        print(f"  = número ya existe: {n['display_phone_number']} (id {n['id']})")
        return n["id"]
    if not cfg.get("NEW_NUMBER_CC") or not cfg.get("NEW_NUMBER"):
        print("  ! NEW_NUMBER_CC/NEW_NUMBER no seteados — saltando alta")
        return None
    st, body = _api("POST", f"{cfg['WABA_ID']}/phone_numbers", cfg["SYSTEM_USER_TOKEN"],
                    cc=cfg["NEW_NUMBER_CC"], phone_number=cfg["NEW_NUMBER"],
                    verified_name=cfg["DISPLAY_NAME"])
    print(f"  + add-number: {st} {json.dumps(body, ensure_ascii=False)}")
    if st != 200 or not body.get("id"):
        print("  ! Si falló por permisos/tipo de app: agregalo en WhatsApp Manager (UI) "
              "y poné su phone_number_id en la config como PHONE_NUMBER_ID para los pasos siguientes.")
        return None
    return body["id"]


def step_request_code(cfg, phone_id):
    st, body = _api("POST", f"{phone_id}/request_code", cfg["SYSTEM_USER_TOKEN"],
                    code_method=cfg["CODE_METHOD"], language=cfg["LANGUAGE"])
    print(f"  + request-code ({cfg['CODE_METHOD']}): {st} {json.dumps(body, ensure_ascii=False)}")
    return st == 200


def step_verify_code(cfg, phone_id, code):
    st, body = _api("POST", f"{phone_id}/verify_code", cfg["SYSTEM_USER_TOKEN"], code=code)
    print(f"  + verify-code: {st} {json.dumps(body, ensure_ascii=False)}")
    return st == 200


def step_register(cfg, phone_id):
    st, body = _api("POST", f"{phone_id}/register", cfg["SYSTEM_USER_TOKEN"],
                    messaging_product="whatsapp", pin=cfg["PIN"])
    print(f"  + register (PIN 2FA): {st} {json.dumps(body, ensure_ascii=False)}")
    return st == 200


def step_subscribe_app(cfg, state):
    if cfg["APP_ID"] in state.get("subscribed_app_ids", []):
        print("  = app ya suscrita al WABA")
        return True
    st, body = _api("POST", f"{cfg['WABA_ID']}/subscribed_apps", cfg["SYSTEM_USER_TOKEN"])
    print(f"  + subscribe-app: {st} {json.dumps(body, ensure_ascii=False)}")
    return st == 200


def step_commerce_settings(cfg, phone_id):
    st, body = _api("POST", f"{phone_id}/whatsapp_commerce_settings", cfg["SYSTEM_USER_TOKEN"],
                    is_catalog_visible="true", is_cart_enabled="true")
    print(f"  + commerce-settings: {st} {json.dumps(body, ensure_ascii=False)}")
    return st == 200


def step_webhook(cfg):
    if not cfg.get("APP_SECRET"):
        print("  ! webhook: falta APP_SECRET (config o env WHATSAPP_APP_SECRET) — SKIP")
        return False
    app_token = f"{cfg['APP_ID']}|{cfg['APP_SECRET']}"
    # POST /subscriptions REEMPLAZA callback + campos. Si la app ya recibe en otra
    # URL (prod vivo), una config desalineada apuntaría el webhook a un 404 y
    # dejaría el número mudo: no se toca, se alinea CALLBACK_URL primero.
    live = actual_webhook_subscription(cfg)
    if live and live.get("callback_url") and live["callback_url"] != cfg["CALLBACK_URL"]:
        print(f"  ! webhook: la app ya recibe en {live['callback_url']} y la config dice "
              f"{cfg['CALLBACK_URL']} — NO toco el webhook (alineá CALLBACK_URL)")
        return False
    st, body = _api("POST", f"{cfg['APP_ID']}/subscriptions", app_token,
                    object="whatsapp_business_account",
                    callback_url=cfg["CALLBACK_URL"], verify_token=cfg["VERIFY_TOKEN"],
                    fields=WEBHOOK_FIELDS)
    print(f"  + webhook: {st} {json.dumps(body, ensure_ascii=False)}")
    return st == 200 and body.get("success")


def actual_webhook_subscription(cfg):
    """Suscripción viva de la app para `whatsapp_business_account` (app token):
    {callback_url, fields[], active}. None = no se pudo leer; {} = no hay."""
    if not cfg.get("APP_SECRET"):
        return None
    app_token = f"{cfg['APP_ID']}|{cfg['APP_SECRET']}"
    st, body = _api("GET", f"{cfg['APP_ID']}/subscriptions", app_token)
    if st != 200:
        return None
    for sub in body.get("data") or []:
        if sub.get("object") == "whatsapp_business_account":
            return {
                "callback_url": sub.get("callback_url"),
                "active": sub.get("active"),
                "fields": [f.get("name") if isinstance(f, dict) else str(f) for f in sub.get("fields") or []],
            }
    return {}


def actual_webhook_fields(cfg):
    """Campos suscritos hoy (None = no se pudo leer)."""
    live = actual_webhook_subscription(cfg)
    return None if live is None else live.get("fields", [])


# ── Meta Business Agent (D3.1) ───────────────────────────────────────────────

def _mba_api(method: str, entity_id: str, resource: str, token: str, body=None):
    """Una llamada a la Cloud API de Meta Business Agent: Bearer + X-API-Version."""
    url = f"{MBA_HOST}/{entity_id}/{resource}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-API-Version": MBA_API_VERSION,
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode() or "{}"
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, {"error": "non-json", "raw": raw[:200]}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"error": "non-json"}


def mba_phone_id(cfg, state):
    """entity_id de MBA = phone_number_id del número de Sales (config o descubierto)."""
    n = find_number(state, cfg)
    phone_id = (n or {}).get("id") or cfg.get("PHONE_NUMBER_ID")
    if not phone_id:
        ids = ", ".join(f"{x.get('id')} ({x.get('display_phone_number')})" for x in state.get("numbers") or [])
        sys.exit(f"FALTA PHONE_NUMBER_ID en la config (números del WABA: {ids or 'ninguno'})")
    return str(phone_id)


def mba_state(cfg, phone_id, state):
    """Estado real de lo que Meta exige antes de onboardear (token, app, webhook,
    elegibilidad) + los agentes que el número ya tiene."""
    t = cfg["SYSTEM_USER_TOKEN"]
    st, elig = _mba_api("GET", phone_id, "agent_eligibility", t)
    st2, settings = _mba_api("GET", phone_id, "agent_config/settings", t)
    if st2 == 200 and isinstance(settings, list):
        agents = settings
    elif st2 == 200 and isinstance(settings, dict) and ("agent_id" in settings or "rollout" in settings):
        agents = [settings]
    else:
        agents = []
    return {
        "phone_id": phone_id,
        "scopes": (state.get("token") or {}).get("scopes") or [],
        "subscribed": cfg["APP_ID"] in state.get("subscribed_app_ids", []),
        "webhook_fields": actual_webhook_fields(cfg),
        "eligible": elig.get("is_eligible") if st == 200 else None,
        "eligibility_raw": (st, elig),
        "agents": agents,
        "settings_raw": (st2, settings),
    }


def mba_readiness(state):
    """[(code, ok, detail)] — una línea por precondición documentada por Meta."""
    missing_scopes = sorted(set(MBA_SCOPES) - set(state.get("scopes") or []))
    fields = state.get("webhook_fields")
    missing_fields = sorted(set(MBA_WEBHOOK_FIELDS) - set(fields or []))
    eligible = state.get("eligible")
    if eligible is True:
        elig_detail = "agent_eligibility → is_eligible=true"
    elif eligible is None:
        elig_detail = "Meta no contestó agent_eligibility (¿token / Términos de MBA sin aceptar?)"
    else:
        elig_detail = ("is_eligible=false: número no elegible (WhatsApp Manager → pestaña Meta Business "
                       "Agent: configurar el número y aceptar los Términos)")
    return [
        ("token_scopes", not missing_scopes,
         f"faltan scopes {missing_scopes}" if missing_scopes else "whatsapp_business_messaging + management"),
        ("app_subscribed", bool(state.get("subscribed")),
         "app suscrita al WABA" if state.get("subscribed") else "la app NO está suscrita al WABA (apply)"),
        ("webhook_fields", not missing_fields,
         ("faltan campos " + ", ".join(missing_fields)) if missing_fields
         else ("no se pudo leer (sin APP_SECRET)" if fields is None else "messages, standby, messaging_handovers")),
        ("eligible", eligible is True, elig_detail),
    ]


def step_mba_onboard(cfg, phone_id, state):
    """POST agent_onboarding, idempotente: si el número ya tiene agente, no toca
    nada. Devuelve el agent_id o None (con el motivo impreso)."""
    agents = state.get("agents") or []
    if agents:
        aid = agents[0].get("agent_id") or agents[0].get("id")
        print(f"  = número {phone_id} ya onboardeado (agent_id {aid})")
        return aid
    failing = [c for c, ok, _ in mba_readiness(state) if not ok]
    if failing:
        print(f"  ! agent_onboarding NO ejecutado: precondiciones fallidas {failing}")
        return None
    st, body = _mba_api("POST", phone_id, "agent_onboarding", cfg["SYSTEM_USER_TOKEN"], {})
    aid = body.get("agent_id") if st in (200, 201) and isinstance(body, dict) else None
    if not aid:
        print(f"  ! agent_onboarding: {st} {json.dumps(body, ensure_ascii=False)[:300]}")
        return None
    print(f"  + agent_onboarding: {st} agent_id={aid}")
    return aid


def step_mba_lock_audience(cfg, phone_id, agent):
    """Meta onboardea con ai_audience=EVERYONE y followup encendido. Estamos en
    producción: la audiencia se deja en ALLOWLISTED_ONLY (lista cerrada) y el
    followup apagado ANTES de cualquier otra cosa, aunque rollout esté off.
    Idempotente: si ya está así, no llama."""
    want = {"ai_audience": "ALLOWLISTED_ONLY", "followup": {"enabled": False}}
    if agent.get("ai_audience") == "ALLOWLISTED_ONLY" and not (agent.get("followup") or {}).get("enabled"):
        print("  = audiencia ALLOWLISTED_ONLY y followup apagado (ya)")
        return True
    aid = agent.get("agent_id") or agent.get("id")
    resource = "agent_config/settings" + (f"?agent_id={urllib.parse.quote(str(aid))}" if aid else "")
    st, body = _mba_api("PUT", phone_id, resource, cfg["SYSTEM_USER_TOKEN"], want)
    ok = st == 200
    print(f"  {'+' if ok else '!'} settings → ALLOWLISTED_ONLY + followup off: {st} "
          f"{json.dumps(body, ensure_ascii=False)[:200]}")
    return ok


def _print_mba_readiness(state):
    for code, ok, detail in mba_readiness(state):
        print(f"  [{'ok' if ok else '!!'}] {code}: {detail}")


def _print_mba_next_steps(cfg, phone_id):
    print("\n  Env del backend (SSM /hubara/<tenant>/…, claves ya declaradas en Terraform):")
    print(f"    WHATSAPP_PHONE_NUMBER_ID={phone_id}   # = entity_id del agente (agent.yaml lo lee del entorno)")
    print(f"    WHATSAPP_APP_ID={cfg['APP_ID']}")
    print("    META_MBA_TOKEN=<= META_SYSTEM_USER_TOKEN>")
    print("  Manual (una vez, WhatsApp Manager → Meta Business Agent): configurar el número y aceptar los Términos.")
    print("  Billing Hub: NO requerido con ai_audience=ALLOWLISTED_ONLY; obligatorio antes de EVERYONE.")


# ── Flows (formularios nativos) ──────────────────────────────────────────────

def actual_flows(cfg: dict) -> list:
    _, body = _api("GET", f"{cfg['WABA_ID']}/flows", cfg["SYSTEM_USER_TOKEN"],
                   fields="id,name,status,categories")
    return body.get("data") or []


def step_flows(cfg: dict) -> dict:
    """Crea/sube/publica los flows de definitions/flows.json. Idempotente: si ya
    hay un flow PUBLISHED con el mismo nombre lo reusa (los flows son WABA-scoped
    — al migrar de WABA hay que re-publicar). Devuelve {ssm_key: flow_id}."""
    defs = _load_defs("flows")
    if not defs:
        return {}
    existing = {f.get("name"): f for f in actual_flows(cfg)}
    resolved: dict = {}
    for d in defs:
        name = d["name"]
        cur = existing.get(name)
        if cur and cur.get("status") == "PUBLISHED":
            print(f"  = flow ya publicado: {name} (id {cur['id']})")
            if d.get("ssm_key"):
                resolved[d["ssm_key"]] = cur["id"]
            continue
        flow_id = cur.get("id") if cur else None
        if not flow_id:
            st, body = _api("POST", f"{cfg['WABA_ID']}/flows", cfg["SYSTEM_USER_TOKEN"],
                            name=name, categories=json.dumps(d.get("categories", [])))
            flow_id = body.get("id")
            print(f"  + flow create: {name} → {st} id={flow_id}")
            if not flow_id:
                print(f"  ! no se pudo crear el flow: {json.dumps(body, ensure_ascii=False)[:200]}")
                continue
        jpath = os.path.join(_repo_root(), d["json"])
        if not os.path.exists(jpath):
            print(f"  ! flow json no existe: {jpath} — SKIP")
            continue
        content = open(jpath, "rb").read()
        st, body = _post_multipart(
            f"{flow_id}/assets", cfg["SYSTEM_USER_TOKEN"],
            {"name": "flow.json", "asset_type": "FLOW_JSON"},
            {"file": ("flow.json", content, "application/json")},
        )
        errs = body.get("validation_errors") if isinstance(body, dict) else None
        print(f"  + flow asset upload: {name} → {st} validation_errors={errs}")
        if d.get("publish"):
            st, body = _api("POST", f"{flow_id}/publish", cfg["SYSTEM_USER_TOKEN"])
            print(f"  + flow publish: {name} → {st} {json.dumps(body, ensure_ascii=False)}")
        if d.get("ssm_key"):
            resolved[d["ssm_key"]] = flow_id
    return resolved


# ── Message templates ────────────────────────────────────────────────────────

def actual_templates(cfg: dict) -> list:
    _, body = _api("GET", f"{cfg['WABA_ID']}/message_templates", cfg["SYSTEM_USER_TOKEN"],
                   fields="id,name,status,category,language,components", limit="200")
    return body.get("data") or []


def _current_body_text(tmpl: dict) -> str:
    """Extrae el texto del componente BODY de un template ya en Meta (para
    comparar contra la definición y decidir si hay que editar)."""
    for c in tmpl.get("components") or []:
        if (c.get("type") or "").upper() == "BODY":
            return c.get("text") or ""
    return ""


def step_templates(cfg: dict) -> None:
    """Crea/submitea a Meta los templates de definitions/templates.json. Idempotente:
    si ya existe (name+language) NO re-submitea, solo reporta su status de aprobación.
    Los templates son WABA-scoped — al migrar de WABA hay que re-someterlos."""
    defs = _load_defs("templates")
    if not defs:
        print("  ! definitions/templates.json vacío o ausente — SKIP")
        return
    existing = {(t.get("name"), t.get("language")): t for t in actual_templates(cfg)}
    for d in defs:
        cur = existing.get((d["name"], d["language"]))
        if cur:
            print(f"  = template existe: {d['name']} [{d['language']}] status={cur.get('status')}")
            continue
        payload = {
            "name": d["name"],
            "language": d["language"],
            "category": d["category"],
            "components": [{
                "type": "BODY",
                "text": d["body"],
                "example": {"body_text": [d["example"]]},
            }],
        }
        st, body = _api("POST", f"{cfg['WABA_ID']}/message_templates",
                        cfg["SYSTEM_USER_TOKEN"], json_body=payload)
        ok = st == 200 and body.get("id")
        mark = "+" if ok else "!"
        print(f"  {mark} template submit: {d['name']} [{d['language']}] → {st} "
              f"{json.dumps(body, ensure_ascii=False)[:220]}")


def step_templates_update(cfg: dict) -> None:
    """Edita EN META los templates cuya copy (o categoría) difiere de
    definitions/templates.json. Idempotente: si el body ya matchea, NO edita.

    Meta permite editar templates APPROVED/REJECTED/PAUSED (no PENDING) vía
    POST /{template_id} con `components`. Al editar, el template vuelve a
    PENDING hasta re-aprobación — por eso solo tocamos los que realmente
    cambiaron. Name/language son inmutables en la edición (para eso hay que
    borrar+recrear, con cooldown de 30 días); esto solo cambia copy/categoría.
    """
    defs = _load_defs("templates")
    if not defs:
        print("  ! definitions/templates.json vacío o ausente — SKIP")
        return
    existing = {(t.get("name"), t.get("language")): t for t in actual_templates(cfg)}
    for d in defs:
        cur = existing.get((d["name"], d["language"]))
        if not cur:
            print(f"  ! no existe en Meta: {d['name']} [{d['language']}] — "
                  f"correr `templates` (create) primero. SKIP")
            continue
        body_same = _current_body_text(cur).strip() == d["body"].strip()
        cat_same = (cur.get("category") or "").upper() == d["category"].upper()
        if body_same and cat_same:
            print(f"  = sin cambios: {d['name']} [{d['language']}] status={cur.get('status')}")
            continue
        status = (cur.get("status") or "").upper()
        if status == "PENDING":
            print(f"  ! {d['name']} está PENDING — no editable hasta que Meta "
                  f"resuelva. SKIP (reintentar luego)")
            continue
        payload = {
            "category": d["category"],
            "components": [{
                "type": "BODY",
                "text": d["body"],
                "example": {"body_text": [d["example"]]},
            }],
        }
        st, body = _api("POST", cur["id"], cfg["SYSTEM_USER_TOKEN"], json_body=payload)
        ok = st == 200 and body.get("success", True) and not body.get("error")
        mark = "~" if ok else "!"
        print(f"  {mark} template EDIT: {d['name']} [{d['language']}] → {st} "
              f"(vuelve a PENDING) {json.dumps(body, ensure_ascii=False)[:200]}")


# ── CAPI dataset (atribución CTWA) ──────────────────────────────────────────

def actual_capi_dataset(cfg: dict) -> str | None:
    """dataset_id CAPI linkeado al WABA, o None si no hay."""
    _, body = _api("GET", f"{cfg['WABA_ID']}/dataset", cfg["SYSTEM_USER_TOKEN"])
    data = body.get("data") or []
    if data and isinstance(data[0], dict) and data[0].get("id"):
        return data[0]["id"]
    return None


def step_capi(cfg: dict) -> str | None:
    """Crea (si falta) el dataset de Conversions API linkeado al WABA.

    `POST /{WABA_ID}/dataset` crea Y linkea en un solo paso (reemplaza los
    clicks de Events Manager §13+§15 del runbook). Idempotente: si el WABA
    ya tiene dataset, lo reusa. El dataset es la caja donde aterrizan los
    eventos LeadSubmitted/Purchase de atribución CTWA (action_source
    business_messaging) — sin él, los CTWA ads son ciegos.

    OJO: el dataset es WABA-scoped — al migrar de WABA se crea uno nuevo y
    hay que re-pushear META_CAPI_DATASET_ID a SSM.
    """
    ds = actual_capi_dataset(cfg)
    if ds:
        print(f"  = dataset CAPI ya existe y está linkeado: {ds}")
        return ds
    st, body = _api("POST", f"{cfg['WABA_ID']}/dataset", cfg["SYSTEM_USER_TOKEN"])
    ds = body.get("id")
    ok = st == 200 and ds
    mark = "+" if ok else "!"
    print(f"  {mark} dataset CAPI create+link → {st} "
          f"{json.dumps(body, ensure_ascii=False)[:200]}")
    return ds if ok else None


# ── Ads: token store del plugin (single-tenant, sin OAuth) ───────────────────

def cmd_ads_token(cfg, _):
    """Prepara el seed del token store del plugin ads (decisión single-tenant
    2026-07-09: el token de la Marketing API es el SYSTEM USER TOKEN provisionado
    — no hay diálogo OAuth ni App Review; los system users operan las cuentas
    publicitarias PROPIAS del business con Standard Access).

    Verifica EN VIVO que el token tenga los scopes de ads y resuelve la cuenta
    publicitaria, e imprime el `aws ssm put-parameter` listo para correr (el CLI
    no toca SSM — misma filosofía que `ssm-block`). Re-correr tras rotar el token.
    """
    t = cfg["SYSTEM_USER_TOKEN"]
    print("ADS-TOKEN (seed del token store /hubara/<tenant>/meta/oauth):")
    _, dbg = _api("GET", "debug_token", t, input_token=t)
    data = dbg.get("data") or {}
    scopes = data.get("scopes") or []
    needed = {"ads_read", "ads_management"}
    missing = sorted(needed - set(scopes))
    if not data.get("is_valid"):
        print(f"  ! token inválido: {json.dumps(dbg, ensure_ascii=False)[:200]}")
        return
    if missing:
        print(f"  ! al token le faltan scopes de ads: {missing} — regenerarlo en el "
              f"Business Manager (System users) con esos permisos y reintentar.")
        return
    print(f"  = token válido, scopes ads OK (expira: {data.get('expires_at') or 'nunca'})")
    _, accts = _api("GET", "me/adaccounts", t, fields="id,name,account_status,currency", limit="5")
    accounts = accts.get("data") or []
    if not accounts:
        print("  ! el token no ve ninguna cuenta publicitaria — asignar la cuenta al "
              "system user en el Business Manager (Assets → Ad accounts).")
        return
    acct = accounts[0]
    print(f"  = cuenta: {acct['id']} '{acct.get('name')}' ({acct.get('currency')})")
    tenant = (cfg.get("TENANT") or "hubara").strip() or "hubara"
    seed = json.dumps(
        {
            "access_token": t,
            "expires_at": None,
            "scopes": sorted(set(scopes) & {"ads_read", "ads_management", "business_management"}),
            "account_id": acct["id"],
            "account_name": acct.get("name"),
        }
    )
    print("\n  Correr (escribe el SecureString que el backend lee en cada request,")
    print("  sin deploy; OJO: 'Desconectar' no existe en la UI — esto ES la conexión):")
    print(f"  aws ssm put-parameter --name /hubara/{tenant}/meta/oauth "
          f"--type SecureString --overwrite --value '{seed}'")


# ── Comandos ─────────────────────────────────────────────────────────────────

def cmd_discover(cfg, _):
    s = actual_state(cfg)
    tok = s["token"]
    print("== TOKEN ==", "app:", tok.get("application"), "app_id:", tok.get("app_id"),
          "valid:", tok.get("is_valid"))
    print("  scopes:", tok.get("scopes"))
    print("== WABA", cfg["WABA_ID"], "==")
    print("  apps suscritas:", s["subscribed_app_ids"], "(nuestra app:", cfg["APP_ID"], ")")
    for n in s["numbers"]:
        print("  número:", json.dumps(n, ensure_ascii=False))
    if "catalog" in s:
        print("  catálogo:", json.dumps(s["catalog"], ensure_ascii=False))
    tmpls = actual_templates(cfg)
    print("  templates:", ", ".join(
        f"{t.get('name')}[{t.get('status')}]" for t in tmpls) or "(ninguno)")
    flows = actual_flows(cfg)
    print("  flows:", ", ".join(
        f"{f.get('name')}[{f.get('status')}]" for f in flows) or "(ninguno)")


def cmd_plan(cfg, _):
    s = actual_state(cfg)
    n = find_number(s, cfg)
    print("PLAN (desired vs actual):")
    if n:
        print(f"  [ok]  número {cfg.get('NEW_NUMBER_CC','')}{cfg.get('NEW_NUMBER','')} ya dado de alta (id {n['id']}, status {n.get('status')})")
    else:
        print(f"  [+]   ALTA número {cfg.get('NEW_NUMBER_CC','')}{cfg.get('NEW_NUMBER','')} (add-number + request-code + verify-code + register)")
    print("  [ok]  app suscrita al WABA" if cfg["APP_ID"] in s["subscribed_app_ids"]
          else "  [+]   suscribir app al WABA")
    print("  [~]   commerce-settings (catálogo visible + carrito)")
    print("  [~]   webhook callback" + ("" if cfg.get("APP_SECRET") else "  (falta APP_SECRET)"))
    existing_t = {(t.get("name"), t.get("language")) for t in actual_templates(cfg)}
    for d in _load_defs("templates"):
        tag = "[ok] " if (d["name"], d["language"]) in existing_t else "[+]  "
        print(f"  {tag} template {d['name']} [{d['language']}] ({d['category']})")
    existing_f = {f.get("name"): f for f in actual_flows(cfg)}
    for d in _load_defs("flows"):
        f = existing_f.get(d["name"])
        tag = "[ok] " if f and f.get("status") == "PUBLISHED" else "[+]  "
        print(f"  {tag} flow {d['name']}")
    print("\n  Manual / human-in-the-loop: conseguir la línea, código de verificación, "
          "App Secret, Business Verification + display name (Meta-side).")
    print("  Env: tras apply, correr `ssm-block` y subir a SSM + render + recreate (ver README).")


def cmd_apply(cfg, args):
    s = actual_state(cfg)
    print("APPLY (idempotente):")
    phone_id = cfg.get("PHONE_NUMBER_ID") or None
    n = find_number(s, cfg)
    if n:
        phone_id = n["id"]
    needs_verify = False
    if not phone_id:
        phone_id = step_add_number(cfg, s)
        if phone_id:
            step_request_code(cfg, phone_id)
            needs_verify = True
    elif n and n.get("code_verification_status") != "VERIFIED" and args.code:
        needs_verify = True
    if needs_verify:
        if not args.code:
            print(f"\n  >>> Esperando el código de verificación del número (id {phone_id}).")
            print(f"  >>> Cuando llegue por {cfg['CODE_METHOD']}, corré:")
            print(f"  >>> python3 {sys.argv[0]} apply --config {args.config} --code <CODE>")
            return
        step_verify_code(cfg, phone_id, args.code)
        step_register(cfg, phone_id)
    if phone_id:
        step_commerce_settings(cfg, phone_id)
    step_subscribe_app(cfg, s)
    step_webhook(cfg)
    print("  -- flows --")
    step_flows(cfg)
    print("  -- templates --")
    step_templates(cfg)
    print("\n  Hecho. Resolved phone_number_id:", phone_id or "(usar UI)")
    print("  Siguiente: `ssm-block` → subir a SSM → render-env-from-ssm.sh → recreate workers.")


def cmd_ssm_block(cfg, _):
    """Imprime el bloque WHATSAPP_* listo para pegar en secrets.<tenant>.env."""
    s = actual_state(cfg)
    n = find_number(s, cfg)
    phone_id = (n or {}).get("id") or cfg.get("PHONE_NUMBER_ID", "<PHONE_NUMBER_ID>")
    print("# Pegar en infra/scripts/secrets.<tenant>.env y subir con aws_bootstrap.py secrets")
    print(f"WHATSAPP_PHONE_NUMBER_ID={phone_id}")
    print(f"WHATSAPP_BUSINESS_ACCOUNT_ID={cfg['WABA_ID']}")
    print("WHATSAPP_ACCESS_TOKEN=<= META_SYSTEM_USER_TOKEN>")
    print(f"WHATSAPP_VERIFY_TOKEN={cfg['VERIFY_TOKEN']}")
    print("WHATSAPP_APP_SECRET=<App Secret>")
    # Meta Business Agent (D3.1): misma app y mismo system user token.
    print(f"WHATSAPP_APP_ID={cfg['APP_ID']}")
    print("META_MBA_TOKEN=<= META_SYSTEM_USER_TOKEN>")
    ds = actual_capi_dataset(cfg)
    print(f"META_CAPI_DATASET_ID={ds or '<correr: whatsapp_provision.py capi>'}")
    print("META_CAPI_ACCESS_TOKEN=<= META_SYSTEM_USER_TOKEN (ads_management)>")
    flows = actual_flows(cfg)
    for d in _load_defs("flows"):
        if not d.get("ssm_key"):
            continue
        f = next((x for x in flows
                  if x.get("name") == d["name"] and x.get("status") == "PUBLISHED"), None)
        print(f"{d['ssm_key']}={(f or {}).get('id', '<pending publish>')}")


def cmd_templates(cfg, _):
    print("TEMPLATES (submit idempotente a Meta):")
    step_templates(cfg)


def cmd_templates_update(cfg, _):
    print("TEMPLATES UPDATE (edita en Meta solo los que cambiaron → PENDING):")
    step_templates_update(cfg)


def cmd_capi(cfg, _):
    print("CAPI (dataset de atribución CTWA, create+link idempotente):")
    ds = step_capi(cfg)
    if ds:
        print(f"  META_CAPI_DATASET_ID={ds}")
        print("  META_CAPI_ACCESS_TOKEN=<= token con ads_management; el "
              "META_SYSTEM_USER_TOKEN sirve si el system user ve el dataset>")


def cmd_flows(cfg, _):
    print("FLOWS (create/upload/publish idempotente):")
    resolved = step_flows(cfg)
    if resolved:
        print("  resolved:", json.dumps(resolved, ensure_ascii=False))


def cmd_mba_status(cfg, _):
    s = actual_state(cfg)
    phone_id = mba_phone_id(cfg, s)
    st = mba_state(cfg, phone_id, s)
    print(f"MBA STATUS (número {phone_id}):")
    _print_mba_readiness(st)
    agents = st["agents"]
    if agents:
        for a in agents:
            print(f"  = agente: {json.dumps(a, ensure_ascii=False)[:300]}")
    else:
        print(f"  - sin agente onboardeado (GET agent_config/settings → {st['settings_raw'][0]})")
    if st["eligible"] is None:
        print(f"  - agent_eligibility crudo: {json.dumps(st['eligibility_raw'], ensure_ascii=False)[:300]}")
    _print_mba_next_steps(cfg, phone_id)


def cmd_mba_onboard(cfg, _):
    s = actual_state(cfg)
    phone_id = mba_phone_id(cfg, s)
    st = mba_state(cfg, phone_id, s)
    print(f"MBA ONBOARD (número {phone_id}, idempotente):")
    if not st["subscribed"]:
        step_subscribe_app(cfg, s)
        s = actual_state(cfg)
        st = mba_state(cfg, phone_id, s)
    missing_fields = set(MBA_WEBHOOK_FIELDS) - set(st["webhook_fields"] or [])
    if missing_fields and cfg.get("APP_SECRET"):
        print(f"  ~ webhook: suscribiendo {WEBHOOK_FIELDS}")
        step_webhook(cfg)
        st["webhook_fields"] = actual_webhook_fields(cfg)
    _print_mba_readiness(st)
    aid = step_mba_onboard(cfg, phone_id, st)
    if aid:
        st = mba_state(cfg, phone_id, s)
        agent = next((a for a in st["agents"] if (a.get("agent_id") or a.get("id")) == aid), None) or {"agent_id": aid}
        step_mba_lock_audience(cfg, phone_id, agent)
        print(f"\n  Hecho. entity_id={phone_id} agent_id={aid} (rollout sigue apagado: lo enciende D4.5 desde la tab)")
    _print_mba_next_steps(cfg, phone_id)


COMMANDS = {
    "discover": cmd_discover,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "templates": cmd_templates,
    "templates-update": cmd_templates_update,
    "flows": cmd_flows,
    "capi": cmd_capi,
    "ads-token": cmd_ads_token,
    "ssm-block": cmd_ssm_block,
    "mba-status": cmd_mba_status,
    "mba-onboard": cmd_mba_onboard,
}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=list(COMMANDS))
    p.add_argument("--config", required=True, help="ruta a tenants/<tenant>.env")
    p.add_argument("--code", default="", help="código de verificación (para apply)")
    args = p.parse_args()
    cfg = load_config(args.config)
    COMMANDS[args.command](cfg, args)


if __name__ == "__main__":
    main()
