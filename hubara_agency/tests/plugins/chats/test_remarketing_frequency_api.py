"""Contrato `remarketing-frequency@v1` de chats: cuántos toques máximo hace el
bot de remarketing. El panel de Agents → Remarketing → Frecuencia lo consume
por cast (agents_admin) y lo EDITA (operador, 2026-10-07: «dashboard editable,
solo cantidad, aplica desde ya»).

GET devuelve el tope efectivo, el techo de Terraform (`REMARKETING_MAX_TOUCHES`)
y la escalera para previsualizar cuándo sale cada toque. PUT guarda un entero
de 0 al techo; nunca lo supera (el techo lo sube Terraform, no el dashboard).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.whatsapp.reengagement_frequency import CEILING_ENV
from src.plugins.chats.api import remarketing_frequency as api

H = 60 * 60 * 1000
PATH = "/api/chats/remarketing/frequency"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api, "_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(api, "_now_ms", lambda: 1_790_200_000_000)
    monkeypatch.delenv(CEILING_ENV, raising=False)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/chats")
    return TestClient(app)


def test_get_sin_nada_guardado_reporta_la_escalera_completa(client: TestClient) -> None:
    body = client.get(PATH).json()

    assert body["max_touches"] == 5
    assert body["ceiling"] == 5
    assert body["saved"] is None
    assert body["updated_by"] is None
    # +2h, +4h, +8h, +14h, +20h desde el último mensaje del cliente.
    assert [s["after_ms"] for s in body["ladder"]] == [2 * H, 4 * H, 8 * H, 14 * H, 20 * H]
    assert [s["touch"] for s in body["ladder"]] == [1, 2, 3, 4, 5]


def test_put_guarda_y_el_get_siguiente_lo_refleja(client: TestClient) -> None:
    res = client.put(PATH, json={"max_touches": 2})

    assert res.status_code == 200
    body = client.get(PATH).json()
    assert (body["max_touches"], body["saved"]) == (2, 2)
    assert body["updated_by"] == "dashboard:operator"
    assert body["updated_at_ms"] == 1_790_200_000_000
    assert res.json()["max_touches"] == 2


def test_put_cero_apaga_la_reactivacion(client: TestClient) -> None:
    assert client.put(PATH, json={"max_touches": 0}).status_code == 200
    assert client.get(PATH).json()["max_touches"] == 0


def test_put_por_encima_del_techo_se_rechaza_y_no_cambia_nada(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CEILING_ENV, "3")
    client.put(PATH, json={"max_touches": 2})

    res = client.put(PATH, json={"max_touches": 4})

    assert res.status_code == 422
    assert res.json()["detail"] == {"reason": "above_ceiling", "ceiling": 3}
    assert client.get(PATH).json()["max_touches"] == 2


@pytest.mark.parametrize("raro", [-1, 2.5, "3", True, None])
def test_put_con_algo_que_no_es_un_entero_valido_se_rechaza(client: TestClient, raro) -> None:
    assert client.put(PATH, json={"max_touches": raro}).status_code == 422
    assert client.get(PATH).json()["saved"] is None


def test_el_techo_de_terraform_baja_el_tope_efectivo_aunque_lo_guardado_sea_mayor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put(PATH, json={"max_touches": 5})
    monkeypatch.setenv(CEILING_ENV, "2")

    body = client.get(PATH).json()
    assert (body["saved"], body["ceiling"], body["max_touches"]) == (5, 2, 2)
