"""Central de cupones (Marketing → Cupones) — router DELGADO.

El dashboard es la central de control: crea, edita, pausa y borra cupones
de porcentaje escribiendo en Medusa (promoción + campaña) por el
`PromotionsAdminPort`, guarda el cupo por unidad en el vault y deja cada
cambio en el registro con el actor VERIFICADO de la sesión (D6: sin roles,
con auditoría). Lo que Medusa tenga que la central no sabe editar se muestra
en solo lectura con el motivo (D7). La traducción al JSON vive en
`domain/coupons.py`; acá solo se orquestan ports.

Errores: 422 `{field, message}` (dato inválido) · 409 `{message}` (código
ocupado, no gestionable, con ventas) · 503 `{message}` (Medusa no responde)
· 502 `{message, step, coupon}` (edición a medias: lo que quedó en Medusa).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.plugins.marketing.domain.coupons import (
    audit_diff,
    coupon_product_ids,
    coupon_json,
    results_json,
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
    QuotaStoreError,
    parse_coupon_spec,
    quota_board,
    quota_product,
    quota_statuses,
    validate_quota_rows,
)

router = APIRouter()

_BOGOTA_OFFSET_H = 5  # Colombia: UTC−5 todo el año
#: Un id de promoción de Medusa (`promo_01K…`): nada que arme una ruta o
#: una URL distinta.
_PROMOTION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


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


# --- Errores de dominio → HTTP -------------------------------------------------


def _fail(status: int, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, **extra})


def _unavailable() -> HTTPException:
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
    audit_log().append(
        actor=current_actor(request),
        action=action,
        promotion_id=view.promotion_id,
        code=view.code,
        detail=detail or {},
    )


def _since(day: date | None) -> datetime:
    """Inicio de la campaña (00:00 Bogotá) para leer sus ventas."""
    if day is None:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    return datetime.combine(day, time(_BOGOTA_OFFSET_H), tzinfo=timezone.utc)


# --- Cupones --------------------------------------------------------------------


@router.get("/coupons")
async def list_coupons() -> dict[str, Any]:
    """Todos los cupones de Medusa (gestionables o no) con su cupo resumido."""
    try:
        views = await promotions_admin().list_coupons()
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    store = quota_store()
    sheets = {s.promotion_id: s for s in store.list_sheets()}
    sold: dict[str, int] | None = {}
    if sheets:
        all_rows = QuotaSheet("*", "", tuple(q for s in sheets.values() for q in s.quotas))
        try:
            sold = {s.quota.id: s.sold for s in await quota_board(all_rows, sales_reader())}
        except PromotionsUnavailableError:
            sold = None  # las unidades quedan sin "quedan"; la lista sigue
    coupons = []
    for view in views:
        sheet = sheets.get(view.promotion_id) or QuotaSheet(view.promotion_id, view.code, ())
        board = quota_statuses(list(sheet.quotas), sold) if sold is not None else None
        coupons.append(coupon_json(view, units=units_summary(board, sheet)))
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
        raise _fail(409, "Ese código ya existe.") from e
    except CouponRejectedError as e:
        raise _fail(422, f"Medusa no aceptó el cupón: {e.message}") from e
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
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


@router.patch("/coupons/{promotion_id}")
async def update_coupon(promotion_id: str, patch: dict[str, Any], request: Request) -> dict[str, Any]:
    before = await _view(promotion_id)
    if not before.manageable:
        raise _fail(409, before.unmanageable_reason or "Este cupón se ve en solo lectura.")
    try:
        spec = parse_coupon_spec(spec_patch(before, patch))
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
        if e.view is not None:
            _audit(request, "update_partial", e.view, audit_diff(before, e.view) | {"failed_step": e.step})
        raise _fail(
            502,
            "El cambio quedó a medias en Medusa; reintentar es seguro.",
            step=e.step,
            coupon=coupon_json(e.view) if e.view else None,
        ) from e
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    diff = audit_diff(before, after)
    if diff:
        _audit(request, "update", after, diff)
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
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    _audit(request, "set_status", view, {"status": body.status})
    return coupon_json(view)


@router.delete("/coupons/{promotion_id}", status_code=204)
async def delete_coupon(promotion_id: str, request: Request) -> Response:
    view = await _view(promotion_id)
    try:
        results = await sales_reader().results(view.code, since=_since(view.starts_on))
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    if results.orders:
        raise _fail(409, "Este cupón ya tiene ventas: no se borra; páusalo.")
    try:
        await promotions_admin().delete_coupon(promotion_id)
    except CouponNotManageableError as e:
        raise _fail(409, e.reason) from e
    except CouponDeleteRefusedError as e:
        raise _fail(409, str(e)) from e
    except PromotionsUnavailableError as e:
        raise _unavailable() from e
    quota_store().delete(promotion_id)
    _audit(request, "delete", view)
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


@router.put("/coupons/{promotion_id}/units")
async def put_coupon_units(promotion_id: str, body: UnitsBody, request: Request) -> dict[str, Any]:
    """Reemplaza las filas del cupo. Todo o nada: con una fila inválida no se
    guarda ninguna. Vale también para cupones de solo lectura (el cupo vive
    en Hubara, no toca Medusa)."""
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
    quotas, errors = validate_quota_rows(
        body.rows,
        promotion_id=promotion_id,
        code=view.code,
        coupon_products=coupon_product_ids(promotion, catalog_products),
        products=products,
        actor=actor,
        now_iso=now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Revisa las filas marcadas.",
                "rows": [{"row": e.row, "field": e.field, "message": e.message} for e in errors],
            },
        )
    sheet = quota_store().replace(
        promotion_id,
        view.code,
        quotas,
        show_units_left=body.show_units_left,
        actor=actor,
        now_iso=now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _audit(
        request,
        "units",
        view,
        {
            "rows": [f"{q.title} · {q.color or '—'} · {q.aroma or '—'}: {q.units}" for q in quotas],
            "show_units_left": body.show_units_left,
        },
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
