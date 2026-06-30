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
    "LANGUAGE", "API_VERSION",
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


COMMANDS = {
    "discover": cmd_discover,
    "plan": cmd_plan,
    "apply": cmd_apply,
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
