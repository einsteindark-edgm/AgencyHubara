"""Comandos de cupones hacia Medusa — la central de Marketing ESCRIBE acá.

Separado del `PromotionsPort` (lecturas, cache 60 s, lo usa el bot): este
puerto solo lo usa la API de la central. Toda lectura es FRESCA (sin cache)
y cada escritura avisa (`on_change`) para que el `PromotionsPort` del mismo
proceso limpie su cache.

Escrituras honestas (L-1): una escritura que NO salió (conexión rechazada)
no cambió nada; una que salió y Medusa no confirmó (timeout, 5xx, conexión
cortada a mitad) PUDO aplicarse. Esa se resuelve releyendo (por id, o por
código al crear) y, si no se puede saber, se dice que es DESCONOCIDO
(`CouponWriteUnconfirmedError`) — nunca "no se hizo ningún cambio".

`InMemoryMedusaPromotions` imita los endpoints de promociones/campañas de
Medusa 2.12.5 (código único, campaña en línea, una campaña que pueden
compartir varias promociones, reglas con id, 404/400 con el mismo sobre de
error); `FakePromotionsAdmin` es el adapter real sobre ese simulador — el
doble oficial para tests de plugins.
"""
from __future__ import annotations

import copy
import itertools
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol, runtime_checkable

import httpx

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
    """Ya existe una promoción (o campaña) con ese código.

    `campaign_identifier`: lo que choca es SOLO el identificador de una
    campaña de Medusa (típicamente la que quedó de un cupón borrado), no el
    código de otra promoción."""

    def __init__(self, message: str = "", *, campaign_identifier: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.campaign_identifier = campaign_identifier


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
    Medusa (releído; None si tampoco se pudo releer), `step` el paso que
    falló y `applied` los que SÍ se aplicaron. Reintentar es seguro: cada
    paso fija valores absolutos."""

    def __init__(
        self, step: str, message: str, view: CouponView | None, *, applied: tuple[str, ...] = ()
    ) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message
        self.view = view
        self.applied = applied


class CouponWriteUnconfirmedError(PromotionsUnavailableError):
    """Medusa no confirmó una escritura que SÍ salió (timeout, 5xx, conexión
    cortada a mitad) y releer no lo resolvió: PUDO haberse aplicado. Quien la
    reciba nunca dice "no se hizo ningún cambio" (L-1: desconocido,
    verificar antes de reintentar)."""

    #: Para quien la ve como `PromotionsUnavailableError` (la API, por el SDK).
    outcome_unknown = True


@dataclass(frozen=True)
class CouponDeletion:
    """Cómo quedó Medusa al borrar un cupón (la promoción YA no existe)."""

    promotion_id: str
    campaign_id: str | None
    campaign_deleted: bool
    #: La campaña quedó en Medusa sin cupones porque no se pudo borrar: su
    #: identificador bloquea volver a crear el mismo código hasta borrarla.
    orphaned_campaign_id: str | None = None


@runtime_checkable
class PromotionsAdminPort(Protocol):
    async def list_coupons(self) -> list[CouponView]: ...

    async def get_coupon(self, promotion_id: str) -> CouponView: ...

    async def create_coupon(self, spec: CouponSpec) -> CouponView: ...

    async def update_coupon(self, promotion_id: str, spec: CouponSpec) -> CouponView: ...

    async def set_status(self, promotion_id: str, status: str) -> CouponView: ...

    async def delete_coupon(self, promotion_id: str) -> CouponDeletion: ...

    async def delete_orphan_campaigns(self, code: str) -> list[str]: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: La escritura ni siquiera salió: seguro que no cambió nada.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
#: Errores de dominio de una lectura/escritura que Medusa sí contestó.
_ANSWERED = (CouponNotFoundError, CouponRejectedError, CouponCodeTakenError)


def _campaign_id(raw: dict[str, Any]) -> str | None:
    campaign = raw.get("campaign")
    cid = (campaign.get("id") if isinstance(campaign, dict) else None) or raw.get("campaign_id")
    return str(cid) if cid else None


def _shared_campaign_ids(promotions: list[dict[str, Any]]) -> set[str]:
    """Campañas de las que cuelga más de una promoción (se arma así en
    Medusa Admin): editar una cambiaría las otras."""
    counts = Counter(cid for p in promotions if (cid := _campaign_id(p)))
    return {cid for cid, n in counts.items() if n > 1}


def _campaign_shared(raw: dict[str, Any], promotions: list[dict[str, Any]]) -> bool:
    """¿OTRA promoción cuelga de la campaña de `raw`?"""
    cid = _campaign_id(raw)
    return cid is not None and any(
        _campaign_id(p) == cid and str(p.get("id")) != str(raw.get("id")) for p in promotions
    )


def _find_code(promotions: list[dict[str, Any]], code: str) -> dict[str, Any] | None:
    """La promoción con ese código, sin distinguir mayúsculas (el bot no las
    distingue: "amor26" y "AMOR26" serían el mismo cupón)."""
    return next(
        (p for p in promotions if str(p.get("code") or "").strip().upper() == code), None
    )


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

    def _view(self, raw: dict[str, Any], *, shared: bool = False) -> CouponView:
        return coupon_view_from_medusa(raw, now=self._clock(), campaign_shared=shared)

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    async def list_coupons(self) -> list[CouponView]:
        raw_list = await self._promotions()
        shared = _shared_campaign_ids(raw_list)
        return [
            self._view(r, shared=_campaign_id(r) in shared) for r in raw_list if r.get("code")
        ]

    async def get_coupon(self, promotion_id: str) -> CouponView:
        raw, promotions = await self._with_siblings(promotion_id)
        return self._view(raw, shared=_campaign_shared(raw, promotions))

    async def create_coupon(self, spec: CouponSpec) -> CouponView:
        # Medusa distingue mayúsculas; el bot no (compara en mayúsculas). Un
        # "amor26" viejo haría ambiguo el "AMOR26" nuevo.
        if _find_code(await self._promotions(), spec.code) is not None:
            raise CouponCodeTakenError(f"Ya existe una promoción con el código {spec.code}.")
        try:
            created = await self._write(self._client.create_promotion(coupon_to_medusa_payload(spec)))
        except CouponWriteUnconfirmedError as exc:
            self._changed()  # pudo haberse creado: el bot no se queda con lo viejo
            found = await self._settle_created(spec.code)
            if found is None:
                log.warning("cupón %s: Medusa no confirmó la creación y no aparece: %s", spec.code, exc)
                raise
            log.warning(
                "cupón %s: Medusa no confirmó la creación, pero quedó creado (%s)",
                spec.code, found.get("id"),
            )
            return self._view(found)
        self._changed()
        return await self._reread(created)

    async def update_coupon(self, promotion_id: str, spec: CouponSpec) -> CouponView:
        """Lleva la promoción a `spec` tocando SOLO lo que cambió.

        Hasta 3 pasos (promoción, regla de productos, campaña), cada uno con
        valores absolutos: reintentar es seguro. Si uno falla sin que se haya
        aplicado ninguno, sale el error original (no cambió nada); si falla a
        mitad, el error trae lo que quedó en Medusa (releído) y lo aplicado.
        """
        raw, promotions = await self._manageable(promotion_id)
        current = self._view(raw)
        if spec.code != current.code:
            if current.status != "draft":
                raise CouponRejectedError(
                    "El código solo se cambia mientras el cupón está en borrador."
                )
            others = [p for p in promotions if str(p.get("id")) != promotion_id]
            if _find_code(others, spec.code) is not None:
                raise CouponCodeTakenError(f"Ya existe una promoción con el código {spec.code}.")
        steps = _update_steps(raw, current, spec)
        applied: list[str] = []
        try:
            for step in steps:
                await self._step(promotion_id, step, spec, applied)
                applied.append(step.name)
        finally:
            if steps:
                self._changed()
        try:
            return self._view(await self._raw(promotion_id))
        except PromotionsUnavailableError as exc:
            # Todos los pasos se aplicaron: lo escrito YA está en Medusa.
            log.warning("cupón %s: editado (%s) pero no pude releerlo: %s", promotion_id, applied, exc)
            return self._view(_expected_raw(raw, steps))

    async def set_status(self, promotion_id: str, status: str) -> CouponView:
        if status not in ("active", "inactive"):
            raise CouponRejectedError("El estado va como activo o pausado.")
        before, _ = await self._manageable(promotion_id)
        try:
            updated = await self._write(
                self._client.update_promotion(promotion_id, {"status": status})
            )
        except CouponWriteUnconfirmedError:
            self._changed()
            fresh = await self._try_raw(promotion_id)
            if fresh is None or str(fresh.get("status")) != status:
                log.warning("cupón %s: Medusa no confirmó el estado %s", promotion_id, status)
                raise
            log.warning("cupón %s: Medusa no confirmó el estado %s, pero quedó", promotion_id, status)
            return self._view(fresh)
        self._changed()
        return await self._reread({**before, **updated, "status": status})

    async def delete_coupon(self, promotion_id: str) -> CouponDeletion:
        """Borra promoción + campaña. SOLO un borrador: lo que ya se pudo usar
        se pausa (las ventas del cupón dependen de que exista). La campaña se
        borra solo si ninguna otra promoción la usa."""
        raw, _ = await self._manageable(promotion_id)
        if str(raw.get("status")) != "draft":
            raise CouponDeleteRefusedError(
                "Solo se borra un cupón en borrador; este se puede pausar."
            )
        try:
            await self._write(self._client.delete_promotion(promotion_id))
        except CouponWriteUnconfirmedError:
            self._changed()
            if not await self._is_gone(promotion_id):
                log.warning("cupón %s: Medusa no confirmó el borrado", promotion_id)
                raise
            log.warning("cupón %s: Medusa no confirmó el borrado, pero ya no existe", promotion_id)
        self._changed()
        campaign_id = _campaign_id(raw)
        if campaign_id is None:
            return CouponDeletion(promotion_id, None, campaign_deleted=False)
        return await self._drop_campaign(promotion_id, campaign_id)

    async def delete_orphan_campaigns(self, code: str) -> list[str]:
        """Borra las campañas SIN promociones cuyo identificador es `code`:
        lo que quedó de un cupón cuyo borrado Medusa aplicó sin confirmar (el
        identificador bloquea volver a crear el código). Una campaña en uso
        jamás se toca. Devuelve los ids borrados."""
        wanted = str(code or "").strip().upper()
        if not wanted:
            return []
        campaigns = await self._call(self._client.list_campaigns())
        used = {_campaign_id(p) for p in await self._promotions()}
        deleted: list[str] = []
        for campaign in campaigns:
            if not isinstance(campaign, dict):
                continue
            cid = str(campaign.get("id") or "")
            ident = str(campaign.get("campaign_identifier") or "").strip().upper()
            if cid and ident == wanted and cid not in used:
                await self._write(self._client.delete_campaign(cid))
                deleted.append(cid)
        if deleted:
            self._changed()
        return deleted

    async def raw_promotion(self, promotion_id: str) -> dict[str, Any]:
        """La promoción tal cual la devuelve Medusa (diagnóstico / tests)."""
        return await self._raw(promotion_id)

    async def _drop_campaign(self, promotion_id: str, campaign_id: str) -> CouponDeletion:
        """La campaña del cupón borrado, si ya nadie la usa: una campaña que
        comparte otra promoción NUNCA se borra. Si no se puede borrar queda
        huérfana y se reporta (su identificador bloquea el código)."""
        try:
            users = [
                p for p in await self._promotions()
                if _campaign_id(p) == campaign_id and str(p.get("id")) != promotion_id
            ]
        except (PromotionsUnavailableError, *_ANSWERED) as exc:
            log.warning(
                "cupón %s borrado; no pude ver si otro usa su campaña %s (queda): %s",
                promotion_id, campaign_id, exc,
            )
            return CouponDeletion(promotion_id, campaign_id, False, orphaned_campaign_id=campaign_id)
        if users:
            log.info("cupón %s borrado; su campaña %s la usan otros cupones: queda", promotion_id, campaign_id)
            return CouponDeletion(promotion_id, campaign_id, campaign_deleted=False)
        try:
            await self._write(self._client.delete_campaign(campaign_id))
        except CouponNotFoundError:
            pass  # ya no estaba
        except (PromotionsUnavailableError, CouponRejectedError, CouponCodeTakenError) as exc:
            log.warning("cupón %s borrado; su campaña %s quedó huérfana: %s", promotion_id, campaign_id, exc)
            return CouponDeletion(promotion_id, campaign_id, False, orphaned_campaign_id=campaign_id)
        return CouponDeletion(promotion_id, campaign_id, campaign_deleted=True)

    async def _reread(self, written: dict[str, Any]) -> CouponView:
        """La promoción tal como quedó; si releer falla, lo que devolvió la
        escritura (el cambio YA está hecho en Medusa: no decir lo contrario)."""
        try:
            return self._view(await self._raw(str(written["id"])))
        except PromotionsUnavailableError:
            log.warning("promoción %s escrita pero no pude releerla", written.get("id"))
            return self._view(written)

    async def _settle_created(self, code: str) -> dict[str, Any] | None:
        """¿Quedó creada? Se busca por código (el POST no devolvió el id)."""
        try:
            return _find_code(await self._promotions(), code)
        except (PromotionsUnavailableError, *_ANSWERED):
            return None

    async def _is_gone(self, promotion_id: str) -> bool:
        """¿Ya no existe? (un 404 al releer). Si no se puede releer: no se sabe."""
        try:
            await self._raw(promotion_id)
        except CouponNotFoundError:
            return True
        except (PromotionsUnavailableError, CouponRejectedError, CouponCodeTakenError):
            return False
        return False

    async def _try_raw(self, promotion_id: str) -> dict[str, Any] | None:
        """Releer para resolver qué quedó; None si tampoco se puede."""
        try:
            return await self._raw(promotion_id)
        except (PromotionsUnavailableError, *_ANSWERED):
            return None

    async def _with_siblings(self, promotion_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """La promoción y TODAS las de Medusa (para saber si su campaña es
        compartida o si un código ya lo usa otra)."""
        raw = await self._raw(promotion_id)
        return raw, await self._promotions()

    async def _manageable(self, promotion_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raw, promotions = await self._with_siblings(promotion_id)
        view = self._view(raw, shared=_campaign_shared(raw, promotions))
        if not view.manageable:
            raise CouponNotManageableError(view.unmanageable_reason or "")
        return raw, promotions

    async def _step(self, promotion_id: str, step: _Step, spec: CouponSpec, applied: list[str]) -> None:
        """Un paso de la edición. Si Medusa no lo confirmó se relee: si quedó
        aplicado se sigue. Si falla sin ningún paso anterior aplicado sale el
        error original (no es "a medias")."""
        fresh: dict[str, Any] | None = None
        try:
            await self._write(step.call(self._client))
            return
        except CouponWriteUnconfirmedError as exc:
            failure: Exception = exc
            fresh = await self._try_raw(promotion_id)
            if fresh is not None and step.name not in {
                s.name for s in _update_steps(fresh, self._view(fresh), spec)
            }:
                log.warning("cupón %s: Medusa no confirmó el paso %s, pero quedó", promotion_id, step.name)
                return
        except (PromotionsUnavailableError, *_ANSWERED) as exc:
            failure = exc
            if applied:
                fresh = await self._try_raw(promotion_id)
        if not applied:
            raise failure
        log.warning(
            "cupón %s: la edición quedó a medias en %s (aplicado: %s): %s",
            promotion_id, step.name, applied, failure,
        )
        view = self._view(fresh) if fresh is not None else None
        raise CouponPartialUpdateError(step.name, str(failure), view, applied=tuple(applied)) from failure

    async def _promotions(self) -> list[dict[str, Any]]:
        return [p for p in await self._call(self._client.list_promotions()) if isinstance(p, dict)]

    async def _raw(self, promotion_id: str) -> dict[str, Any]:
        return await self._call(self._client.get_promotion(promotion_id))

    async def _call(self, awaitable: Any) -> Any:
        """Traduce los errores del vendor a errores de dominio (lecturas)."""
        try:
            return await awaitable
        except MedusaAPIError as exc:
            raise _domain_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 — timeouts/red no cruzan el port
            raise PromotionsUnavailableError(str(exc)) from exc

    async def _write(self, awaitable: Any) -> Any:
        """Una escritura: como `_call`, pero separa "no salió" (no cambió
        nada) de "salió y Medusa no confirmó" (`CouponWriteUnconfirmedError`:
        quien escribió lo resuelve releyendo)."""
        try:
            return await awaitable
        except MedusaAPIError as exc:
            if exc.status_code >= 500:
                raise CouponWriteUnconfirmedError(_error_message(exc)) from exc
            raise _domain_error(exc) from exc
        except _NOT_SENT as exc:
            raise PromotionsUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — timeout / conexión cortada a mitad
            raise CouponWriteUnconfirmedError(str(exc) or type(exc).__name__) from exc


@dataclass(frozen=True)
class _Step:
    """Un paso de una edición: la llamada a Medusa y cómo queda la promoción
    si se aplicó (para devolver lo escrito cuando releer falla)."""

    name: str
    call: Callable[[Any], Any]
    apply: Callable[[dict[str, Any]], None]


def _update_steps(raw: dict[str, Any], current: CouponView, spec: CouponSpec) -> list[_Step]:
    """Los pasos para llevar `current` a `spec` (solo lo que cambió)."""
    promotion_id = current.promotion_id
    steps: list[_Step] = []

    promo_patch: dict[str, Any] = {}
    if spec.code != current.code:
        promo_patch["code"] = spec.code
    if spec.percentage != current.percentage:
        promo_patch["application_method"] = {"value": spec.percentage}
    if promo_patch:

        def apply_promotion(r: dict[str, Any]) -> None:
            if "code" in promo_patch:
                r["code"] = promo_patch["code"]
            if "application_method" in promo_patch:
                r["application_method"] = {**(r.get("application_method") or {}), "value": spec.percentage}

        steps.append(
            _Step("promotion", lambda c: c.update_promotion(promotion_id, promo_patch), apply_promotion)
        )

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

        def apply_products(r: dict[str, Any]) -> None:
            method = dict(r.get("application_method") or {})
            rules = [
                x for x in method.get("target_rules") or []
                if not (isinstance(x, dict) and x.get("attribute") == PRODUCT_RULE_ATTR)
            ]
            if wanted:
                rules.append(
                    {"attribute": PRODUCT_RULE_ATTR, "operator": "in", "values": [{"value": v} for v in wanted]}
                )
            r["application_method"] = {**method, "target_rules": rules}

        steps.append(
            _Step(
                "products",
                lambda c: c.batch_promotion_target_rules(
                    promotion_id, create=create, update=update, delete=delete
                ),
                apply_products,
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

        def apply_campaign(r: dict[str, Any]) -> None:
            r["campaign"] = {**(r.get("campaign") or {}), **campaign_patch}

        steps.append(
            _Step("campaign", lambda c: c.update_campaign(campaign_id, campaign_patch), apply_campaign)
        )
    return steps


def _expected_raw(raw: dict[str, Any], steps: list[_Step]) -> dict[str, Any]:
    """La promoción como quedó si TODOS los pasos se aplicaron (lo escrito)."""
    out = copy.deepcopy(raw)
    for step in steps:
        step.apply(out)
    return out


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
        # "Campaign with campaign_identifier: X, already exists." = solo choca
        # el identificador de una campaña (la de un cupón borrado).
        return CouponCodeTakenError(message, campaign_identifier="campaign_identifier" in message)
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

    async def delete_coupon(self, promotion_id: str) -> CouponDeletion:
        raise self._down()

    async def delete_orphan_campaigns(self, code: str) -> list[str]:
        raise self._down()


# ---------------------------------------------------------------------------
# Doble oficial: Medusa 2.12.5 en memoria (promociones + campañas).
# ---------------------------------------------------------------------------


def _error(status: int, path: str, kind: str, message: str) -> MedusaAPIError:
    return MedusaAPIError(status, path, json.dumps({"type": kind, "message": message}))


class InMemoryMedusaPromotions:
    """Los endpoints de promociones y campañas de Medusa, en memoria.

    Reproduce lo que importa del vendor: `code` único (entre no borradas),
    `campaign_identifier` único, campaña en línea al crear, campañas que son
    entidades APARTE (varias promociones pueden colgar de una, y una campaña
    sobrevive a sus promociones), reglas y valores con id, 404 al pedir una
    que no existe y el mismo sobre de error.
    """

    def __init__(self, promotions: list[dict[str, Any]] | None = None) -> None:
        self._promotions: dict[str, dict[str, Any]] = {}
        #: Campañas por id. Una promoción apunta a una con `campaign_id`.
        self._campaigns: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.writes: list[tuple[str, str]] = []
        for raw in promotions or []:
            self.seed_promotion(raw)

    def seed_promotion(self, raw: dict[str, Any]) -> None:
        """Siembra una promoción tal cual la devuelve Medusa. Si trae una
        campaña con el id de otra ya sembrada, las dos COMPARTEN esa campaña."""
        promo = copy.deepcopy(raw)
        campaign = promo.pop("campaign", None)
        if isinstance(campaign, dict) and campaign.get("id"):
            self._campaigns.setdefault(str(campaign["id"]), campaign)
            promo["campaign_id"] = campaign["id"]
        self._promotions[str(promo["id"])] = promo

    def add_campaign(self, campaign: dict[str, Any]) -> None:
        """Siembra una campaña sin promociones (p. ej. la que quedó de un
        cupón borrado)."""
        self._campaigns[str(campaign["id"])] = copy.deepcopy(campaign)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids):04d}"

    def _render(self, promo: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(promo)
        campaign = self._campaigns.get(str(promo.get("campaign_id") or ""))
        out["campaign"] = copy.deepcopy(campaign) if campaign is not None else None
        out["campaign_id"] = campaign["id"] if campaign is not None else None
        return out

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
        return [self._render(p) for p in self._promotions.values()]

    async def list_campaigns(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(c) for c in self._campaigns.values()]

    async def get_promotion(self, promotion_id: str) -> dict[str, Any]:
        return self._render(self._get(promotion_id))

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
            if any(c.get("campaign_identifier") == ident for c in self._campaigns.values()):
                raise _error(
                    400, path, "invalid_data",
                    f"Campaign with campaign_identifier: {ident}, already exists.",
                )
            campaign = {"id": self._new_id("procamp"), "budget": None, **campaign_in}
            self._campaigns[campaign["id"]] = campaign
        promo = {
            "id": self._new_id("promo"),
            "code": code,
            "type": payload.get("type", "standard"),
            "is_automatic": payload.get("is_automatic", False),
            "status": payload.get("status", "draft"),
            "campaign_id": campaign["id"] if campaign else None,
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
        return self._render(promo)

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
        return self._render(promo)

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
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise _error(404, path, "not_found", f"Campaign with id: {campaign_id} was not found")
        ident = payload.get("campaign_identifier")
        if ident is not None and any(
            c.get("campaign_identifier") == ident and cid != campaign_id
            for cid, c in self._campaigns.items()
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
        # La campaña sobrevive a la promoción (en Medusa son entidades aparte).
        self._promotions.pop(promotion_id)
        return {"id": promotion_id, "object": "promotion", "deleted": True}

    async def delete_campaign(self, campaign_id: str) -> dict[str, Any]:
        self.writes.append(("DELETE", f"/admin/campaigns/{campaign_id}"))
        self._campaigns.pop(campaign_id, None)
        for promo in self._promotions.values():
            if promo.get("campaign_id") == campaign_id:
                promo["campaign_id"] = None
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
    "CouponDeletion",
    "CouponNotFoundError",
    "CouponNotManageableError",
    "CouponPartialUpdateError",
    "CouponRejectedError",
    "CouponWriteUnconfirmedError",
    "FakePromotionsAdmin",
    "InMemoryMedusaPromotions",
    "MedusaPromotionsAdmin",
    "PromotionsAdminPort",
    "UnavailablePromotionsAdmin",
]
