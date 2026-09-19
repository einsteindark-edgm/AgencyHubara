"""La tarjeta de tarifas vigente se elige POR FECHA, no por un env que nadie setea.

Verificado 2026-09-18: `get_current_rate_card()` defaulteaba a `co_2026q2_v1`
(service = 0) y `WHATSAPP_RATE_CARD_VERSION` NO existe en SSM. El 1-oct-2026
Meta empieza a cobrar los service messages (free-form dentro de la ventana de
24h) y las utility dentro de la ventana — webhook `pricing: {billable: true,
type: "regular", category: "service"}` — y prod habría seguido calculando
costo 0 en silencio hasta que alguien recordara flippear el env.

Además los `effective_from_ms` de AMBAS tarjetas estaban mal (q2 → 2024-06-01,
q4 → 2025-10-01): con selección por fecha el dato deja de ser decorativo.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from src.platform.whatsapp import composition
from src.platform.whatsapp.cost import (
    RATE_CARDS_DIR,
    PricingSnapshot,
    compute_message_cost_micros,
    effective_rate_card_version,
    load_rate_card_from_yaml,
)


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


OCT_1 = _ms("2026-10-01T00:00:00")


@pytest.fixture(autouse=True)
def _no_forced_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)


class TestVersionVigentePorFecha:
    def test_antes_del_1_de_octubre_rige_q2(self):
        assert effective_rate_card_version(OCT_1 - 1) == "co_2026q2_v1"

    def test_desde_el_1_de_octubre_rige_q4(self):
        assert effective_rate_card_version(OCT_1) == "co_2026q4_v1"
        assert effective_rate_card_version(_ms("2027-03-15T12:00:00")) == "co_2026q4_v1"

    def test_antes_de_la_primera_tarjeta_cae_en_la_mas_vieja(self):
        # Defensivo: relojes raros / datos históricos — nunca explota.
        assert effective_rate_card_version(_ms("2020-01-01T00:00:00")) == "co_2026q2_v1"


class TestGetCurrentRateCard:
    def test_sin_env_elige_por_fecha(self):
        assert composition.get_current_rate_card(now_ms=OCT_1 - 1).version == "co_2026q2_v1"
        assert composition.get_current_rate_card(now_ms=OCT_1).version == "co_2026q4_v1"

    def test_el_env_fuerza_una_version(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WHATSAPP_RATE_CARD_VERSION", "co_2026q2_v1")
        assert composition.get_current_rate_card(now_ms=OCT_1 + 1).version == "co_2026q2_v1"

    def test_un_proceso_vivo_cruza_el_1_de_octubre_sin_reiniciar(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Los workers no se reinician a medianoche: el singleton por proceso
        # (`lru_cache(maxsize=1)`) habría dejado la tarjeta vieja hasta el
        # próximo deploy.
        clock = {"now": (OCT_1 - 60_000) / 1000}
        monkeypatch.setattr(composition.time, "time", lambda: clock["now"])
        assert composition.get_current_rate_card().version == "co_2026q2_v1"
        clock["now"] = (OCT_1 + 60_000) / 1000
        assert composition.get_current_rate_card().version == "co_2026q4_v1"


class TestGuardaDelAcantilado:
    @pytest.mark.parametrize(
        "iso", ["2026-10-01T00:00:00", "2026-12-24T18:00:00", "2027-06-01T00:00:00"]
    )
    def test_desde_el_1_de_octubre_service_y_utility_se_cobran(self, iso: str):
        card = composition.get_current_rate_card(now_ms=_ms(iso))
        for category in ("service", "utility"):
            micros = card.rates[category].usd_micros_per_message
            assert micros is not None and micros > 0, (
                f"{card.version}: `{category}` en {micros} a {iso} — desde el "
                "1-oct-2026 Meta lo cobra; un 0 acá es gasto subcontado en silencio"
            )

    def test_service_regular_post_oct_cuesta_lo_que_dice_meta(self):
        # Shape oficial del webhook (doc "non-template messages" de Meta):
        # {"billable": true, "pricing_model": "PMP", "type": "regular",
        #  "category": "service"} → misma tarifa que utility del país.
        card = composition.get_current_rate_card(now_ms=OCT_1)
        snapshot = PricingSnapshot(billable=True, pricing_type="regular", category="service")
        assert compute_message_cost_micros(snapshot, card) == 800
        assert (
            card.rates["service"].usd_micros_per_message
            == card.rates["utility"].usd_micros_per_message
        )


class TestCoherenciaDeLasTarjetas:
    def test_effective_from_coincide_con_el_trimestre_de_la_version(self):
        # `co_2026q4_v1` → 2026-10-01T00:00:00Z. Las dos tarjetas tenían el
        # valor mal (comentario correcto, número equivocado).
        for path in sorted(RATE_CARDS_DIR.glob("*.yaml")):
            match = re.fullmatch(r"[a-z]{2}_(\d{4})q([1-4])_v\d+", path.stem)
            assert match, f"nombre de tarjeta fuera de convención: {path.stem}"
            year, quarter = int(match.group(1)), int(match.group(2))
            expected = _ms(f"{year}-{3 * (quarter - 1) + 1:02d}-01T00:00:00")
            card = load_rate_card_from_yaml(path.stem)
            assert card.effective_from_ms == expected, (
                f"{path.stem}: effective_from_ms={card.effective_from_ms} "
                f"({datetime.fromtimestamp(card.effective_from_ms / 1000, tz=timezone.utc)}) "
                f"≠ inicio del trimestre {expected}"
            )
