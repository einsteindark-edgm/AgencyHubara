"""Composición del puerto de avisos push.

`PUSH_PROVIDER`:

  auto (por defecto)  # FCM si están las dos llaves; si no, nulo (sin avisos, la app sigue con el vigía)
  fake                # el falso (tests, backend del emulador); `PUSH_FAKE_OUTBOX` = archivo donde deja cada aviso
  off / null          # interruptor: no se manda nada

Llaves (SSM → `.env`): `FCM_SERVICE_ACCOUNT_JSON` y `FIREBASE_ANDROID_CONFIG_JSON`
(ver `adapters/fcm_v1.py`). `MOBILE_ANDROID_PACKAGE` (por defecto
`com.hubara.operator`) elige el cliente Android dentro de `google-services.json`.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import structlog

from src.platform.push.adapters.fake import FakePushAdapter
from src.platform.push.adapters.fcm_v1 import FcmV1PushAdapter, android_client_options, service_account
from src.platform.push.adapters.null import NullPushAdapter
from src.platform.push.ports import PushPort

logger = structlog.get_logger()

DEFAULT_ANDROID_PACKAGE = "com.hubara.operator"


@lru_cache(maxsize=1)
def get_push_port() -> PushPort:
    provider = (os.getenv("PUSH_PROVIDER") or "auto").strip().lower()
    if provider == "fake":
        outbox = (os.getenv("PUSH_FAKE_OUTBOX") or "").strip()
        return FakePushAdapter(outbox=Path(outbox) if outbox else None)
    if provider in {"off", "null", "disabled"}:
        return NullPushAdapter()
    account = service_account(os.getenv("FCM_SERVICE_ACCOUNT_JSON"))
    if account is None:
        return NullPushAdapter()
    package = (os.getenv("MOBILE_ANDROID_PACKAGE") or DEFAULT_ANDROID_PACKAGE).strip()
    client = android_client_options(os.getenv("FIREBASE_ANDROID_CONFIG_JSON"), package=package)
    if client is None:
        logger.warning("push.no_android_app", package=package)
    elif client.project_id != account["project_id"]:
        # Cada aviso volvería SENDER_ID_MISMATCH y se borrarían los tokens de todos los teléfonos.
        logger.error("push.project_mismatch", service_account=account["project_id"], android=client.project_id)
        return NullPushAdapter()
    return FcmV1PushAdapter(account=account, client=client)
