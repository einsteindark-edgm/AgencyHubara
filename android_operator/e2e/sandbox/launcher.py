#!/usr/bin/env python3
"""Boot the REAL Hubara API (``src.main:app``, the object ``run_api.py`` serves in
the docker image) against the sandbox data dir, on 127.0.0.1:8010.

Real: the plugin loader + every chats/orders router exactly as the manifests
mount them (incl. mobile + operator_tools), ``require_auth`` (dev no-op: no
Cognito), mobile rules, vault reads/writes, bot UI tools + scoped flush,
history writes, SSE sampler/bus, OrderFacts store, Medusa query/command
adapters, catalog snapshot client, template registry, CAPI outbox (no config →
skipped).

Faked (external systems only — see sandbox_fakes.py):
  * Medusa   → the real ``HttpMedusaClient`` with its transport replaced by an
               emulation of the Admin REST API over data/medusa/store.json
  * Temporal → FakeTemporalClient (records to temporal.log, never connects)
  * WhatsApp → the repo's own FakeSend path (no token) + recorder → sent.log

Hard guards: refuses to start if any credential-looking env var is set, disables
python-dotenv, and installs a socket guard that blocks every outbound TCP
connection / DNS lookup except loopback:8010 (the chats→orders self-cast).

Usage (normally via run_api.sh):
    cd <repo>/hubara_agency && uv run --no-sync python <sandbox>/launcher.py [--check]
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import socket
import sys
from pathlib import Path

SANDBOX_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SANDBOX_DIR))

from sandbox_common import (  # noqa: E402
    CATALOG_DIR,
    DATA_DIR,
    MEDUSA_STORE,
    MOBILE_CONFIG,
    PHONE_NUMBER_ID,
    PORT,
    SEED_INFO,
    STATIC_DIR,
    VAULT_DIR,
    read_json,
)

DEFAULT_REPO = Path(__file__).resolve().parents[3]  # android_operator/e2e/sandbox → raíz del repo
REPO = Path(os.environ.get("SANDBOX_REPO") or DEFAULT_REPO).resolve()
BACKEND = REPO / "hubara_agency"
HOST = "127.0.0.1"

log = logging.getLogger("sandbox")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Environment: no credentials, sandbox paths, dead endpoints
# ─────────────────────────────────────────────────────────────────────────────

#: Any of these set = this is NOT a clean sandbox environment → refuse to start.
SECRET_VARS = (
    "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_BUSINESS_ACCOUNT_ID", "WHATSAPP_APP_ID",
    "META_SYSTEM_USER_TOKEN", "META_CAPI_ACCESS_TOKEN", "META_CAPI_DATASET_ID", "META_APP_ID", "META_APP_SECRET",
    "META_ACCESS_TOKEN", "META_CATALOG_ID", "META_FLOW_ID_SHIPPING",
    "MEDUSA_BASE_URL", "MEDUSA_ADMIN_TOKEN", "MEDUSA_ADMIN_EMAIL", "MEDUSA_ADMIN_PASSWORD",
    "MEDUSA_PUBLISHABLE_API_KEY", "MEDUSA_REGION_ID", "MEDUSA_SALES_CHANNEL_ID",
    "TEMPORAL_API_KEY", "TEMPORAL_TLS_CERT_PATH", "TEMPORAL_TLS_KEY_PATH",
    "COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_ID", "HUBARA_SERVICE_TOKEN",
    "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY", "LITELLM_API_KEY", "LITELLM_MASTER_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
    "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS",
    "PAYMENT_TRANSFER_ACCOUNT_NUMBER", "PAYMENT_TRANSFER_HOLDER_ID",
)

SANDBOX_ENV = {
    "HUBARA_ENV": "dev",  # dev = require_auth no-op without Cognito (prod would be fail-closed 503)
    "PYTHON_DOTENV_DISABLED": "1",
    "ENABLED_PLUGINS": "chats,orders",  # chats depends_on orders (validated by the real loader)
    "WORKSPACE_VAULT_DIR": str(VAULT_DIR),
    "CATALOG_SNAPSHOT_DIR": str(CATALOG_DIR),
    "CATALOG_MAX_AGE_MINUTES": "10080",
    "WHATSAPP_PHONE_NUMBER_ID": PHONE_NUMBER_ID,
    "PAYMENT_NEQUI_NUMBER": "@llave-sandbox",
    "HUBARA_TENANT_ID": "sandbox",
    # Loopback self-casts (chats→orders, mba/agents_admin→chats) go to THIS app, never :8000.
    "ORDERS_API_BASE": f"http://{HOST}:{PORT}",
    "CHATS_API_BASE": f"http://{HOST}:{PORT}",
    # Dead endpoints: even if some path tried, nothing listens there (and the socket guard blocks it).
    "API_BASE_LLMLITE": f"http://{HOST}:9",
    "TEMPORAL_URL": f"{HOST}:9",
    "TEMPORAL_ADDRESS": f"{HOST}:9",
    "OTEL_SDK_DISABLED": "true",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "RATE_LIMIT_PER_MINUTE": "0",
}


def _abort(msg: str) -> None:
    print(f"[sandbox] ABORT: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(2)


def prepare_environment() -> None:
    present = [v for v in SECRET_VARS if os.environ.get(v)]
    if present:
        _abort(f"credential-like env vars are set ({', '.join(present)}). Start through run_api.sh (env -i).")
    if os.environ.get("HUBARA_ENV", "dev").strip().lower() in {"production", "prod"}:
        _abort("HUBARA_ENV=production is not allowed in the sandbox")
    if not (MEDUSA_STORE.exists() and (VAULT_DIR).is_dir() and (CATALOG_DIR / "snapshot.json").exists()):
        _abort(f"sandbox data missing under {DATA_DIR} — run seed.py first")
    os.environ.update(SANDBOX_ENV)


def disable_dotenv() -> None:
    """Belt and braces on top of PYTHON_DOTENV_DISABLED: config.py calls
    ``load_dotenv()`` at import; make every entry point a no-op."""
    import dotenv
    import dotenv.main

    def _off(*_a: object, **_k: object) -> bool:
        return False

    for mod in (dotenv, dotenv.main):
        mod.load_dotenv = _off  # type: ignore[assignment]
        mod.find_dotenv = lambda *a, **k: ""  # type: ignore[assignment]
        mod.dotenv_values = lambda *a, **k: {}  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Network guard: loopback:8010 only
# ─────────────────────────────────────────────────────────────────────────────

_LOCAL_NAMES = {"localhost", "ip6-localhost", "localhost.localdomain"}


def _is_loopback(host: object) -> bool:
    if host is None:
        return True
    text = host.decode() if isinstance(host, bytes) else str(host)
    if text in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(text.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def install_socket_guard() -> None:
    from sandbox_fakes import BLOCKED_LOG, log_line

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex

    def _blocked(kind: str, host: object, port: object) -> None:
        log_line(BLOCKED_LOG, {"blocked": kind, "host": str(host), "port": port})
        log.error("[sandbox] BLOCKED outbound %s to %s:%s", kind, host, port)

    def _check(sock: socket.socket, address: object) -> None:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return  # AF_UNIX (event loop self-pipes etc.)
        host, port = address[0], address[1]  # type: ignore[index]
        if _is_loopback(host) and port == PORT:
            return
        _blocked("connect", host, port)
        raise ConnectionRefusedError(f"sandbox: outbound connection to {host}:{port} is blocked")

    def guarded_connect(self: socket.socket, address: object) -> None:
        _check(self, address)
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        _check(self, address)
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host: object, port: object, *args: object, **kwargs: object):  # noqa: ANN202
        if not _is_loopback(host):
            _blocked("dns", host, port)
            raise socket.gaierror(socket.EAI_NONAME, f"sandbox: DNS lookup of {host!r} is blocked")
        return real_getaddrinfo(host, port, *args, **kwargs)

    def guarded_gethostbyname(host: str) -> str:
        if not _is_loopback(host):
            _blocked("dns", host, None)
            raise socket.gaierror(socket.EAI_NONAME, f"sandbox: DNS lookup of {host!r} is blocked")
        return real_gethostbyname(host)

    def guarded_gethostbyname_ex(host: str):  # noqa: ANN202
        if not _is_loopback(host):
            _blocked("dns", host, None)
            raise socket.gaierror(socket.EAI_NONAME, f"sandbox: DNS lookup of {host!r} is blocked")
        return real_gethostbyname_ex(host)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = guarded_gethostbyname  # type: ignore[assignment]
    socket.gethostbyname_ex = guarded_gethostbyname_ex  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Real app + external fakes
# ─────────────────────────────────────────────────────────────────────────────


def build_app():  # noqa: ANN201, C901 — linear boot sequence
    import sandbox_fakes

    os.chdir(DATA_DIR)  # any relative path the app might use lands in the sandbox
    sys.path.insert(0, str(BACKEND))

    from src.platform import config

    if config.WHATSAPP_ACCESS_TOKEN or config.META_CAPI_ACCESS_TOKEN or config.is_production():
        _abort("config picked up credentials / production mode")
    if Path(config.WORKSPACE_VAULT_DIR).resolve() != VAULT_DIR.resolve():
        _abort(f"vault dir mismatch: {config.WORKSPACE_VAULT_DIR}")

    # WhatsApp: repo FakeSend + recorder.
    from src.platform.whatsapp import client as wa_client
    from src.platform.whatsapp import dtos as wa_dtos

    wa_originals = {name: getattr(wa_client, name)
                    for name in ("_post_json", "send_message", "upload_media", "send_typing_indicator")}
    sandbox_fakes.install_whatsapp_recorder(wa_client, wa_dtos)

    # Temporal: fake client (patched BEFORE any module does `from ... import get_temporal_client`).
    from src.platform.temporal import client as temporal_client

    orig_get_temporal = temporal_client.get_temporal_client
    temporal_client.get_temporal_client = sandbox_fakes.fake_get_temporal_client

    # Medusa: real HttpMedusaClient methods over an emulated transport.
    from src.platform.medusa import composition as medusa_comp
    from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError
    from src.platform.medusa.settings import MedusaSettings

    medusa_cls = sandbox_fakes.build_medusa_client_class(HttpMedusaClient, MedusaAPIError)
    fake_medusa = medusa_cls(MEDUSA_STORE)
    # region/sales channel unset on purpose → registration port = the repo's StubOrderRegistration.
    fake_settings = MedusaSettings(base_url=fake_medusa.base_url, admin_token=fake_medusa._admin_token)
    orig_get_medusa_client = medusa_comp.get_medusa_client
    orig_get_medusa_settings = medusa_comp.get_medusa_settings
    medusa_comp.get_medusa_client = lambda: fake_medusa
    medusa_comp.get_medusa_settings = lambda: fake_settings

    # The REAL app: plugin loader mounts chats + orders exactly as in production.
    import src.main as main_mod

    # Sweep: nobody may keep a reference to an original external entry point.
    replacements = {
        id(orig_get_temporal): (orig_get_temporal, sandbox_fakes.fake_get_temporal_client),
        id(orig_get_medusa_client): (orig_get_medusa_client, medusa_comp.get_medusa_client),
        id(orig_get_medusa_settings): (orig_get_medusa_settings, medusa_comp.get_medusa_settings),
        **{id(fn): (fn, getattr(wa_client, name)) for name, fn in wa_originals.items()},
    }
    swept: list[str] = []
    for mod_name, mod in list(sys.modules.items()):
        if mod is None or not (mod_name == "src" or mod_name.startswith("src.")):
            continue
        for attr, value in list(vars(mod).items()):
            hit = replacements.get(id(value))
            if hit is not None and value is hit[0]:
                setattr(mod, attr, hit[1])
                swept.append(f"{mod_name}.{attr}")
    real_patched = {"src.platform.temporal.client.get_temporal_client",
                    "src.platform.medusa.composition.get_medusa_client",
                    "src.platform.medusa.composition.get_medusa_settings"}
    unexpected = [s for s in swept if s not in real_patched]
    if unexpected:
        log.warning("[sandbox] swept late references to external entry points: %s", unexpected)

    # Drop any composition singleton built during import (none expected) so it
    # is rebuilt lazily on top of the fakes.
    import src.platform.catalog.composition as catalog_comp
    import src.platform.orders.composition as orders_comp
    import src.platform.promotions.composition as promo_comp
    from src.plugins.chats.api import mobile, operator_tools, session_actions

    for fn in (orders_comp.get_order_registration_port, orders_comp.get_order_query_port,
               orders_comp.get_order_facts_port, orders_comp._raw_order_query, orders_comp.get_order_command_port,
               medusa_comp.get_medusa_product_service, promo_comp.get_promotions_port,
               promo_comp.get_promotions_admin_port, promo_comp.get_coupon_sales_reader,
               catalog_comp.get_catalog_client, mobile.get_mobile_deps, operator_tools.get_operator_tools_deps,
               session_actions.get_session_actions_deps):
        fn.cache_clear()

    app = main_mod.app
    _mount_sandbox_extras(app, main_mod, swept)
    return app, main_mod


def _mount_sandbox_extras(app, main_mod, swept: list[str]) -> None:  # noqa: ANN001
    """Sandbox-only additions, all under /__sandbox (nothing real is shadowed)."""
    from fastapi.staticfiles import StaticFiles

    app.mount("/__sandbox/static", StaticFiles(directory=str(STATIC_DIR)), name="sandbox-static")

    # La configuración del servidor de la App Operador (en producción, <cloudfront>/mobile/config.json que publica
    # frontend-deploy). El APK de prueba trae una dirección muerta de respaldo: si la app llega a la bandeja es porque
    # tomó el backend de aquí. `inject.py config --min-version N` cambia la versión mínima.
    @app.get("/__sandbox/mobile/config.json", include_in_schema=False)
    def sandbox_mobile_config() -> dict:
        override = read_json(MOBILE_CONFIG, {})
        return {
            "version": 1,
            "api_base_url": f"http://10.0.2.2:{PORT}",
            "cognito_region": "us-east-1",
            "cognito_client_id": "",
            "min_version_code": int(override.get("min_version_code", 0)),
        }

    @app.get("/__sandbox/info", include_in_schema=False)
    def sandbox_info() -> dict:
        return {
            "sandbox": True,
            "plugins_loaded": main_mod._LOADED_PLUGINS,
            "data_dir": str(DATA_DIR),
            "fakes": {
                "whatsapp": "repo FakeSend (no WHATSAPP_ACCESS_TOKEN) + recorder → sent.log",
                "temporal": "FakeTemporalClient → temporal.log (no connection)",
                "medusa": "HttpMedusaClient with emulated transport over data/medusa/store.json → medusa.log",
                "network": f"socket guard: only {HOST}:{PORT} reachable; blocked attempts → blocked.log",
            },
            "swept_references": swept,
            "seed": read_json(SEED_INFO, {}),
        }


async def watch_medusa_store() -> None:
    """Out-of-process edits of the fake Medusa store (inject.py / reset) are
    announced like any orders mutation: ``orders.changed`` on the dashboard bus
    → OrderFactsStore invalidates + SSE clients refetch."""
    from src.sdk.dashboardkit import get_dashboard_event_bus

    bus = get_dashboard_event_bus()
    last: int | None = None
    while True:
        await asyncio.sleep(1.0)
        try:
            stamp = MEDUSA_STORE.stat().st_mtime_ns
        except OSError:
            continue
        if last is not None and stamp != last:
            bus.publish("orders", "changed")
        last = stamp


async def serve(app) -> None:  # noqa: ANN001
    import uvicorn

    config = uvicorn.Config(app, host=HOST, port=PORT, loop="asyncio", http="h11", lifespan="on",
                            log_level="info", access_log=True)
    server = uvicorn.Server(config)
    watcher = asyncio.create_task(watch_medusa_store())
    try:
        await server.serve()
    finally:
        watcher.cancel()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="boot + wire everything, print routes, exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    prepare_environment()
    disable_dotenv()
    install_socket_guard()
    app, main_mod = build_app()

    routes = sorted({getattr(r, "path", "") for r in app.routes})
    wanted = [
        "/api/dashboard/sessions", "/api/dashboard/sessions/{session_id}",
        "/api/dashboard/sessions/{session_id}/intervene", "/api/dashboard/sessions/{session_id}/return-to-bot",
        "/api/dashboard/sessions/{session_id}/messages", "/api/dashboard/whatsapp-templates",
        "/api/dashboard/sessions/{session_id}/template-messages", "/api/dashboard/sse-ticket",
        "/api/dashboard/events", "/api/dashboard/media/{session_id}/{filename}",
        "/api/chats/mobile/suggestions/{session_id}", "/api/chats/mobile/fires", "/api/chats/mobile/hot",
        "/api/chats/mobile/devices", "/api/chats/mobile/devices/{token}", "/api/chats/catalog",
        "/api/chats/session-actions/{session_key}/tools/{tool}", "/api/orders/orders",
        "/api/orders/orders/{order_id}", "/api/orders/orders/{order_id}/stage",
    ]
    missing = [p for p in wanted if p not in routes]
    if missing:
        _abort(f"the real app did not mount: {missing}")
    print(json.dumps({"plugins_loaded": main_mod._LOADED_PLUGINS, "app_routes": len(routes),
                      "required_routes_present": len(wanted)}), flush=True)
    if args.check:
        return

    (SANDBOX_DIR / "api.pid").write_text(str(os.getpid()), encoding="utf-8")
    try:
        asyncio.run(serve(app))
    finally:
        try:
            (SANDBOX_DIR / "api.pid").unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
