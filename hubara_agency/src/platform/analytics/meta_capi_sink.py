"""Sink Meta Conversions API — atribución server-side de CTWA.

Endpoint: `POST https://graph.facebook.com/v23.0/{pixel_id}/events?access_token=...`

Mapeo:

* `referral.ctwa_referral_captured` → `event_name="Lead"` con
  `custom_data.ctwa_clid` (Meta espera este campo para attribution).
* `conversion.AddToCart` → `event_name="AddToCart"` con `value + currency`.
* `conversion.InitiateCheckout` → `event_name="InitiateCheckout"`.
* `conversion.Purchase` → `event_name="Purchase"` con `value + currency`.
* Otros: skip por default — sólo emitimos lo que Meta soporta para
  optimización de Advantage+ Shopping campaigns.

Identidad del usuario (CAPI espera user_data hash-ed):
* `phone` → SHA256 lowercase del número.
* `external_id` → SHA256 del session_id.

Datos del click:
* `action_source` = `"business_messaging"`.
* `messaging_channel` = `"whatsapp"`.
* `custom_data.ctwa_clid` requerido.

Acción si Meta CAPI rechaza: loguear, NO retry — la pérdida ocasional de
atribución es aceptable, no es transaccional. El sink filesystem ya tiene
la copia local para post-mortem.

Status: stub funcional. Producción requiere el `META_PIXEL_ID` +
`META_CAPI_ACCESS_TOKEN` + opcional `META_CAPI_TEST_EVENT_CODE` (dev).
"""
from __future__ import annotations

import hashlib
import os

import httpx
import structlog

from src.platform.analytics.events import AnalyticsEvent

logger = structlog.get_logger()


# Eventos Meta CAPI soportados (resto se ignoran)
_MAPPED_EVENT_NAMES = {
    "ctwa_referral_captured": "Lead",
    "conversion.Purchase": "Purchase",
    "conversion.AddToCart": "AddToCart",
    "conversion.InitiateCheckout": "InitiateCheckout",
    "conversion.Lead": "Lead",
    "conversion.ViewContent": "ViewContent",
    "conversion.Contact": "Contact",
}


class MetaConversionsAPISink:
    name = "meta_capi"

    def __init__(
        self,
        pixel_id: str,
        access_token: str,
        test_event_code: str | None = None,
        api_version: str = "v23.0",
    ) -> None:
        self._pixel_id = pixel_id
        self._access_token = access_token
        self._test_event_code = test_event_code
        self._endpoint = f"https://graph.facebook.com/{api_version}/{pixel_id}/events"

    @classmethod
    def from_env(cls) -> "MetaConversionsAPISink | None":
        """Construye el sink desde env vars. Si faltan, devuelve None
        (caller arma el bus sin este sink).
        """
        pixel_id = os.getenv("META_PIXEL_ID", "").strip()
        token = os.getenv("META_CAPI_ACCESS_TOKEN", "").strip()
        if not pixel_id or not token:
            return None
        return cls(
            pixel_id=pixel_id,
            access_token=token,
            test_event_code=os.getenv("META_CAPI_TEST_EVENT_CODE") or None,
        )

    async def write(self, event: AnalyticsEvent) -> None:
        meta_event_name = _MAPPED_EVENT_NAMES.get(event.kind)
        if not meta_event_name:
            return  # no es un evento que Meta CAPI consuma

        ctwa_clid = event.correlation.get("ctwa_clid")
        if not ctwa_clid and meta_event_name in {"Purchase", "AddToCart", "InitiateCheckout"}:
            # CAPI acepta estos eventos sin ctwa_clid (atribución difusa)
            # pero loguemos para visibilidad.
            logger.info(
                "meta_capi.event_without_ctwa_clid",
                event_kind=event.kind,
                session=event.correlation.get("session_id"),
            )

        session_id = event.correlation.get("session_id") or ""
        phone = event.correlation.get("phone") or _phone_from_session(session_id)

        user_data: dict = {}
        if phone:
            user_data["ph"] = _sha256(phone)
        if session_id:
            user_data["external_id"] = _sha256(session_id)

        custom_data = {
            "currency": event.payload.get("currency"),
            "value": event.payload.get("value"),
        }
        if ctwa_clid:
            custom_data["ctwa_clid"] = ctwa_clid
        # Limpia None
        custom_data = {k: v for k, v in custom_data.items() if v is not None}

        payload_event = {
            "event_name": meta_event_name,
            "event_time": event.timestamp_ms // 1000,  # Meta espera epoch s
            "event_id": event.event_id,  # dedup
            "action_source": "business_messaging",
            "messaging_channel": "whatsapp",
            "user_data": user_data,
            "custom_data": custom_data,
        }

        body: dict = {"data": [payload_event], "access_token": self._access_token}
        if self._test_event_code:
            body["test_event_code"] = self._test_event_code

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(self._endpoint, json=body)
        except httpx.HTTPError as e:
            logger.warning("meta_capi.transport_error", error=str(e))
            return

        if resp.status_code >= 300:
            logger.warning(
                "meta_capi.bad_response",
                status=resp.status_code,
                body=resp.text[:400],
                event_kind=event.kind,
            )
            return

        logger.info(
            "meta_capi.event_sent",
            event_name=meta_event_name,
            event_id=event.event_id,
            ctwa_clid=ctwa_clid,
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _phone_from_session(session_id: str) -> str | None:
    """Sales convention: session_id = `wa_<phone_number>`. Extraemos el phone."""
    if not session_id.startswith("wa_"):
        return None
    rest = session_id[3:]
    return rest if rest else None
