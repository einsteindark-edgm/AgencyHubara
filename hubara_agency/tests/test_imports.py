"""Smoke-test de imports: verifica que todos los modulos cuya estructura toco la
Fase 3 se cargan sin errores. Detecta tipicos roturas de path despues de mover
archivos (F3.7) o renombrar simbolos.
"""
from __future__ import annotations

import importlib


def test_workers_importable() -> None:
    importlib.import_module("src.sales_whatsapp.worker")
    importlib.import_module("src.remarketing_whatsapp.worker")


def test_activities_importable() -> None:
    importlib.import_module("src.platform.temporal.activities")
    importlib.import_module("src.platform.whatsapp.activities")
    importlib.import_module("src.sales_whatsapp.activities")
    importlib.import_module("src.remarketing_whatsapp.activities")


def test_workflows_importable() -> None:
    importlib.import_module("src.sales_whatsapp.workflows.sales_session")
    importlib.import_module("src.remarketing_whatsapp.workflows.remarketing")


def test_contracts_importable() -> None:
    mod = importlib.import_module("src.remarketing_whatsapp.contracts")
    assert hasattr(mod, "RemarketingSessionInput")


def test_parsers_importable() -> None:
    mod = importlib.import_module("src.sales_whatsapp.parsers")
    assert hasattr(mod, "parse_whatsapp_inbound")
    assert hasattr(mod, "WhatsAppMessage")


def test_whatsapp_client_canonical_location_importable() -> None:
    """La unica ubicacion canonica del cliente HTTP de WhatsApp es
    `src.platform.whatsapp.client` (post PR-F: ex-`src.core.infrastructure.whatsapp.client`).
    """
    mod = importlib.import_module("src.platform.whatsapp.client")
    assert hasattr(mod, "send_message")
    assert callable(mod.send_message)
