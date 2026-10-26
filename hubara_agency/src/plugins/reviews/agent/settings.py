"""Configuración de las reseñas tras la entrega (entorno ← SSM ← Terraform).

Nace APAGADA (`REVIEW_REQUESTS_MODE=off`): el operador la enciende primero para
los números de prueba (`test` + `REVIEW_TEST_SESSIONS`) y después para todos
(`on`). Toda clave está declarada en Terraform (regla del operador: nada de
parámetros creados a mano).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from src.plugins.reviews.domain.planning import ReviewTiming
from src.sdk.runtime import is_placeholder

_MODES = ("off", "test", "on")

#: Lo que se lee del entorno y Terraform declara en `modules/review-config`
#: (config de producción en git) — guard: tests/infra/test_reviews_terraform.py.
CONFIG_ENV_KEYS = (
    "REVIEW_REQUESTS_MODE",
    "REVIEW_TEST_SESSIONS",
    "REVIEW_REQUEST_MIN_DELAY_MINUTES",
    "REVIEW_REQUEST_WINDOW_MARGIN_MINUTES",
    "REVIEW_REQUEST_TEMPLATE_AFTER_MINUTES",
    "REVIEW_REQUEST_GIVE_UP_MINUTES",
    "REVIEW_REQUEST_POLL_MINUTES",
    "REVIEW_COUPON_CODE",
    "REVIEW_COUPON_PHOTO_CODE",
    "REVIEW_COUPON_DAYS",
    "GOOGLE_REVIEW_URL",
)
#: Claves por tenant con valor fuera de git (`secret_keys`): el flow del
#: formulario lo publica el CLI de provisioning en el WABA del tenant.
SECRET_ENV_KEYS = ("META_FLOW_ID_REVIEW",)
_MINUTE_MS = 60_000
#: El botón «Opinar en Google» va directo a la ficha de Google (premortem P12:
#: no depende de que la web haya publicado su redirección).
DEFAULT_GOOGLE_REVIEW_URL = "https://g.page/r/CdsqIGl0gWaWECE/review"


def _session_of(raw: str) -> str:
    """El número de prueba como lo escriba el operador («+57 300 …»,
    «57300…», «wa_57300…») → la conversación `wa_<dígitos>`. Un id de Meta de
    cliente sin teléfono (`wa_CO…`) queda igual."""
    text = raw.strip()
    if text.startswith("wa_"):
        return text
    digits = re.sub(r"\D", "", text)
    return f"wa_{digits}" if digits else ""


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        value = int(str(env.get(key, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class ReviewSettings:
    #: off | test (solo `test_sessions`) | on (todos).
    mode: str
    test_sessions: frozenset[str]
    #: Flow `review_v1` publicado en el WABA (`META_FLOW_ID_REVIEW`).
    flow_id: str | None
    #: Destino del botón «Opinar en Google».
    google_url: str
    coupon_code_5: str
    coupon_code_10: str
    coupon_days: int
    timing: ReviewTiming

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ReviewSettings:
        env = os.environ if env is None else env
        mode = str(env.get("REVIEW_REQUESTS_MODE", "off")).strip().lower()
        flow_id = str(env.get("META_FLOW_ID_REVIEW", "")).strip()
        return cls(
            mode=mode if mode in _MODES else "off",
            test_sessions=frozenset(
                session
                for raw in str(env.get("REVIEW_TEST_SESSIONS", "")).split(",")
                if not is_placeholder(raw.strip()) and (session := _session_of(raw))
            ),
            flow_id=None if is_placeholder(flow_id) else flow_id,
            google_url=str(env.get("GOOGLE_REVIEW_URL") or "").strip() or DEFAULT_GOOGLE_REVIEW_URL,
            coupon_code_5=str(env.get("REVIEW_COUPON_CODE") or "GRACIAS5").strip().upper(),
            coupon_code_10=str(env.get("REVIEW_COUPON_PHOTO_CODE") or "GRACIAS10").strip().upper(),
            coupon_days=_int(env, "REVIEW_COUPON_DAYS", 60),
            timing=ReviewTiming(
                min_delay_ms=_int(env, "REVIEW_REQUEST_MIN_DELAY_MINUTES", 120) * _MINUTE_MS,
                window_margin_ms=_int(env, "REVIEW_REQUEST_WINDOW_MARGIN_MINUTES", 60) * _MINUTE_MS,
                template_after_ms=_int(env, "REVIEW_REQUEST_TEMPLATE_AFTER_MINUTES", 1200) * _MINUTE_MS,
                give_up_after_ms=_int(env, "REVIEW_REQUEST_GIVE_UP_MINUTES", 2880) * _MINUTE_MS,
                poll_ms=_int(env, "REVIEW_REQUEST_POLL_MINUTES", 30) * _MINUTE_MS,
            ),
        )

    def allows(self, session_id: str) -> bool:
        """¿Este cliente recibe la invitación con el modo actual?"""
        if self.mode == "on":
            return True
        return self.mode == "test" and session_id in self.test_sessions

    def describe(self) -> str:
        """Una línea para el arranque del worker: el modo y qué falta."""
        parts = [f"modo {self.mode}"]
        if self.mode == "test":
            n = len(self.test_sessions)
            parts.append(f"{n} número{'s' if n != 1 else ''} de prueba")
        if self.mode != "off" and not self.flow_id:
            parts.append("falta META_FLOW_ID_REVIEW (no se invita a nadie)")
        if self.mode == "test" and not self.test_sessions:
            parts.append("falta REVIEW_TEST_SESSIONS (no se invita a nadie)")
        parts.append(f"cupones {self.coupon_code_5}/{self.coupon_code_10} por {self.coupon_days} días")
        return " · ".join(parts)

    def coupon_code_for(self, percent: int) -> str:
        return self.coupon_code_10 if percent >= 10 else self.coupon_code_5
