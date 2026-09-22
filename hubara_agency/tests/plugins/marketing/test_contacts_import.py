"""Importación de contactos por CSV — parser puro + audiencia con importados.

El operador sube un archivo con números (una lista externa: feria, base de
clientes de otro canal). Cada número válido se vuelve un destinatario aunque
JAMÁS haya chateado con el bot. Sin efectos: el dominio solo decide.
"""
from src.plugins.marketing.domain.campaigns import (
    SEGMENT_IMPORTADOS,
    new_campaign,
    resolve_campaign_audience,
)
from src.plugins.marketing.domain.contacts import (
    normalize_phone,
    parse_contacts_file,
)


# --- normalize_phone --------------------------------------------------------


def test_normalize_phone_celular_colombiano_recibe_indicativo() -> None:
    assert normalize_phone("3001234567") == "573001234567"
    assert normalize_phone("300 123 4567") == "573001234567"
    assert normalize_phone("+57 300 123 4567") == "573001234567"
    assert normalize_phone("0057 3001234567") == "573001234567"
    assert normalize_phone("57-300-123-4567") == "573001234567"


def test_normalize_phone_rechaza_basura_y_fijos() -> None:
    assert normalize_phone("") is None
    assert normalize_phone("abc") is None
    assert normalize_phone("12345") is None  # muy corto
    assert normalize_phone("6012345678") is None  # fijo de 10 dígitos sin 3
    assert normalize_phone("1" * 20) is None  # más largo que E.164


def test_normalize_phone_acepta_otros_paises_en_e164() -> None:
    assert normalize_phone("+1 555 000 1234") == "15550001234"
    assert normalize_phone("+54 9 11 1234 5678") == "5491112345678"


# --- parse_contacts_file ----------------------------------------------------


def test_parse_csv_con_encabezado_detecta_telefono_y_nombre() -> None:
    text = (
        "nombre,telefono,ciudad\n"
        "Camila,3001234567,Bogotá\n"
        "Andrés,+57 3109876543,Cali\n"
    )
    result = parse_contacts_file(text)
    assert [c.phone for c in result.contacts] == ["573001234567", "573109876543"]
    assert [c.name for c in result.contacts] == ["Camila", "Andrés"]
    assert result.rejected == []
    assert result.duplicates == 0


def test_parse_csv_sin_encabezado_una_columna() -> None:
    text = "3001234567\n3109876543\n\n"
    result = parse_contacts_file(text)
    assert [c.phone for c in result.contacts] == ["573001234567", "573109876543"]
    assert [c.name for c in result.contacts] == [None, None]


def test_parse_csv_separador_punto_y_coma_y_excel_utf8_bom() -> None:
    text = "﻿Celular;Nombre\n3001234567;Camila\n"
    result = parse_contacts_file(text)
    assert [(c.phone, c.name) for c in result.contacts] == [
        ("573001234567", "Camila")
    ]


def test_parse_csv_reporta_rechazados_con_linea_y_dedup() -> None:
    text = (
        "telefono,nombre\n"
        "3001234567,Camila\n"
        "no-es-numero,Pepe\n"
        "300 123 4567,Camila otra vez\n"
        "6012345678,Fijo\n"
    )
    result = parse_contacts_file(text)
    assert [c.phone for c in result.contacts] == ["573001234567"]
    assert result.duplicates == 1
    assert [(r.line, r.reason) for r in result.rejected] == [
        (3, "numero_invalido"),
        (5, "numero_invalido"),
    ]


def test_parse_csv_sin_columna_de_telefono_devuelve_todo_rechazado() -> None:
    result = parse_contacts_file("nombre,ciudad\nCamila,Bogotá\n")
    assert result.contacts == []
    assert result.rejected[0].reason == "sin_columna_telefono"


def test_parse_csv_tolera_lineas_con_texto_extra_en_una_columna() -> None:
    # Un export de WhatsApp/Excel suele traer "Camila - 3001234567".
    result = parse_contacts_file("Camila - 3001234567\nAndrés 3109876543\n")
    assert [c.phone for c in result.contacts] == ["573001234567", "573109876543"]


# --- audiencia con importados ----------------------------------------------


def _campaign(segments, imported):
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["segments"] = segments
    campaign["imported_contacts"] = imported
    return campaign


def test_importado_sin_sesion_entra_como_segmento_importados() -> None:
    campaign = _campaign([], [{"phone": "573001234567", "name": "Camila"}])
    audience = resolve_campaign_audience(campaign, [])
    assert [r.session_id for r in audience.recipients] == ["wa_573001234567"]
    assert audience.recipients[0].segment == SEGMENT_IMPORTADOS
    assert audience.recipients[0].customer_name == "Camila"


def test_importado_con_sesion_respeta_humano_y_opt_out() -> None:
    campaign = _campaign(
        [],
        [
            {"phone": "573001234567", "name": None},
            {"phone": "573109876543", "name": None},
            {"phone": "573201112233", "name": None},
        ],
    )
    sessions = [
        ("wa_573001234567", {"tag": "HUMANO"}),
        ("wa_573109876543", {"marketing_opt_out": True}),
        ("wa_573201112233", {"tag": "INTERESADO", "profile": {"name": "Ana Ruiz"}}),
    ]
    audience = resolve_campaign_audience(campaign, sessions)
    assert [r.session_id for r in audience.recipients] == ["wa_573201112233"]
    # Con sesión real el nombre del vault gana sobre el del CSV (None).
    assert audience.recipients[0].customer_name == "Ana"
    reasons = {s.session_id: s.reason for s in audience.skipped}
    assert reasons["wa_573001234567"] == "excluido"
    assert reasons["wa_573109876543"] == "excluido"


def test_importado_salta_cooldown_pero_no_quiet_hours() -> None:
    now = 1_750_000_000_000
    campaign = _campaign(
        [],
        [{"phone": "573001234567", "name": None}, {"phone": "573109876543", "name": None}],
    )
    sessions = [
        (
            "wa_573001234567",
            {"campaign_touches": [{"campaign_id": "x", "sent_at_ms": now - 1000}]},
        ),
    ]
    audience = resolve_campaign_audience(
        campaign,
        sessions,
        now_ms=now,
        is_quiet_hours=lambda sid: sid == "wa_573109876543",
    )
    assert [r.session_id for r in audience.recipients] == ["wa_573001234567"]
    reasons = {s.session_id: s.reason for s in audience.skipped}
    assert reasons["wa_573109876543"] == "quiet_hours"


def test_importado_quitado_por_operador_no_recibe() -> None:
    campaign = _campaign([], [{"phone": "573001234567", "name": None}])
    campaign["excluded_session_ids"] = ["wa_573001234567"]
    audience = resolve_campaign_audience(campaign, [])
    assert audience.recipients == []
    assert audience.skipped[0].reason == "quitado_por_operador"


def test_importado_no_se_duplica_si_tambien_cae_en_segmento() -> None:
    campaign = _campaign(["clientes"], [{"phone": "573001234567", "name": None}])
    sessions = [("wa_573001234567", {"tag": "COMPRA_EXITOSA"})]
    audience = resolve_campaign_audience(campaign, sessions)
    assert [r.session_id for r in audience.recipients] == ["wa_573001234567"]
    assert audience.recipients[0].segment == "clientes"
