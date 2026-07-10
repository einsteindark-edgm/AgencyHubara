"""Tests de helpers puros de la ventana de servicio WhatsApp.

Ver `src/platform/whatsapp/window.py` y HU-WA24H-001 §3.3.
"""
from __future__ import annotations


from src.platform.whatsapp.window import (
    CTWA_WINDOW_MS,
    SERVICE_WINDOW_MS,
    WATCHDOG_PRE_EXPIRY_MS,
    compute_ctwa_window_expiry,
    compute_service_window_expiry,
    is_in_ctwa_window,
    is_in_service_window,
    is_message_billable,
    is_service_window_closed,
    watchdog_fire_at,
)


# Anchors de tiempo (epoch ms). Elegidos para ser legibles en debug.
NOW_MS = 1_716_700_000_000
ONE_HOUR_MS = 60 * 60 * 1000
ONE_DAY_MS = 24 * ONE_HOUR_MS


# =============================================================================
# Constantes
# =============================================================================


class TestConstants:
    def test_service_window_is_24_hours(self):
        assert SERVICE_WINDOW_MS == 24 * 60 * 60 * 1000

    def test_ctwa_window_is_72_hours(self):
        assert CTWA_WINDOW_MS == 72 * 60 * 60 * 1000

    def test_watchdog_fires_30_min_before_expiry(self):
        assert WATCHDOG_PRE_EXPIRY_MS == 30 * 60 * 1000


# =============================================================================
# compute_service_window_expiry
# =============================================================================


class TestComputeServiceWindowExpiry:
    def test_exact_24h_offset(self):
        assert compute_service_window_expiry(NOW_MS) == NOW_MS + ONE_DAY_MS


# =============================================================================
# compute_ctwa_window_expiry
# =============================================================================


class TestComputeCtwaWindowExpiry:
    def test_exact_72h_offset(self):
        assert compute_ctwa_window_expiry(NOW_MS) == NOW_MS + (3 * ONE_DAY_MS)


# =============================================================================
# is_in_service_window
# =============================================================================


class TestIsInServiceWindow:
    def test_returns_true_when_inside(self):
        metadata = {"service_window_expires_at_ms": NOW_MS + ONE_HOUR_MS}
        assert is_in_service_window(NOW_MS, metadata) is True

    def test_returns_false_when_past_expiry(self):
        metadata = {"service_window_expires_at_ms": NOW_MS - ONE_HOUR_MS}
        assert is_in_service_window(NOW_MS, metadata) is False

    def test_returns_false_at_exact_expiry(self):
        # Edge: now == exp. Decisión: NO está dentro (ya expiró).
        metadata = {"service_window_expires_at_ms": NOW_MS}
        assert is_in_service_window(NOW_MS, metadata) is False

    def test_returns_false_when_field_missing(self):
        assert is_in_service_window(NOW_MS, {}) is False

    def test_returns_false_when_field_none(self):
        assert is_in_service_window(NOW_MS, {"service_window_expires_at_ms": None}) is False

    def test_returns_false_when_field_non_int(self):
        # Defensa contra metadata corrupta (e.g., string serializado mal).
        metadata = {"service_window_expires_at_ms": "1716700000000"}
        assert is_in_service_window(NOW_MS, metadata) is False


# =============================================================================
# is_in_ctwa_window
# =============================================================================


class TestIsInCtwaWindow:
    def test_returns_true_when_inside(self):
        metadata = {"ctwa_window_expires_at_ms": NOW_MS + ONE_HOUR_MS}
        assert is_in_ctwa_window(NOW_MS, metadata) is True

    def test_returns_false_when_past_expiry(self):
        metadata = {"ctwa_window_expires_at_ms": NOW_MS - ONE_HOUR_MS}
        assert is_in_ctwa_window(NOW_MS, metadata) is False

    def test_returns_false_when_field_missing(self):
        assert is_in_ctwa_window(NOW_MS, {}) is False

    def test_ctwa_independent_from_service_window(self):
        # Servicio expiró, pero CTWA sigue. is_in_ctwa_window True.
        metadata = {
            "service_window_expires_at_ms": NOW_MS - ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": NOW_MS + ONE_HOUR_MS,
        }
        assert is_in_service_window(NOW_MS, metadata) is False
        assert is_in_ctwa_window(NOW_MS, metadata) is True


# =============================================================================
# is_message_billable
# =============================================================================


class TestIsMessageBillable:
    def test_inside_service_window_not_billable(self):
        metadata = {"service_window_expires_at_ms": NOW_MS + ONE_HOUR_MS}
        assert is_message_billable(NOW_MS, metadata) is False

    def test_inside_ctwa_window_not_billable(self):
        metadata = {"ctwa_window_expires_at_ms": NOW_MS + ONE_HOUR_MS}
        assert is_message_billable(NOW_MS, metadata) is False

    def test_both_windows_open_not_billable(self):
        metadata = {
            "service_window_expires_at_ms": NOW_MS + ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": NOW_MS + ONE_HOUR_MS,
        }
        assert is_message_billable(NOW_MS, metadata) is False

    def test_both_windows_closed_billable(self):
        metadata = {
            "service_window_expires_at_ms": NOW_MS - ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": NOW_MS - ONE_HOUR_MS,
        }
        assert is_message_billable(NOW_MS, metadata) is True

    def test_no_metadata_at_all_billable(self):
        # Nunca hubo inbound — cualquier outbound es billable.
        assert is_message_billable(NOW_MS, {}) is True


# =============================================================================
# watchdog_fire_at
# =============================================================================


class TestWatchdogFireAt:
    def test_returns_30min_before_expiry(self):
        exp = NOW_MS + ONE_DAY_MS
        metadata = {"service_window_expires_at_ms": exp}
        assert watchdog_fire_at(metadata) == exp - WATCHDOG_PRE_EXPIRY_MS

    def test_respects_pre_expiry_minutes_env(self, monkeypatch):
        # El lead time del watchdog debe ser configurable por env
        # (WATCHDOG_PRE_EXPIRY_MINUTES) — así se ajusta vía ConfigMap de AWS
        # sin redeploy, en vez de quedar fijo en 30 min.
        monkeypatch.setenv("WATCHDOG_PRE_EXPIRY_MINUTES", "10")
        exp = NOW_MS + ONE_DAY_MS
        metadata = {"service_window_expires_at_ms": exp}
        assert watchdog_fire_at(metadata) == exp - 10 * 60 * 1000

    def test_returns_none_when_field_missing(self):
        assert watchdog_fire_at({}) is None

    # (los tests de is_service_window_closed viven en TestIsServiceWindowClosed)

    def test_returns_none_when_field_none(self):
        assert watchdog_fire_at({"service_window_expires_at_ms": None}) is None

    def test_returns_none_when_field_non_int(self):
        assert watchdog_fire_at({"service_window_expires_at_ms": "abc"}) is None

    def test_fire_at_can_be_in_past_if_window_already_closing_soon(self):
        # Si el llamado llega cuando ya pasó el fire_at (e.g., webhook
        # demorado), retorna el valor pasado igual. El caller decide qué
        # hacer (típicamente: NO programar watchdog si delta_ms <= 0).
        exp = NOW_MS + (10 * 60 * 1000)  # 10 min al cierre
        metadata = {"service_window_expires_at_ms": exp}
        result = watchdog_fire_at(metadata)
        assert result is not None
        assert result < NOW_MS  # fire_at quedó en el pasado


# =============================================================================
# is_service_window_closed — guard del operador (fail-open cuando se desconoce)
# =============================================================================


class TestIsServiceWindowClosed:
    """`is_service_window_closed` es el guard que usa la ruta del operador
    humano. Distinto de `not is_in_service_window`: solo devuelve True cuando
    SABEMOS que la ventana cerró (campo presente y vencido). Si el campo no
    existe (dev, seed data, sesión sin ingest de ventana) devuelve False —
    fail-open, para no bloquear al operador por metadata incompleta."""

    def test_closed_when_expiry_passed(self):
        exp = NOW_MS - ONE_HOUR_MS  # venció hace 1h
        assert is_service_window_closed(NOW_MS, {"service_window_expires_at_ms": exp}) is True

    def test_open_when_expiry_future(self):
        exp = NOW_MS + ONE_HOUR_MS
        assert is_service_window_closed(NOW_MS, {"service_window_expires_at_ms": exp}) is False

    def test_not_closed_when_field_missing(self):
        # Desconocido → NO bloquea (fail-open). Preserva el comportamiento
        # actual del endpoint del operador en dev/seed.
        assert is_service_window_closed(NOW_MS, {}) is False

    def test_not_closed_when_field_none_or_non_int(self):
        assert is_service_window_closed(NOW_MS, {"service_window_expires_at_ms": None}) is False
        assert is_service_window_closed(NOW_MS, {"service_window_expires_at_ms": "abc"}) is False
