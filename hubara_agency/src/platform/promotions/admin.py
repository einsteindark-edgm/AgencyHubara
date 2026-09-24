"""Comandos de cupones hacia Medusa — la central de Marketing ESCRIBE acá.

Separado del `PromotionsPort` (lecturas, cache 60 s, lo usa el bot): este
puerto solo lo usa la API de la central. Toda lectura es FRESCA (sin cache)
y cada escritura avisa (`on_change`) para que el `PromotionsPort` del mismo
proceso limpie su cache.

`InMemoryMedusaPromotions` imita los endpoints de promociones/campañas de
Medusa 2.12.5 (código único, campaña en línea, reglas con id, 404/400 con el
mismo sobre de error); `FakePromotionsAdmin` es el adapter real sobre ese
simulador — el doble oficial para tests de plugins.
"""
from __future__ import annotations

import copy
import itertools
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from src.platform.medusa.client import MedusaAPIError
from src.platform.promotions.coupon import (
    PRODUCT_RULE_ATTR,
    CouponSpec,
    CouponView,
    coupon_to_medusa_payload,
    coupon_view_from_medusa,
    day_start_utc,
)
from src.platform.promotions.port import PromotionsUnavailableError

log = logging.getLogger(__name__)


class CouponNotFoundError(LookupError):
    """La promoción no existe en Medusa."""


class CouponCodeTakenError(ValueError):
    """Ya existe una promoción (o campaña) con ese código."""


class CouponRejectedError(ValueError):
    """Medusa rechazó los datos; `message` dice por qué."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CouponNotManageableError(PermissionError):
    """La promoción no la puede editar la central (D7); `reason` dice por qué."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CouponDeleteRefusedError(PermissionError):
    """Solo se borra un borrador (sin ventas): lo demás se pausa."""


class CouponPartialUpdateError(RuntimeError):
    """Una edición en varios pasos falló a mitad: `view` es lo que quedó en
    Medusa (releído) y `step` el paso que falló. Reintentar es seguro: cada
    paso fija valores absolutos."""

    def __init__(self, step: str, message: str, view: CouponView | None) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message
        self.view = view


@runtime_checkable
class PromotionsAdminPort(Protocol):
    async def list_coupons(self) -> list[CouponView]: ...

    async def get_coupon(self, promotion_id: str) -> CouponView: ...

    async def create_coupon(self, spec: CouponSpec) -> CouponView: ...

    async def update_coupon(self, promotion_id: str, spec: CouponSpec) -> CouponView: ...

    async def set_status(self, promotion_id: str, status: str) -> CouponView: ...

    async def delete_coupon(self, promotion_id: str) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MedusaPromotionsAdmin:
    """Adapter real: `HttpMedusaClient` (o cualquier cliente con la misma
    forma, como `InMemoryMedusaPromotions`)."""

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], datetime] = _utcnow,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._client = client
        self._clock = clock
        self._on_change = on_change

    def _view(self, raw: dict[str, Any]) -> CouponView:
        return coupon_view_from_medusa(raw, now=self._clock())

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    async def list_coupons(self) -> list[CouponView]:
        raw_list = await self._call(self._client.list_promotions())
        return [self._view(r) for r in raw_list if isinstance(r, dict) and r.get("code")]

    async def get_coupon(self, promotion_id: str) -> CouponView:
        return self._view(await self._raw(promotion_id))

    async def create_coupon(self, spec: CouponSpec) -> CouponView:
        # Medusa distingue mayúsculas; el bot no (compara en mayúsculas). Un
        # "amor26" viejo haría ambiguo el "AMOR26" nuevo.
        existing = await self._call(self._client.list_promotions())
        if any(str(p.get("code") or "").strip().upper() == spec.code for p in existing):
            raise CouponCodeTakenError(f"Ya existe una promoción con el código {spec.code}.")
        created = await self._call(self._client.create_promotion(coupon_to_medusa_payload(spec)))
        self._changed()
        return await self._reread(created)

    async def update_coupon(self, promotion_id: str, spec: CouponSpec) -> CouponView:
        """Lleva la promoción a `spec` tocando SOLO lo que cambió.

        Hasta 3 pasos (promoción, regla de productos, campaña), cada uno con
        valores absolutos: reintentar es seguro. Si uno falla, el error trae
        lo que quedó en Medusa (releído) y el paso que falló.
        """
        raw = await self._manageable_raw(promotion_id)
        current = self._view(raw)
        if spec.code != current.code and current.status != "draft":
            raise CouponRejectedError(
                "El código solo se cambia mientras el cupón está en borrador."
            )
        steps = _update_steps(raw, current, spec)
        try:
            for step, call in steps:
                await self._step(promotion_id, step, call(self._client))
        finally:
            if steps:
                self._changed()
        return self._view(await self._raw(promotion_id))

    async def set_status(self, promotion_id: str, status: str) -> CouponView:
        if status not in ("active", "inactive"):
            raise CouponRejectedError("El estado va como activo o pausado.")
        before = await self._manageable_raw(promotion_id)
        updated = await self._call(self._client.update_promotion(promotion_id, {"status": status}))
        self._changed()
        return await self._reread({**before, **updated, "status": status})

    async def delete_coupon(self, promotion_id: str) -> None:
        """Borra promoción + campaña. SOLO un borrador: lo que ya se pudo usar
        se pausa (las ventas del cupón dependen de que exista)."""
        raw = await self._manageable_raw(promotion_id)
        if str(raw.get("status")) != "draft":
            raise CouponDeleteRefusedError(
                "Solo se borra un cupón en borrador; este se puede pausar."
            )
        await self._call(self._client.delete_promotion(promotion_id))
        self._changed()
        campaign_id = (raw.get("campaign") or {}).get("id") or raw.get("campaign_id")
        if campaign_id:
            try:
                await self._call(self._client.delete_campaign(str(campaign_id)))
            except (PromotionsUnavailableError, CouponNotFoundError, CouponRejectedError) as exc:
                # La promoción ya no existe (el cupón quedó borrado); la
                # campaña huérfana queda en Medusa y hay que borrarla a mano
                # (su identificador bloquea volver a crear el mismo código).
                log.warning("cupón %s borrado; su campaña %s quedó: %s", promotion_id, campaign_id, exc)

    async def raw_promotion(self, promotion_id: str) -> dict[str, Any]:
        """La promoción tal cual la devuelve Medusa (diagnóstico / tests)."""
        return await self._raw(promotion_id)

    async def _reread(self, written: dict[str, Any]) -> CouponView:
        """La promoción tal como quedó; si releer falla, lo que devolvió la
        escritura (el cambio YA está hecho en Medusa: no decir lo contrario)."""
        try:
            return self._view(await self._raw(str(written["id"])))
        except PromotionsUnavailableError:
            log.warning("promoción %s escrita pero no pude releerla", written.get("id"))
            return self._view(written)

    async def _manageable_raw(self, promotion_id: str) -> dict[str, Any]:
        raw = await self._raw(promotion_id)
        view = self._view(raw)
        if not view.manageable:
            raise CouponNotManageableError(view.unmanageable_reason or "")
        return raw

    async def _step(self, promotion_id: str, step: str, awaitable: Any) -> None:
        try:
            await self._call(awaitable)
        except (
            PromotionsUnavailableError,
            CouponRejectedError,
            CouponNotFoundError,
            CouponCodeTakenError,
        ) as exc:
            try:
                view = self._view(await self._raw(promotion_id))
            except Exception:  # noqa: BLE001 — releer es best-effort
                view = None
            raise CouponPartialUpdateError(step, str(exc), view) from exc

    async def _raw(self, promotion_id: str) -> dict[str, Any]:
        return await self._call(self._client.get_promotion(promotion_id))

    async def _call(self, awaitable: Any) -> Any:
        """Traduce los errores del vendor a errores de dominio."""
        try:
            return await awaitable
        except MedusaAPIError as exc:
            raise _domain_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 — timeouts/red no cruzan el port
            raise PromotionsUnavailableError(str(exc)) from exc


def _update_steps(
    raw: dict[str, Any], current: CouponView, spec: CouponSpec
) -> list[tuple[str, Callable[[Any], Any]]]:
    """Los pasos (nombre, llamada al cliente) para llevar `current` a `spec`."""
    promotion_id = current.promotion_id
    steps: list[tuple[str, Callable[[Any], Any]]] = []

    promo_patch: dict[str, Any] = {}
    if spec.code != current.code:
        promo_patch["code"] = spec.code
    if spec.percentage != current.percentage:
        promo_patch["application_method"] = {"value": spec.percentage}
    if promo_patch:
        steps.append(("promotion", lambda c: c.update_promotion(promotion_id, promo_patch)))

    wanted = tuple(spec.products or ())
    if wanted != tuple(current.products or ()):
        rule = next(
            (
                r
                for r in (raw.get("application_method") or {}).get("target_rules") or []
                if isinstance(r, dict) and r.get("attribute") == PRODUCT_RULE_ATTR
            ),
            None,
        )
        create: list[dict[str, Any]] = []
        update: list[dict[str, Any]] = []
        delete: list[str] = []
        if rule is None:
            create.append({"attribute": PRODUCT_RULE_ATTR, "operator": "in", "values": list(wanted)})
        elif wanted:
            update.append({"id": rule["id"], "operator": "in", "values": list(wanted)})
        else:
            delete.append(str(rule["id"]))
        steps.append(
            (
                "products",
                lambda c: c.batch_promotion_target_rules(
                    promotion_id, create=create, update=update, delete=delete
                ),
            )
        )

    campaign_patch: dict[str, Any] = {}
    if spec.campaign_name != current.campaign_name:
        campaign_patch["name"] = spec.campaign_name
    if spec.code != current.code:
        campaign_patch["campaign_identifier"] = spec.code
    if spec.starts_on != current.starts_on:
        campaign_patch["starts_at"] = day_start_utc(spec.starts_on)
    if spec.ends_on != current.ends_on:
        campaign_patch["ends_at"] = day_start_utc(spec.ends_on + timedelta(days=1))
    if campaign_patch and current.campaign_id:
        campaign_id = current.campaign_id
        steps.append(("campaign", lambda c: c.update_campaign(campaign_id, campaign_patch)))
    return steps


def _error_message(exc: MedusaAPIError) -> str:
    try:
        body = json.loads(exc.body)
    except (TypeError, ValueError):
        return exc.body[:300]
    return str(body.get("message") or exc.body)[:300] if isinstance(body, dict) else exc.body[:300]


def _domain_error(exc: MedusaAPIError) -> Exception:
    message = _error_message(exc)
    if exc.status_code == 404:
        return CouponNotFoundError(message)
    if exc.status_code >= 500 or exc.status_code in (401, 403, 429):
        return PromotionsUnavailableError(message)
    if "already exists" in message:
        return CouponCodeTakenError(message)
    return CouponRejectedError(message)


class UnavailablePromotionsAdmin:
    """Sin Medusa configurado: la central no puede leer ni escribir."""

    def _down(self) -> PromotionsUnavailableError:
        return PromotionsUnavailableError("Medusa no está configurado en este deployment")

    async def list_coupons(self) -> list[CouponView]:
        raise self._down()

    async def get_coupon(self, promotion_id: str) -> CouponView:
        raise self._down()

    async def create_coupon(self, spec: CouponSpec) -> CouponView:
        raise self._down()

    async def update_coupon(self, promotion_id: str, spec: CouponSpec) -> CouponView:
        raise self._down()

    async def set_status(self, promotion_id: str, status: str) -> CouponView:
        raise self._down()

    async def delete_coupon(self, promotion_id: str) -> None:
        raise self._down()


# ---------------------------------------------------------------------------
# Doble oficial: Medusa 2.12.5 en memoria (promociones + campañas).
# ---------------------------------------------------------------------------


def _error(status: int, path: str, kind: str, message: str) -> MedusaAPIError:
    return MedusaAPIError(status, path, json.dumps({"type": kind, "message": message}))


class InMemoryMedusaPromotions:
    """Los endpoints de promociones y campañas de Medusa, en memoria.

    Reproduce lo que importa del vendor: `code` único (entre no borradas),
    `campaign_identifier` único, campaña en línea al crear, reglas y valores
    con id, 404 al pedir una que no existe y el mismo sobre de error.
    """

    def __init__(self, promotions: list[dict[str, Any]] | None = None) -> None:
        self._promotions: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.writes: list[tuple[str, str]] = []
        self._orphan_campaigns: dict[str, dict[str, Any]] = {}
        for raw in promotions or []:
            self._promotions[str(raw["id"])] = copy.deepcopy(raw)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids):04d}"

    def _campaigns(self) -> list[dict[str, Any]]:
        linked = [p["campaign"] for p in self._promotions.values() if isinstance(p.get("campaign"), dict)]
        return linked + list(self._orphan_campaigns.values())

    def _rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        values = rule.get("values")
        values = [values] if isinstance(values, str) else list(values or [])
        return {
            "id": self._new_id("prorul"),
            "attribute": rule["attribute"],
            "operator": rule["operator"],
            "values": [{"id": self._new_id("prorulval"), "value": v} for v in values],
        }

    async def list_promotions(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(p) for p in self._promotions.values()]

    async def get_promotion(self, promotion_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._get(promotion_id))

    async def create_promotion(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = "/admin/promotions"
        self.writes.append(("POST", path))
        code = payload["code"]
        if any(p["code"] == code for p in self._promotions.values()):
            raise _error(400, path, "invalid_data", f"Promotion with code: {code}, already exists.")
        method = dict(payload["application_method"])
        campaign_in = payload.get("campaign")
        campaign = None
        if campaign_in:
            ident = campaign_in["campaign_identifier"]
            if any(c.get("campaign_identifier") == ident for c in self._campaigns()):
                raise _error(
                    400, path, "invalid_data",
                    f"Campaign with campaign_identifier: {ident}, already exists.",
                )
            campaign = {"id": self._new_id("procamp"), "budget": None, **campaign_in}
        promo = {
            "id": self._new_id("promo"),
            "code": code,
            "type": payload.get("type", "standard"),
            "is_automatic": payload.get("is_automatic", False),
            "status": payload.get("status", "draft"),
            "campaign_id": campaign["id"] if campaign else None,
            "campaign": campaign,
            "rules": [self._rule(r) for r in payload.get("rules") or []],
            "application_method": {
                "id": self._new_id("proappmet"),
                "max_quantity": None,
                "currency_code": None,
                **{k: v for k, v in method.items() if k != "target_rules"},
                "target_rules": [self._rule(r) for r in method.get("target_rules") or []],
            },
        }
        self._promotions[promo["id"]] = promo
        return copy.deepcopy(promo)


    async def update_promotion(self, promotion_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        promo = self._get(promotion_id)
        self.writes.append(("POST", f"/admin/promotions/{promotion_id}"))
        code = payload.get("code")
        if code is not None and any(
            p["code"] == code and pid != promotion_id for pid, p in self._promotions.items()
        ):
            raise _error(
                400, f"/admin/promotions/{promotion_id}", "invalid_data",
                f"Promotion with code: {code}, already exists.",
            )
        for key in ("code", "status", "is_automatic", "type"):
            if key in payload:
                promo[key] = payload[key]
        if isinstance(payload.get("application_method"), dict):
            promo["application_method"].update(payload["application_method"])
        return copy.deepcopy(promo)

    async def batch_promotion_target_rules(
        self,
        promotion_id: str,
        *,
        create: list[dict[str, Any]] | None = None,
        update: list[dict[str, Any]] | None = None,
        delete: list[str] | None = None,
    ) -> dict[str, Any]:
        promo = self._get(promotion_id)
        self.writes.append(("POST", f"/admin/promotions/{promotion_id}/target-rules/batch"))
        rules = promo["application_method"]["target_rules"]
        gone = set(delete or [])
        rules[:] = [r for r in rules if r["id"] not in gone]
        for patch in update or []:
            rule = next(r for r in rules if r["id"] == patch["id"])
            fresh = self._rule({**rule, **{k: v for k, v in patch.items() if k != "id"}})
            rule.update(operator=fresh["operator"], attribute=fresh["attribute"], values=fresh["values"])
        created = [self._rule(r) for r in create or []]
        rules.extend(created)
        return {
            "created": copy.deepcopy(created),
            "updated": copy.deepcopy([r for r in rules if r["id"] in {u["id"] for u in update or []}]),
            "deleted": {"ids": sorted(gone), "object": "promotion-rule", "deleted": True},
        }

    async def update_campaign(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = f"/admin/campaigns/{campaign_id}"
        campaign = next((c for c in self._campaigns() if c.get("id") == campaign_id), None)
        if campaign is None:
            raise _error(404, path, "not_found", f"Campaign with id: {campaign_id} was not found")
        ident = payload.get("campaign_identifier")
        if ident is not None and any(
            c.get("campaign_identifier") == ident and c.get("id") != campaign_id for c in self._campaigns()
        ):
            raise _error(
                400, path, "invalid_data", f"Campaign with campaign_identifier: {ident}, already exists."
            )
        self.writes.append(("POST", path))
        campaign.update(payload)
        return copy.deepcopy(campaign)

    async def delete_promotion(self, promotion_id: str) -> dict[str, Any]:
        self._get(promotion_id)
        self.writes.append(("DELETE", f"/admin/promotions/{promotion_id}"))
        promo = self._promotions.pop(promotion_id)
        # La campaña sobrevive a la promoción (en Medusa son entidades aparte).
        if isinstance(promo.get("campaign"), dict):
            self._orphan_campaigns[promo["campaign"]["id"]] = promo["campaign"]
        return {"id": promotion_id, "object": "promotion", "deleted": True}

    async def delete_campaign(self, campaign_id: str) -> dict[str, Any]:
        self.writes.append(("DELETE", f"/admin/campaigns/{campaign_id}"))
        self._orphan_campaigns.pop(campaign_id, None)
        for promo in self._promotions.values():
            if promo.get("campaign_id") == campaign_id:
                promo["campaign_id"] = None
                promo["campaign"] = None
        return {"id": campaign_id, "object": "campaign", "deleted": True}

    def _get(self, promotion_id: str) -> dict[str, Any]:
        promo = self._promotions.get(promotion_id)
        if promo is None:
            path = f"/admin/promotions/{promotion_id}"
            raise _error(404, path, "not_found", f"Promotion with id: {promotion_id} was not found")
        return promo


class FakePromotionsAdmin(MedusaPromotionsAdmin):
    """Doble oficial (P-27): el adapter real sobre Medusa en memoria.

    `medusa.writes` registra cada escritura (método, ruta) para asertar que
    una operación NO escribió."""

    def __init__(
        self,
        promotions: list[dict[str, Any]] | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.medusa = InMemoryMedusaPromotions(promotions)
        super().__init__(self.medusa, clock=clock, on_change=on_change)


__all__ = [
    "CouponCodeTakenError",
    "CouponDeleteRefusedError",
    "CouponNotFoundError",
    "CouponNotManageableError",
    "CouponPartialUpdateError",
    "CouponRejectedError",
    "FakePromotionsAdmin",
    "InMemoryMedusaPromotions",
    "MedusaPromotionsAdmin",
    "PromotionsAdminPort",
    "UnavailablePromotionsAdmin",
]
