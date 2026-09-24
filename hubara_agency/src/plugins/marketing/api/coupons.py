"""Central de cupones (Marketing → Cupones) — router DELGADO.

El dashboard es la central de control: crea, edita, pausa y borra cupones
de porcentaje escribiendo en Medusa (promoción + campaña) por el
`PromotionsAdminPort`, guarda el cupo por unidad en el vault y deja cada
cambio en el registro con el actor VERIFICADO de la sesión (D6: sin roles,
con auditoría). Lo que Medusa tenga que la central no sabe editar se muestra
en solo lectura con el motivo (D7). La traducción al JSON vive en
`domain/coupons.py`; acá solo se orquestan ports.

Errores: 422 `{field, message}` (dato inválido) · 409 `{message}` (código
ocupado, no gestionable, con ventas) · 409 `{code: "units_changed", message}`
(C-5) · 503 `{message}` (Medusa no responde: dice si NO cambió nada o si no
se sabe) · 502 `{message, step, coupon}` (edición a medias: lo que quedó en
Medusa, o `coupon: null` si no se pudo releer).

Lo que va al vault DESPUÉS de escribir en Medusa (registro, cupo) nunca
convierte la respuesta en 500: el cambio ya está hecho; se registra en el log.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.plugins.marketing.domain.coupons import (
    audit_diff,
    coupon_form,
    coupon_json,
    coupon_product_ids,
    created_since,
    intended_diff,
    quota_row_label,
    results_json,
    scan_since,
    spec_patch,
    units_json,
    units_summary,
)
from src.sdk.castkit import current_actor
from src.sdk.connectorkit import (
    CouponCodeTakenError,
    CouponDeleteRefusedError,
    CouponNotFoundError,
    CouponNotManageableError,
    CouponPartialUpdateError,
    CouponRejectedError,
    CouponSpecError,
    CouponView,
    PromotionsUnavailableError,
    QuotaSheet,
    QuotaStatus,
    QuotaStoreError,
    parse_coupon_spec,
    quota_board,
    quota_product,
    quota_statuses,
    sold_units_by_quota,
    validate_quota_rows,
)

log = logging.getLogger(__name__)

router = APIRouter()

_BOGOTA_OFFSET_H = 5  # Colombia: UTC−5 todo el año
#: Un id de promoción de Medusa (`promo_01K…`): nada que arme una ruta o
#: una URL distinta.
_PROMOTION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: Medusa no confirmó la escritura y releer no lo resolvió (L-1: desconocido).
_CREATE_UNKNOWN = (
    "Medusa no confirmó si el cupón quedó creado. Revisa la lista antes de reintentar: "
    "si ya aparece, no lo vuelvas a crear."
)
_UPDATE_UNKNOWN = (
    "Medusa no confirmó si el cambio quedó guardado. Recarga el cupón para ver cómo quedó; "
    "reintentar es seguro."
)
_STATUS_UNKNOWN = (
    "Medusa no confirmó si el cambio de estado quedó. Recarga el cupón para ver cómo quedó; "
    "reintentar es seguro."
)
_DELETE_UNKNOWN = "Medusa no confirmó si el cupón quedó borrado. Recarga la lista antes de reintentar."
_UNITS_CHANGED = (
    "Otra persona cambió las unidades de este cupón. Recarga para ver lo nuevo antes de guardar."
)
_UNITS_UNREADABLE = (
    "No pude leer las unidades que ya estaban guardadas para este cupón, así que no guardé nada "
    "(para no perder la cuenta de las vendidas). Hay que revisar ese registro antes de volver a guardar."
)


# --- Providers (módulo, monkeypatcheables en tests; imports perezosos: la
#     composición arrastra Medusa y el gate `test_sdk_lazy_surface` lo cuida) --


def promotions_admin() -> Any:
    from src.sdk.connectorkit import get_promotions_admin_port

    return get_promotions_admin_port()


def quota_store() -> Any:
    from src.sdk.connectorkit import get_promo_quota_store

    return get_promo_quota_store()


def audit_log() -> Any:
    from src.sdk.connectorkit import get_coupon_audit_log

    return get_coupon_audit_log()


def sales_reader() -> Any:
    from src.sdk.connectorkit import get_coupon_sales_reader

    return get_coupon_sales_reader()


def promotions_reader() -> Any:
    """Lectura de Medusa con el alcance REAL de cada cupón (traduce las
    reglas por etiquetas): la usa el cupo para no aceptar productos que el
    cupón no cubre."""
    from src.sdk.connectorkit import get_promotions_port

    return get_promotions_port()


def catalog() -> Any:
    from src.sdk.catalogkit import get_catalog_client

    return get_catalog_client()


def now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    """Marca de tiempo de lo que se guarda en el vault. Con microsegundos:
    `updated_at` es la versión del cupo (C-5) y dos guardados no la repiten."""
    return now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- Errores de dominio → HTTP -------------------------------------------------


def _fail(status: int, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, **extra})


def _unavailable(error: Exception | None = None, *, unknown: str = "") -> HTTPException:
    """503. Si la escritura SALIÓ y Medusa no la confirmó, dice que no se
    sabe (nunca "no se hizo ningún cambio")."""
    if unknown and getattr(error, "outcome_unknown", False):
        return _fail(503, unknown)
    return _fail(503, "Medusa no responde ahora mismo; no se hizo ningún cambio. Reintenta en un momento.")


def _pid(promotion_id: str) -> str:
    if not _PROMOTION_ID_RE.fullmatch(promotion_id or ""):
        raise _fail(404, "Ese cupón no existe en Medusa.")
    return promotion_id


def _sheet(promotion_id: str) -> QuotaSheet:
    try:
        return quota_store().get(_pid(promotion_id))
    except QuotaStoreError as e:
        raise _fail(503, "No pude leer las unidades guardadas de este cupón; no se hizo ningún cambio.") from e


async def _view(promotion_id: str) -> CouponView:
    _pid(promotion_id)
    try:
        return await promotions_admin().get_coupon(promotion_id)
    except CouponNotFoundError as e:
        raise _fail(404, "Ese cupón no existe en Medusa.") from e
    except PromotionsUnavailableError as e:
        raise _unavailable() from e


def _spec_error(e: CouponSpecError) -> HTTPException:
    return HTTPException(status_code=422, detail={"field": e.field, "message": e.message})


async def _catalog_products() -> list[Any]:
    try:
        result = await catalog().search("", limit=500)
    except Exception as e:  # noqa: BLE001 — el catálogo es un port externo
        raise _fail(503, "El catálogo no responde ahora mismo; reintenta en un momento.") from e
    return list(result.results)


async def _check_products(products: tuple[str, ...] | None, *, keep: tuple[str, ...] = ()) -> None:
    """Los productos nuevos del cupón tienen que existir en el catálogo (los
    que ya tenía se respetan aunque hoy no estén publicados)."""
    new = [p for p in products or () if p not in keep]
    if not new:
        return
    known = {p.id for p in await _catalog_products()}
    missing = [p for p in new if p not in known]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"field": "products", "message": "Hay productos que no están en el catálogo."},
        )


def _audit(request: Request, action: str, view: CouponView, detail: dict[str, Any] | None = None) -> None:
    _audit_ids(request, action, view.promotion_id, view.code, detail)


def _audit_ids(
    request: Request, action: str, promotion_id: str, code: str, detail: dict[str, Any] | None = None
) -> None:
    """Deja la acción en el registro. Si el vault falla, el cambio YA está
    hecho: se loguea, no se convierte en 500."""
    try:
        audit_log().append(
            actor=current_actor(request),
            action=action,
            promotion_id=promotion_id,
            code=code,
            detail=detail or {},
        )
    except Exception:  # noqa: BLE001 — el registro no deshace lo hecho en Medusa
        log.exception("central: no pude registrar %s del cupón %s", action, promotion_id)


def _since(day: date | None) -> datetime:
    """Inicio de la campaña (00:00 Bogotá) para leer sus ventas."""
    if day is None:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    return datetime.combine(day, time(_BOGOTA_OFFSET_H), tzinfo=timezone.utc)


class _OneScan:
    """Lee los pedidos de Medusa UNA vez (desde el inicio más viejo de las
    hojas de la lista) y responde el `sold_units` de cada hoja filtrando por
    fecha: cada cupón se cuenta desde SU `counting_since`, como en el
    detalle. Si la lectura falla, falla para todas (sin reintentar N veces)."""

    def __init__(self, reader: Any, since: datetime) -> None:
        self._reader = reader
        self._since = since
        self._orders: list[dict[str, Any]] | None = None
        self._error: PromotionsUnavailableError | None = None

    async def sold_units(self, *, since: datetime, exclude: Any = None) -> dict[str, int]:
        if since < self._since:  # no debería pasar (el margen es mayor)
            return await self._reader.sold_units(since=since)
        if self._error is not None:
            raise self._error
        if self._orders is None:
            try:
                self._orders = await self._reader.orders_since(self._since)
            except PromotionsUnavailableError as e:
                self._error = e
                raise
        return sold_units_by_quota(created_since(self._orders, since), exclude=exclude)


# --- Cupones --------------------------------------------------------------------


@router.get("/coupons")
async def list_coupons() -> dict[str, Any]:
    """Todos los cupones de Medusa (gestionables o no) con su cupo resumido.

    Cada cupón se cuenta desde su propio inicio (igual que el detalle), con
    UNA lectura de pedidos; las hojas de cupones que ya no existen en Medusa
    no se cuentan ni estiran esa lectura."""
    try:
        views = await promotions_admin().list_coupons()
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    live = {v.promotion_id for v in views}
    sheets = {s.promotion_id: s for s in quota_store().list_sheets() if s.promotion_id in live}
    boards: dict[str, list[QuotaStatus] | None] = {}
    if sheets:
        reader = _OneScan(sales_reader(), scan_since(list(sheets.values())))
        for promotion_id, sheet in sheets.items():
            try:
                boards[promotion_id] = await quota_board(sheet, reader)
            except PromotionsUnavailableError:
                boards[promotion_id] = None  # las unidades quedan sin "quedan"; la lista sigue
    coupons = []
    for view in views:
        sheet = sheets.get(view.promotion_id) or QuotaSheet(view.promotion_id, view.code, ())
        coupons.append(coupon_json(view, units=units_summary(boards.get(view.promotion_id), sheet)))
    return {"coupons": coupons, "unavailable": False}


class CouponBody(BaseModel):
    code: str = ""
    campaign_name: str = ""
    percentage: Any = None
    products: Any = None
    starts_on: str = ""
    ends_on: str = ""
    status: str = "active"


@router.post("/coupons", status_code=201)
async def create_coupon(body: CouponBody, request: Request) -> dict[str, Any]:
    try:
        spec = parse_coupon_spec(body.model_dump())
    except CouponSpecError as e:
        raise _spec_error(e) from e
    await _check_products(spec.products)
    try:
        view = await promotions_admin().create_coupon(spec)
    except CouponCodeTakenError as e:
        if getattr(e, "campaign_identifier", False):
            # Solo choca la campaña que quedó de un cupón borrado.
            raise _fail(
                409,
                f"Ya hay una campaña en Medusa con el identificador {spec.code} (quedó de un cupón "
                "borrado). Bórrala en Medusa Admin → Promociones → Campañas, o usa otro código.",
            ) from e
        raise _fail(409, "Ese código ya existe.") from e
    except CouponRejectedError as e:
        raise _fail(422, f"Medusa no aceptó el cupón: {e.message}") from e
    except PromotionsUnavailableError as e:
        log.warning("central: crear el cupón %s falló: %s", spec.code, e)
        raise _unavailable(e, unknown=_CREATE_UNKNOWN) from e
    _audit(request, "create", view, {"percentage": view.percentage, "status": view.status})
    return coupon_json(view)


@router.get("/coupons/{promotion_id}")
async def get_coupon(promotion_id: str) -> dict[str, Any]:
    view = await _view(promotion_id)
    sheet = _sheet(promotion_id)
    try:
        board = await quota_board(sheet, sales_reader())
    except PromotionsUnavailableError:
        board = None
    return {
        "coupon": coupon_json(view, units=units_summary(board, sheet)),
        # Sin Medusa se ven las filas (sin vendidas) y el aviso.
        "units": units_json(board if board is not None else quota_statuses(list(sheet.quotas), {}), sheet)
        | {"unavailable": board is None},
        "changes": [
            {"ts": e.ts, "actor": e.actor, "action": e.action, "detail": e.detail}
            for e in audit_log().entries(promotion_id=promotion_id)
        ],
    }


def _prune_units(request: Request, before: CouponView, after: CouponView) -> None:
    """Sacar productos del cupón saca sus filas del cupo (el bot ya no puede
    venderlas y sumaban en los totales de la central). Se registra."""
    if after.products is None or tuple(before.products or ()) == tuple(after.products):
        return
    try:
        removed = quota_store().prune_to_products(
            after.promotion_id, after.products, actor=current_actor(request), now_iso=_stamp()
        )
    except (QuotaStoreError, OSError, ValueError) as e:
        log.warning("central: no pude quitar las filas de productos que salieron de %s: %s", after.promotion_id, e)
        return
    if removed:
        _audit(request, "units_pruned", after, {"rows": [quota_row_label(q) for q in removed]})


@router.patch("/coupons/{promotion_id}")
async def update_coupon(promotion_id: str, patch: dict[str, Any], request: Request) -> dict[str, Any]:
    before = await _view(promotion_id)
    if not before.manageable:
        raise _fail(409, before.unmanageable_reason or "Este cupón se ve en solo lectura.")
    try:
        # Solo se valida lo que cambia: un código de 16 caracteres creado en
        # Medusa no impide editar el porcentaje.
        spec = parse_coupon_spec(spec_patch(before, patch), current=coupon_form(before))
    except CouponSpecError as e:
        raise _spec_error(e) from e
    await _check_products(spec.products, keep=before.products or ())
    try:
        after = await promotions_admin().update_coupon(promotion_id, spec)
    except CouponNotFoundError as e:
        raise _fail(404, "Ese cupón ya no existe en Medusa.") from e
    except CouponNotManageableError as e:
        raise _fail(409, e.reason) from e
    except CouponCodeTakenError as e:
        raise _fail(409, "Ese código ya existe.") from e
    except CouponRejectedError as e:
        raise _fail(422, e.message) from e
    except CouponPartialUpdateError as e:
        applied = list(getattr(e, "applied", ()))
        log.warning(
            "central: la edición del cupón %s quedó a medias en %s (aplicado: %s): %s",
            promotion_id, e.step, applied, e.message,
        )
        if e.view is not None:
            detail = audit_diff(before, e.view) | {"failed_step": e.step}
            _audit(request, "update_partial", e.view, detail)
            _prune_units(request, before, e.view)
            message = "El cambio quedó a medias en Medusa; reintentar es seguro."
        else:
            # No se pudo releer: queda lo que se QUISO cambiar (mismo formato
            # {campo: [antes, pedido]}) y qué pasos sí se aplicaron.
            detail = intended_diff(before, spec) | {
                "failed_step": e.step,
                "applied_steps": ", ".join(applied),
                "state_unknown": True,
            }
            _audit(request, "update_partial", before, detail)
            message = (
                "El cambio quedó a medias en Medusa y no pude ver cómo quedó el cupón. "
                "Recárgalo; reintentar es seguro."
            )
        raise _fail(502, message, step=e.step, coupon=coupon_json(e.view) if e.view else None) from e
    except PromotionsUnavailableError as e:
        log.warning("central: editar el cupón %s falló: %s", promotion_id, e)
        if getattr(e, "outcome_unknown", False):
            _audit(request, "update_unconfirmed", before, intended_diff(before, spec))
        raise _unavailable(e, unknown=_UPDATE_UNKNOWN) from e
    diff = audit_diff(before, after)
    if diff:
        _audit(request, "update", after, diff)
    _prune_units(request, before, after)
    return coupon_json(after)


class StatusBody(BaseModel):
    status: str = Field(default="")


@router.post("/coupons/{promotion_id}/status")
async def set_coupon_status(promotion_id: str, body: StatusBody, request: Request) -> dict[str, Any]:
    _pid(promotion_id)
    if body.status not in ("active", "inactive"):
        raise HTTPException(
            status_code=422, detail={"field": "status", "message": "El estado va como activo o pausado."}
        )
    try:
        view = await promotions_admin().set_status(promotion_id, body.status)
    except CouponNotFoundError as e:
        raise _fail(404, "Ese cupón no existe en Medusa.") from e
    except CouponNotManageableError as e:
        raise _fail(409, e.reason) from e
    except CouponRejectedError as e:
        raise _fail(422, f"Medusa no aceptó el cambio de estado: {e.message}") from e
    except PromotionsUnavailableError as e:
        log.warning("central: cambiar el estado del cupón %s falló: %s", promotion_id, e)
        if getattr(e, "outcome_unknown", False):
            _audit_ids(request, "set_status_unconfirmed", promotion_id, await _code_of(promotion_id),
                       {"status": body.status})
        raise _unavailable(e, unknown=_STATUS_UNKNOWN) from e
    _audit(request, "set_status", view, {"status": body.status})
    return coupon_json(view)


async def _code_of(promotion_id: str) -> str:
    """El código de un cupón para el registro, si Medusa responde ("" si no)."""
    try:
        return (await promotions_admin().get_coupon(promotion_id)).code
    except Exception:  # noqa: BLE001 — best-effort: el registro va igual
        return ""


def _forget_units(promotion_id: str) -> bool:
    """Borra la hoja del cupo de un cupón que ya no existe. False si el
    vault falló (queda en el log; el cupón ya no está en Medusa)."""
    try:
        quota_store().delete(promotion_id)
    except (OSError, ValueError) as e:
        log.warning("central: el cupón %s ya no existe pero no pude borrar sus unidades: %s", promotion_id, e)
        return False
    return True


def _code_in_audit(promotion_id: str) -> str:
    try:
        entries = audit_log().entries(promotion_id=promotion_id, limit=50)
    except Exception:  # noqa: BLE001 — best-effort
        return ""
    return next((e.code for e in entries if e.code), "")


async def _clean_up_deleted(request: Request, promotion_id: str, *, code: str = "") -> None:
    """El cupón ya no existe en Medusa (p. ej. un borrado que Medusa aplicó
    sin confirmar y ahora se reintenta). Lo que quedó de él se limpia: sus
    unidades y la campaña SIN cupones que bloquea su código. Sin rastro local
    (hoja o registro) no se toca nada: un id cualquiera no borra campañas."""
    try:
        sheet: QuotaSheet | None = quota_store().get(promotion_id)
    except (QuotaStoreError, OSError, ValueError):
        sheet = None  # ilegible: igual se borra (el cupón ya no existe)
    code = code or (sheet.code if sheet is not None else "") or _code_in_audit(promotion_id)
    had_units = sheet is None or bool(sheet.quotas)
    if not code and not had_units:
        return
    campaigns: list[str] = []
    if code:
        try:
            campaigns = list(await promotions_admin().delete_orphan_campaigns(code))
        except Exception as e:  # noqa: BLE001 — limpieza best-effort
            log.warning("central: no pude limpiar la campaña que dejó el cupón %s (%s): %s", promotion_id, code, e)
    units_deleted = had_units and _forget_units(promotion_id)
    if units_deleted or campaigns:
        log.warning(
            "central: el cupón %s (%s) ya estaba borrado en Medusa; limpié unidades=%s campañas=%s",
            promotion_id, code, units_deleted, campaigns,
        )
        _audit_ids(
            request, "delete", promotion_id, code,
            {"already_deleted": True, "units_deleted": units_deleted, "orphan_campaigns_deleted": campaigns},
        )


@router.delete("/coupons/{promotion_id}", status_code=204)
async def delete_coupon(promotion_id: str, request: Request) -> Response:
    _pid(promotion_id)
    try:
        view = await promotions_admin().get_coupon(promotion_id)
    except CouponNotFoundError as e:
        await _clean_up_deleted(request, promotion_id)
        raise _fail(404, "Ese cupón no existe en Medusa.") from e
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    try:
        results = await sales_reader().results(view.code, since=_since(view.starts_on))
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    if results.orders:
        raise _fail(409, "Este cupón ya tiene ventas: no se borra; páusalo.")
    try:
        deletion = await promotions_admin().delete_coupon(promotion_id)
    except CouponNotFoundError as e:
        await _clean_up_deleted(request, promotion_id, code=view.code)
        raise _fail(404, "Ese cupón ya no existe en Medusa.") from e
    except CouponNotManageableError as e:
        raise _fail(409, e.reason) from e
    except CouponDeleteRefusedError as e:
        raise _fail(409, str(e)) from e
    except CouponRejectedError as e:
        raise _fail(422, f"Medusa no aceptó el borrado: {e.message}") from e
    except PromotionsUnavailableError as e:
        log.warning("central: borrar el cupón %s falló: %s", promotion_id, e)
        if getattr(e, "outcome_unknown", False):
            _audit(request, "delete_unconfirmed", view)
        raise _unavailable(e, unknown=_DELETE_UNKNOWN) from e
    _forget_units(promotion_id)
    detail: dict[str, Any] = {}
    orphan = getattr(deletion, "orphaned_campaign_id", None)
    if orphan:
        # Su identificador bloquea volver a crear el código: queda a la vista.
        log.warning("central: cupón %s borrado; su campaña %s quedó en Medusa", promotion_id, orphan)
        detail["orphaned_campaign_id"] = orphan
    _audit(request, "delete", view, detail)
    return Response(status_code=204)


# --- Cupo por unidad --------------------------------------------------------------


@router.get("/coupons/{promotion_id}/units")
async def get_coupon_units(promotion_id: str) -> dict[str, Any]:
    sheet = _sheet(promotion_id)
    try:
        board = await quota_board(sheet, sales_reader())
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    return units_json(board, sheet)


class UnitsBody(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    show_units_left: bool = True
    #: C-5: el `updated_at` de la versión que editó el operador (None =
    #: "nunca se guardó"). Sin el campo (cliente viejo) no hay chequeo.
    expected_updated_at: str | None = None


@router.put("/coupons/{promotion_id}/units")
async def put_coupon_units(promotion_id: str, body: UnitsBody, request: Request) -> dict[str, Any]:
    """Reemplaza las filas del cupo. Todo o nada: con una fila inválida no se
    guarda ninguna. Vale también para cupones de solo lectura (el cupo vive
    en Hubara, no toca Medusa). Con `expected_updated_at`, si otra persona
    guardó después → 409 `units_changed` (se compara bajo el candado)."""
    view = await _view(promotion_id)
    if view.percentage is None:
        raise _fail(409, "El cupo por unidad es solo para cupones de porcentaje.")
    catalog_products = await _catalog_products()
    products = {
        p.id: quota_product(p.id, p.handle, p.title, list(p.tags or []))
        for p in catalog_products
    }
    # Alcance REAL del cupón (con la regla por etiquetas traducida): una fila
    # de un producto que el cupón no cubre haría prometer un descuento falso.
    try:
        promotion = await promotions_reader().get_by_code(view.code)
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    if promotion is None or promotion.scope_unresolved:
        raise _fail(409, "No pude confirmar a qué productos aplica este cupón; revisa sus reglas en Medusa.")
    actor = current_actor(request)
    stamp = _stamp()
    quotas, errors = validate_quota_rows(
        body.rows,
        promotion_id=promotion_id,
        code=view.code,
        coupon_products=coupon_product_ids(promotion, catalog_products),
        products=products,
        actor=actor,
        now_iso=stamp,
    )
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Revisa las filas marcadas.",
                "rows": [{"row": e.row, "field": e.field, "message": e.message} for e in errors],
            },
        )
    version = (
        {"expected_updated_at": body.expected_updated_at}
        if "expected_updated_at" in body.model_fields_set
        else {}
    )
    try:
        sheet = quota_store().replace(
            promotion_id,
            view.code,
            quotas,
            show_units_left=body.show_units_left,
            actor=actor,
            now_iso=stamp,
            **version,
        )
    except QuotaStoreError as e:
        if getattr(e, "reason", "") == "changed":
            raise HTTPException(
                status_code=409, detail={"code": "units_changed", "message": _UNITS_CHANGED}
            ) from e
        log.warning("central: el cupo guardado del cupón %s no se puede leer: %s", promotion_id, e)
        raise _fail(409, _UNITS_UNREADABLE) from e
    _audit(
        request,
        "units",
        view,
        {"rows": [quota_row_label(q) for q in quotas], "show_units_left": body.show_units_left},
    )
    try:
        board = await quota_board(sheet, sales_reader())
    except PromotionsUnavailableError:
        board = quota_statuses(list(sheet.quotas), {})
    return units_json(board, sheet)


# --- Resultados y catálogo ----------------------------------------------------------


@router.get("/coupons/{promotion_id}/sales")
async def get_coupon_sales(promotion_id: str) -> dict[str, Any]:
    view = await _view(promotion_id)
    try:
        results = await sales_reader().results(view.code, since=_since(view.starts_on))
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    return results_json(results)


@router.get("/coupon-products")
async def list_coupon_products() -> dict[str, Any]:
    """Productos del catálogo con sus listas cerradas de color y aroma (para
    elegir productos del cupón y las filas del cupo)."""
    products = []
    for p in await _catalog_products():
        qp = quota_product(p.id, p.handle, p.title, list(p.tags or []))
        products.append(
            {"id": qp.product_id, "handle": qp.handle, "title": qp.title, "colors": qp.colors, "aromas": qp.aromas}
        )
    return {"products": products}
