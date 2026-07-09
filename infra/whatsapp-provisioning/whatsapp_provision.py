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
  python3 whatsapp_provision.py app-domains --config tenants/hubara.env
  python3 whatsapp_provision.py ssm-block --config tenants/hubara.env

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


# ── Config ───────────────────────────────────────────────────────────────────

CONFIG_KEYS = (
    "TENANT", "BUSINESS_ID", "APP_ID", "APP_SECRET", "WABA_ID",
    "SYSTEM_USER_TOKEN", "CATALOG_ID", "CALLBACK_URL", "VERIFY_TOKEN",
    "NEW_NUMBER_CC", "NEW_NUMBER", "DISPLAY_NAME", "PIN", "CODE_METHOD",
    "LANGUAGE", "API_VERSION", "APP_DOMAINS", "WEBSITE_URL",
)


def load_config(path: str) -> dict:
    cfg = {k: "" for k in CONFIG_KEYS}
    if path and os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            cfg[k.strip()] = v.strip()
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
    st, body = _api("POST", f"{cfg['APP_ID']}/subscriptions", app_token,
                    object="whatsapp_business_account",
                    callback_url=cfg["CALLBACK_URL"], verify_token=cfg["VERIFY_TOKEN"],
                    fields="messages,message_template_status_update")
    print(f"  + webhook: {st} {json.dumps(body, ensure_ascii=False)}")
    return st == 200 and body.get("success")


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


# ── App settings: dominios OAuth (login Meta del dashboard) ─────────────────

def _app_token(cfg: dict) -> str | None:
    """App access token (`id|secret`) — el que autoriza settings de LA APP
    (dominios, webhook subscriptions), a diferencia del system-user token
    (que autoriza assets del business: WABA, catálogo, dataset)."""
    if not cfg.get("APP_SECRET"):
        return None
    return f"{cfg['APP_ID']}|{cfg['APP_SECRET']}"


def actual_app_settings(cfg: dict) -> dict:
    tok = _app_token(cfg)
    if not tok:
        return {}
    _, body = _api("GET", cfg["APP_ID"], tok, fields="app_domains,website_url")
    return body if isinstance(body, dict) else {}


def step_app_domains(cfg: dict) -> bool:
    """Converge `app_domains` + `website_url` de la app Meta vía API.

    Sin el dominio del redirect_uri en `app_domains`, el diálogo OAuth rebota
    con "El dominio de esta URL no está incluido en los dominios de la app"
    (caso login Meta del plugin ads, 2026-07-09). Editarlo a mano en el
    dashboard es frágil: el campo exige Enter para commitear el chip antes de
    "Guardar cambios" y descarta silenciosamente lo demás. La API es la vía
    autoritativa — y GET después del POST verifica lo que REALMENTE quedó.

    Idempotente y ADITIVO: agrega los APP_DOMAINS que falten sin borrar los
    existentes. Dos gotchas conocidas:
      - error (#10): la app tiene deshabilitado "Allow API Access to App
        Settings" → toggle en Configuración → Avanzada → Seguridad.
      - dominios DNS compartidos (sslip.io, ngrok): Meta puede aceptarlos y
        descartarlos en silencio → el re-GET los delata.
    """
    tok = _app_token(cfg)
    if not tok:
        print("  ! app-domains: falta APP_SECRET (config o env WHATSAPP_APP_SECRET) — SKIP")
        return False
    desired = [d.strip() for d in (cfg.get("APP_DOMAINS") or "").split(",") if d.strip()]
    website = (cfg.get("WEBSITE_URL") or "").strip()
    if not desired and not website:
        print("  ! app-domains: APP_DOMAINS/WEBSITE_URL no seteados en la config — SKIP")
        return False
    cur = actual_app_settings(cfg)
    cur_domains = cur.get("app_domains") or []
    cur_website = cur.get("website_url") or ""
    missing = [d for d in desired if d not in cur_domains]
    website_diff = bool(website) and website != cur_website
    if not missing and not website_diff:
        print(f"  = app_domains ya converge: {cur_domains} website_url={cur_website or '(none)'}")
        return True
    fields: dict = {}
    if missing:
        fields["app_domains"] = json.dumps(cur_domains + missing)
    if website_diff:
        fields["website_url"] = website
    st, body = _api("POST", cfg["APP_ID"], tok, **fields)
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    if err.get("code") == 10:
        print(f"  ! app-domains: la app tiene BLOQUEADO el cambio de settings por API ({st}).")
        print("    Fix (una vez, en el dashboard de la app): Configuración → Avanzada →")
        print("    Seguridad → 'Permitir el acceso de la API a la configuración de la app'")
        print("    → Sí → Guardar cambios. Después re-correr este comando.")
        return False
    ok = st == 200 and body.get("success")
    mark = "+" if ok else "!"
    print(f"  {mark} app-domains POST (+{missing}"
          f"{' website_url' if website_diff else ''}): {st} "
          f"{json.dumps(body, ensure_ascii=False)[:200]}")
    if not ok:
        return False
    # Verificar lo que REALMENTE persistió — Meta descarta en silencio dominios
    # de DNS compartido (sslip.io, ngrok) aunque el POST diga success.
    after = actual_app_settings(cfg)
    dropped = [d for d in desired if d not in (after.get("app_domains") or [])]
    if dropped:
        print(f"  ! Meta DESCARTÓ en silencio: {dropped} (típico con DNS compartido "
              f"tipo sslip.io/ngrok). Usar un dominio propio para el OAuth.")
        return False
    print(f"  = verificado: app_domains={after.get('app_domains')} "
          f"website_url={after.get('website_url') or '(none)'}")
    return True


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
    if cfg.get("APP_SECRET"):
        app = actual_app_settings(cfg)
        print("  app_domains:", app.get("app_domains"), "website_url:", app.get("website_url"))
    else:
        print("  app_domains: (falta APP_SECRET para leerlos)")


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
    desired_domains = [d.strip() for d in (cfg.get("APP_DOMAINS") or "").split(",") if d.strip()]
    if desired_domains:
        cur_domains = actual_app_settings(cfg).get("app_domains") or []
        missing = [d for d in desired_domains if d not in cur_domains]
        print("  [ok]  app_domains (OAuth) converge" if not missing
              else f"  [+]   app_domains (OAuth): agregar {missing}")
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
    if cfg.get("APP_DOMAINS") or cfg.get("WEBSITE_URL"):
        print("  -- app-domains (OAuth) --")
        step_app_domains(cfg)
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


def cmd_app_domains(cfg, _):
    print("APP-DOMAINS (dominios OAuth de la app, converge aditivo + verifica):")
    step_app_domains(cfg)


COMMANDS = {
    "discover": cmd_discover,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "templates": cmd_templates,
    "templates-update": cmd_templates_update,
    "flows": cmd_flows,
    "capi": cmd_capi,
    "app-domains": cmd_app_domains,
    "ssm-block": cmd_ssm_block,
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
