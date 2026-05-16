"""Smoke-test de imports: verifica que todos los modulos cuya estructura toco la
Fase 3 se cargan sin errores. Detecta tipicos roturas de path despues de mover
archivos (F3.7) o renombrar simbolos.
"""
from __future__ import annotations

import importlib


def test_workers_importable() -> None:
    importlib.import_module("src.plugins.chats.workers.sales")
    importlib.import_module("src.plugins.chats.workers.remarketing")
    importlib.import_module("src.plugins.catalog.workers.sync")


def test_activities_importable() -> None:
    importlib.import_module("src.platform.temporal.activities")
    importlib.import_module("src.platform.whatsapp.activities")
    importlib.import_module("src.plugins.chats.agent.sales.activities")
    importlib.import_module("src.plugins.chats.agent.remarketing.activities")
    importlib.import_module("src.plugins.catalog.agent.activities")


def test_workflows_importable() -> None:
    importlib.import_module("src.plugins.chats.agent.sales.workflows.sales_session")
    importlib.import_module("src.plugins.chats.agent.remarketing.workflows.remarketing")
    importlib.import_module("src.plugins.catalog.agent.workflows.sync")


def test_contracts_importable() -> None:
    mod = importlib.import_module("src.plugins.chats.agent.remarketing.contracts")
    assert hasattr(mod, "RemarketingSessionInput")


def test_parsers_importable() -> None:
    mod = importlib.import_module("src.plugins.chats.agent.sales.parsers")
    assert hasattr(mod, "parse_whatsapp_inbound")
    assert hasattr(mod, "WhatsAppMessage")


def test_whatsapp_client_canonical_location_importable() -> None:
    """La unica ubicacion canonica del cliente HTTP de WhatsApp es
    `src.platform.whatsapp.client` (post PR-F: ex-`src.core.infrastructure.whatsapp.client`).
    """
    mod = importlib.import_module("src.platform.whatsapp.client")
    assert hasattr(mod, "send_message")
    assert callable(mod.send_message)
