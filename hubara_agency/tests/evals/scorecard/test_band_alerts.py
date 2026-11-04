"""Alertas de las bandas por regla (Ciclo Colossus F4).

Una regla en `abrir` abre un issue `[ALR]` con la etiqueta `origen:alerta` en
el repo del ciclo (la recepción lo vuelve tarjeta), o comenta «volvió a pasar»
en el que ya está abierto (huella por regla). Sin configuración, no-op. El
cuerpo nunca lleva un número completo ni evidencia sin redactar.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard import alerts, band_alerts


class FakeTracker:
    def __init__(self, existing: dict[str, int] | None = None) -> None:
        self.existing = dict(existing or {})
        self.created: list[tuple[str, str, list[str]]] = []
        self.comments: list[tuple[int, str]] = []

    async def find_open(self, fingerprint: str) -> int | None:
        return self.existing.get(fingerprint)

    async def create(self, title: str, body: str, labels: list[str]) -> int:
        self.created.append((title, body, labels))
        return 51

    async def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))


def _row(**over) -> dict:
    row = {
        "regla": "VAR-06",
        "nombre": "No vuelve a preguntar la cantidad",
        "nivel": "mayor",
        "familia": "variantes",
        "banda": "abrir",
        "motivo": "banda_3σ",
        "reciente": {"fallas": 42, "turnos": 700, "tasa": 6.0},
        "base": {"fallas": 28, "turnos": 2800, "tasa": 1.0, "dias": 28},
        "sigma": 0.376,
        "dispersion": 1.0,
        "desvio": 13.3,
        "ejemplos": [
            {"sesion": "…0001", "episodio": "ep_012", "turno": 3, "fecha": "2026-10-30",
             "evidencia": "repreguntó la cantidad"},
        ],
    }
    row.update(over)
    return row


def _report(*rows: dict) -> dict:
    return {
        "generado": "2026-10-31T04:00:00+00:00",
        "ventanas": {
            "reciente": {"desde": "2026-10-25", "hasta": "2026-10-31", "dias": 7},
            "base": {"desde": "2026-09-27", "hasta": "2026-10-24", "dias": 28},
        },
        "bot": "nuevo",
        "reglas": list(rows),
    }


async def test_a_rule_out_of_its_band_opens_an_alert_for_the_cycle() -> None:
    tracker = FakeTracker()

    outcome = await band_alerts.notify_bands(_report(_row()), tracker=tracker)

    assert outcome == [{"regla": "VAR-06", "accion": "abierta", "issue": 51}]
    title, body, labels = tracker.created[0]
    assert title == "[ALR] Banda 3σ: VAR-06 No vuelve a preguntar la cantidad"
    assert labels == ["origen:alerta"]
    for expected in ("6.0", "42", "700", "1.0", "28", "2800", "0.376", "13.3", "2026-10-25", "2026-09-27"):
        assert expected in body, expected
    assert "Calidad LLM" in body
    assert "Colossus" in body  # solo cuenta el bot en producción
    assert "…0001" in body and "ep_012" in body and "turno 3" in body
    assert band_alerts.fingerprint("VAR-06") in body


async def test_a_new_critical_failure_says_so_in_the_title() -> None:
    tracker = FakeTracker()
    row = _row(regla="CIE-04", nombre="Escala la verificación de pago tras registrar", nivel="critico",
               motivo="critica_nueva", reciente={"fallas": 1, "turnos": 703, "tasa": 0.14},
               base={"fallas": 0, "turnos": 2800, "tasa": 0.0, "dias": 28})

    await band_alerts.notify_bands(_report(row), tracker=tracker)

    assert tracker.created[0][0] == "[ALR] Crítica nueva: CIE-04 Escala la verificación de pago tras registrar"


async def test_an_open_alert_gets_a_comment_instead_of_a_duplicate() -> None:
    tracker = FakeTracker(existing={band_alerts.fingerprint("VAR-06"): 7})

    outcome = await band_alerts.notify_bands(_report(_row()), tracker=tracker)

    assert tracker.created == []
    assert outcome == [{"regla": "VAR-06", "accion": "comentada", "issue": 7}]
    number, comment = tracker.comments[0]
    assert number == 7
    assert comment.startswith("Volvió a pasar")
    assert "2026-10-31" in comment and "6.0" in comment and "…0001" in comment


async def test_only_rules_in_the_open_band_alert() -> None:
    tracker = FakeTracker()
    rows = [_row(regla=r, banda=b) for r, b in (("DES-01", "diagnosticar"), ("DES-02", "anotar"), ("EST-01", "normal"))]

    assert await band_alerts.notify_bands(_report(*rows), tracker=tracker) == []
    assert tracker.created == [] and tracker.comments == []


async def test_without_configuration_nothing_is_sent(monkeypatch) -> None:
    monkeypatch.delenv(alerts.TOKEN_ENV, raising=False)
    monkeypatch.delenv(alerts.REPO_ENV, raising=False)

    assert await band_alerts.notify_bands(_report(_row())) == []


async def test_evidence_and_examples_never_carry_phones_or_emails() -> None:
    tracker = FakeTracker()
    row = _row(ejemplos=[{"sesion": "…4567", "episodio": "ep_002", "turno": 1, "fecha": "2026-10-30",
                          "evidencia": "le escribió al 3001234567 y a ana@correo.com"}])

    await band_alerts.notify_bands(_report(row), tracker=tracker)

    body = tracker.created[0][1]
    assert "3001234567" not in body and "ana@correo.com" not in body


def test_each_rule_has_its_own_fingerprint() -> None:
    """La huella va en el cuerpo y se busca como texto: la de EST-03 no puede
    ser prefijo de la de EST-03b."""
    a, b = band_alerts.fingerprint("EST-03"), band_alerts.fingerprint("EST-03b")

    assert a != b and not b.startswith(a) and not a.startswith(b)
    assert band_alerts.fingerprint("EST-03") == a
