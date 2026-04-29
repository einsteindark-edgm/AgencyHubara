"""Smoke-test de imports: verifica que todos los modulos cuya estructura toco la
Fase 3 se cargan sin errores. Detecta tipicos roturas de path despues de mover
archivos (F3.7) o renombrar simbolos.
"""
from __future__ import annotations

import importlib


def test_workers_importable() -> None:
    importlib.import_module("src.domains.sales_whatsapp.worker")
    importlib.import_module("src.domains.remarketing_whatsapp.worker")


def test_activities_importable() -> None:
    importlib.import_module("src.core.activities")
    importlib.import_module("src.core.infrastructure.whatsapp.activities")
    importlib.import_module("src.domains.sales_whatsapp.activities")
    importlib.import_module("src.domains.remarketing_whatsapp.activities")


def test_workflows_importable() -> None:
    importlib.import_module("src.domains.sales_whatsapp.workflows.sales_session")
    importlib.import_module("src.domains.remarketing_whatsapp.workflows.remarketing")


def test_contracts_importable() -> None:
    mod = importlib.import_module("src.domains.remarketing_whatsapp.contracts")
    assert hasattr(mod, "RemarketingSessionInput")


def test_parsers_importable() -> None:
    mod = importlib.import_module("src.domains.sales_whatsapp.parsers")
    assert hasattr(mod, "parse_whatsapp_inbound")
    assert hasattr(mod, "WhatsAppMessage")


def test_whatsapp_client_canonical_location_importable() -> None:
    """Tras F6.4 el shim `sales_whatsapp.integrations` fue eliminado. La unica
    ubicacion canonica del cliente HTTP de WhatsApp es
    `src.core.infrastructure.whatsapp.client`. Este test bloquea regresiones
    accidentales de path o de simbolo exportado.
    """
    mod = importlib.import_module("src.core.infrastructure.whatsapp.client")
    assert hasattr(mod, "send_message")
    assert callable(mod.send_message)
