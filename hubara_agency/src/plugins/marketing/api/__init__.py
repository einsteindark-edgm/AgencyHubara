"""API del plugin marketing — routers DELGADOS (el perfil lo audita).

La lógica vive en ``domain/`` (pura, sin I/O); este módulo traduce
HTTP ↔ dominio. Auth: la aplica el loader central (`src/main.py`) al montar
el router (require_auth salvo rutas públicas — acá no hay ninguna pública).
"""
import json
import logging
import re
import time
import uuid
from datetime import timedelta
from typing import Any

from anyio import from_thread
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.plugins.marketing.api.coupons import router as coupons_router
from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.carousel import CarouselError, resolve_campaign_carousel
from src.plugins.marketing.domain.campaigns import (
    SKIP_DADO_DE_BAJA,
    SKIP_SESION_DE_PRUEBA,
    is_test_session,
    ALL_SEGMENTS,
    IMPORTED_CONTACTS_CAP,
    STATUS_DRAFT,
    STATUS_SCHEDULED,
    append_campaign_touch,
    build_campaign_touch,
    campaign_stats,
    campaign_template_name,
    campaign_template_variables,
    carousel_handles,
    carousel_size_error,
    customer_name_from_metadata,
    new_campaign,
    resolve_campaign_audience,
    segment_for_metadata,
)
from src.plugins.marketing.domain.contacts import normalize_phone, parse_contacts_file
from src.plugins.marketing.domain.coupons import coupon_terms
from src.plugins.marketing.domain.logic import health_payload
from src.sdk import get_task_queue
from src.sdk.connectorkit import (
    FilesystemAttributionStore,
    OrderFactsSnapshot,
    get_catalog_client,
)
from src.sdk.messagingkit import (
    get_current_rate_card,
    send_template_to_session,
)
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
    get_temporal_client,
)

log = logging.getLogger(__name__)

router = APIRouter()
# Central de cupones (Marketing → Cupones): /coupons, /coupon-products.
router.include_router(coupons_router)

_SEGMENT_LABELS: dict[str, tuple[str, str]] = {
    "clientes": ("Clientes", "Ya compraron · alto valor"),
    "interesados": ("Interesados", "Mostraron intención o tienen pago pendiente"),
    "frios": ("Fríos", "Consultaron sin etiqueta de compra"),
}

#: Tipos aceptados para el archivo de contactos (CSV/TSV/texto plano). Excel
#: exporta CSV; un .xlsx binario se rechaza con 415 (conviértalo a CSV).
_CONTACTS_MIMES = {
    "text/csv",
    "text/plain",
    "text/tab-separated-values",
    "application/csv",
    "application/vnd.ms-excel",  # Windows etiqueta los .csv así
    "application/octet-stream",  # algunos navegadores no tipan el .csv
}
_CONTACTS_EXTENSIONS = (".csv", ".txt", ".tsv")
#: 1 MB de texto ≈ 80k líneas — muy por encima del cap de contactos.
_MAX_CONTACTS_BYTES = 1 * 1024 * 1024

#: Estados en los que la campaña sigue siendo editable por el operador.
_EDITABLE_STATUSES = {STATUS_DRAFT, STATUS_SCHEDULED}


def _store() -> CampaignStore:
    return CampaignStore(WORKSPACE_VAULT_DIR)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_campaign(campaign_id: str) -> dict[str, Any]:
    campaign = _store().get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    return campaign


@router.get("/health")
def health() -> dict:
    """Smoke del plugin."""
    return health_payload()


# --- CRUD -------------------------------------------------------------------


class CreateCampaignBody(BaseModel):
    name: str = Field(default="Nueva campaña sin título", max_length=120)


class MessageBody(BaseModel):
    # La plantilla aprobada solo tiene cuerpo (saludo + mensaje + oferta +
    # baja fija): pie y botón no existen. Si un dashboard viejo los manda,
    # pydantic los ignora.
    header: str = Field(default="", max_length=60)
    body: str = Field(default="", max_length=640)


class UpdateCampaignBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    goal: str | None = None
    percent: int | None = Field(default=None, ge=0, le=100)
    coupon_code: str | None = Field(default=None, max_length=14)
    valid_until: str | None = Field(default=None, max_length=60)
    # Carrusel de productos (handles del catálogo): [] = sin carrusel, si no
    # 2..10 (Meta fija la cantidad de tarjetas al aprobar la plantilla).
    carousel_handles: list[str] | None = Field(default=None, max_length=20)
    segments: list[str] | None = None
    message: MessageBody | None = None
    # Curaduría manual de la audiencia (replace completo, como el resto del PUT).
    excluded_session_ids: list[str] | None = Field(default=None, max_length=5000)
    extra_session_ids: list[str] | None = Field(default=None, max_length=5000)


@router.post("/campaigns", status_code=201)
def create_campaign(body: CreateCampaignBody) -> dict:
    campaign = new_campaign(
        campaign_id=f"mkt-{uuid.uuid4().hex[:10]}",
        name=body.name,
        now_ms=_now_ms(),
    )
    _store().save(campaign)
    return campaign


@router.get("/campaigns")
def list_campaigns() -> dict:
    return {"campaigns": _store().list_campaigns()}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str) -> dict:
    return _require_campaign(campaign_id)


@router.put("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, body: UpdateCampaignBody) -> dict:
    campaign = _require_campaign(campaign_id)
    if campaign["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Campaña en estado {campaign['status']!r} — ya no es editable",
        )
    patch = body.model_dump(exclude_none=True)
    if "segments" in patch:
        unknown = set(patch["segments"]) - set(ALL_SEGMENTS)
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"Segmentos desconocidos: {sorted(unknown)}"
            )
    if "coupon_code" in patch:
        code = patch["coupon_code"].strip().upper()
        # Solo letras y números: un cupón con forma de tag interno (`VELAS_10`)
        # dispara el guard anti-leak del bot y lo enmudece (memoria
        # coupon-tag-shape-collision). Vacío = sin cupón.
        if code and not _COUPON_CODE_RE.fullmatch(code):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cupón inválido: {code!r}. Solo letras y números, sin espacios, "
                    "guiones ni guiones bajos (ej. MAMA15)."
                ),
            )
        patch["coupon_code"] = code
    if "carousel_handles" in patch:
        handles: list[str] = []
        for handle in patch["carousel_handles"]:
            if not isinstance(handle, str) or not _HANDLE_RE.fullmatch(handle):
                raise HTTPException(
                    status_code=422, detail=f"handle de producto inválido: {handle!r}"
                )
            if handle not in handles:
                handles.append(handle)
        size_error = carousel_size_error(handles)
        if size_error:
            raise HTTPException(status_code=422, detail=size_error)
        patch["carousel_handles"] = handles
    for field in ("excluded_session_ids", "extra_session_ids"):
        if field not in patch:
            continue
        # fullmatch: el `$` de `match` acepta un salto de línea final.
        bad_format = [s for s in patch[field] if not _SESSION_ID_RE.fullmatch(s)]
        if bad_format:
            raise HTTPException(
                status_code=422,
                detail=f"{field}: session_id inválido: {bad_format[:5]}",
            )
    if "extra_session_ids" in patch:
        # Un agregado manual necesita sesión en el vault (el send resuelve
        # phone_number_id de su metadata — misma regla que el envío de prueba).
        missing = [
            s
            for s in patch["extra_session_ids"]
            if not (WORKSPACE_VAULT_DIR / s / "metadata.json").exists()
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Sin conversación previa con el bot: {', '.join(missing[:5])} — "
                    "el número debe haber chateado al menos una vez"
                ),
            )
    if "message" in patch:
        # Solo header/body: una campaña vieja con footer/cta los pierde acá.
        merged = {**campaign["message"], **patch["message"]}
        patch["message"] = {"header": merged.get("header", ""), "body": merged.get("body", "")}
    campaign.update(patch)
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    return campaign


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: str) -> None:
    campaign = _require_campaign(campaign_id)
    if campaign["status"] != STATUS_DRAFT:
        raise HTTPException(
            status_code=409, detail="Solo se puede borrar una campaña en borrador"
        )
    _store().delete(campaign_id)


# --- Contactos importados (CSV) --------------------------------------------


def _decode_contacts_file(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


@router.post("/campaigns/{campaign_id}/contacts/import")
async def import_contacts(campaign_id: str, file: UploadFile = File(...)) -> dict:
    """Importa una lista de números (CSV/TSV/texto) como audiencia extra.

    Merge con lo ya importado (dedupe por teléfono). Los números NO necesitan
    conversación previa con el bot: el envío usa el número del negocio
    (`WHATSAPP_PHONE_NUMBER_ID`) y el primer mensaje crea la sesión. Devuelve
    el resumen (importados / duplicados / rechazados con línea) para que el
    operador vea qué NO entró y por qué.
    """
    campaign = _require_campaign(campaign_id)
    if campaign["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Campaña en estado {campaign['status']!r} — ya no es editable",
        )
    mime = (file.content_type or "").split(";")[0].strip().lower()
    filename = (file.filename or "").lower()
    if mime not in _CONTACTS_MIMES and not filename.endswith(_CONTACTS_EXTENSIONS):
        raise HTTPException(
            status_code=415,
            detail=f"Tipo no soportado: {mime or filename!r}. Subí un CSV o TXT.",
        )
    if file.size is not None and file.size > _MAX_CONTACTS_BYTES:
        raise HTTPException(
            status_code=413, detail="Archivo demasiado grande (máximo 1 MB)"
        )
    content = await file.read(_MAX_CONTACTS_BYTES + 1)
    if len(content) > _MAX_CONTACTS_BYTES:
        raise HTTPException(
            status_code=413, detail="Archivo demasiado grande (máximo 1 MB)"
        )
    if content[:4] == b"PK\x03\x04" or b"\x00" in content[:512]:
        raise HTTPException(
            status_code=415,
            detail="El archivo no es texto (¿.xlsx?). Exportalo como CSV.",
        )

    result = parse_contacts_file(_decode_contacts_file(content))
    existing = list(campaign.get("imported_contacts") or [])
    known = {c.get("phone") for c in existing if isinstance(c, dict)}
    added = 0
    duplicates = result.duplicates
    for contact in result.contacts:
        if contact.phone in known:
            duplicates += 1
            continue
        if len(existing) >= IMPORTED_CONTACTS_CAP:
            break
        existing.append({"phone": contact.phone, "name": contact.name})
        known.add(contact.phone)
        added += 1
    campaign["imported_contacts"] = existing
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    return {
        "imported": added,
        "duplicates": duplicates,
        "rejected": [{"line": r.line, "reason": r.reason} for r in result.rejected][
            :200
        ],
        "rejected_count": len(result.rejected),
        "total": len(existing),
        "campaign": campaign,
    }


@router.delete("/campaigns/{campaign_id}/contacts")
def clear_contacts(campaign_id: str) -> dict:
    """Vacía la lista importada (la campaña vuelve a depender de segmentos)."""
    campaign = _require_campaign(campaign_id)
    if campaign["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Campaña en estado {campaign['status']!r} — ya no es editable",
        )
    campaign["imported_contacts"] = []
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    return campaign


# --- Segmentos + costos -----------------------------------------------------


# --- Cupones (promociones de Medusa) ----------------------------------------


def get_promotions_port():
    """Provider a nivel módulo (lazy + monkeypatcheable en tests): el import
    arrastra la composición de Medusa (gate `test_sdk_lazy_surface`)."""
    from src.sdk.connectorkit import get_promotions_port as _factory

    return _factory()


#: Por qué el cupón que anuncia la campaña no sirve — para el operador.
_COUPON_PROBLEM = {
    "invalid_format": "no tiene una forma válida (solo letras y números)",
    "not_found": "no existe en Medusa — créalo en Medusa → Promociones o elige uno de los vigentes",
    "inactive": "está inactivo en Medusa",
    "not_started": "todavía no empieza a regir en Medusa",
    "expired": "ya venció en Medusa",
    "budget_exhausted": "ya agotó sus usos en Medusa",
    "scope_unresolved": "tiene reglas que no pude leer en Medusa (no sé a qué productos aplica)",
}


async def _validate_campaign_coupon(campaign: dict[str, Any]) -> None:
    """Valida el cupón de la campaña y, si es válido, copia sus términos (el %
    y el último día) a la campaña: la plantilla anuncia lo que dice el cupón,
    no lo que tipeó el operador (central de cupones, Fase 7.1)."""
    promotion = await _resolve_campaign_coupon(campaign)
    if promotion is None:
        return
    terms = coupon_terms(promotion)
    if any(campaign.get(k) != v for k, v in terms.items()):
        campaign.update(terms)
        campaign["updated_at_ms"] = _now_ms()
        _store().save(campaign)


async def _resolve_campaign_coupon(campaign: dict[str, Any]) -> Any:
    """El cupón que anuncia la campaña tiene que existir y regir en Medusa —
    el mismo chequeo que hace el bot con `apply_coupon`. Incidente
    2026-09-22: la campaña anunció "AMOR" y el código real era AMOR26, así
    que el cliente que lo escribiera recibía "ese código no existe"."""
    from src.sdk.connectorkit import PromotionsUnavailableError, resolve_coupon

    code = (campaign.get("coupon_code") or "").strip()
    if not code:
        return None
    port = get_promotions_port()
    try:
        promotions = list(await port.list_active())
        extra = await port.get_by_code(code)
    except PromotionsUnavailableError as e:
        raise HTTPException(
            status_code=503,
            detail=f"No pude validar el cupón {code} en Medusa ahora mismo — reintenta en un momento",
        ) from e
    if extra is not None and all(p.id != extra.id for p in promotions):
        promotions.append(extra)
    resolution = resolve_coupon(code, promotions, now_ms=_now_ms())
    if not resolution.ok:
        problem = _COUPON_PROBLEM.get(resolution.reason or "", "no se puede usar")
        raise HTTPException(status_code=422, detail=f"El cupón {code} {problem}")
    return resolution.promotion


@router.get("/promotions")
async def list_promotions() -> dict:
    """Cupones vigentes en Medusa (Admin → Promotions) para elegir en el
    builder: el mismo código que el bot valida con `apply_coupon`."""
    from src.sdk.connectorkit import PromotionsUnavailableError

    try:
        promotions = await get_promotions_port().list_active()
    except PromotionsUnavailableError as e:
        log.warning("marketing: no pude leer promociones de Medusa: %s", e)
        return {"promotions": [], "unavailable": True}
    return {
        "promotions": [
            {
                "code": p.code,
                "discount_type": p.discount_type,
                "value": p.value,
                "target_type": p.target_type,
                "name": p.description,
                "ends_at_ms": p.ends_at_ms,
                "min_subtotal_cop": p.min_subtotal_cop,
                "product_count": len(p.product_ids) + len(p.variant_ids) + len(p.collection_ids),
            }
            for p in promotions
        ],
        "unavailable": False,
    }


# --- Catálogo (picker de producto) -----------------------------------------


@router.get("/products")
async def list_products() -> dict:
    """Productos del snapshot local de catálogo, para el picker del builder."""
    result = await get_catalog_client().search("", limit=200)
    products = []
    for p in result.results:
        variant = p.variants[0] if p.variants else None
        price = variant.prices[0] if variant and variant.prices else None
        products.append(
            {
                "handle": p.handle,
                "title": p.title,
                "sku": variant.sku if variant else None,
                "category": p.categories[0] if p.categories else None,
                "price_amount": price.amount if price else None,
                "currency": price.currency_code if price else None,
                "thumbnail": p.thumbnail,
            }
        )
    return {"products": products}


# --- Enviar / programar / prueba -------------------------------------------


class SendCampaignBody(BaseModel):
    schedule_at_ms: int | None = Field(default=None, ge=0)


class TestSendBody(BaseModel):
    phone: str = Field(min_length=7, max_length=25)


def _validate_ready_to_send(campaign: dict[str, Any]) -> None:
    problems = []
    if not campaign.get("goal"):
        problems.append("falta el objetivo")
    if not (campaign.get("message") or {}).get("body"):
        problems.append("falta el cuerpo del mensaje")
    if not campaign.get("segments") and not campaign.get("imported_contacts"):
        problems.append("falta elegir audiencia (segmentos o contactos importados)")
    size_error = carousel_size_error(carousel_handles(campaign))
    if size_error:
        problems.append(size_error)
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(campaign_id: str, body: SendCampaignBody) -> dict:
    """Envía ahora o programa (start_delay Temporal) el CampaignSendWorkflow."""
    campaign = _require_campaign(campaign_id)
    if campaign["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Campaña en estado {campaign['status']!r} — no se puede enviar",
        )
    _validate_ready_to_send(campaign)
    await _validate_campaign_coupon(campaign)

    now = _now_ms()
    start_delay = None
    if body.schedule_at_ms is not None:
        if body.schedule_at_ms <= now:
            raise HTTPException(
                status_code=422, detail="schedule_at_ms debe estar en el futuro"
            )
        start_delay = timedelta(milliseconds=body.schedule_at_ms - now)

    client = await get_temporal_client()
    workflow_id = f"campaign-send-{campaign_id}"
    try:
        handle = await client.start_workflow(
            "CampaignSendWorkflow",
            args=[campaign_id, campaign.get("name") or campaign_id],
            id=workflow_id,
            task_queue=get_task_queue("marketing", "campaigns"),
            start_delay=start_delay,
        )
    except Exception as exc:  # noqa: BLE001 — superficie el error al operador
        log.warning("marketing.send: start_workflow falló: %s", exc)
        detail = f"No pude arrancar el envío en Temporal: {exc}"
        status = 409 if "already" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=detail) from exc

    if body.schedule_at_ms is not None:
        campaign["status"] = STATUS_SCHEDULED
        campaign["schedule_at_ms"] = body.schedule_at_ms
        campaign["updated_at_ms"] = now
        _store().save(campaign)

    return {
        "workflow_id": workflow_id,
        "run_id": getattr(handle, "first_execution_run_id", "") or "",
        "scheduled": body.schedule_at_ms is not None,
    }


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str) -> dict:
    """Cancela una campaña PROGRAMADA: aborta el workflow encolado y vuelve
    la campaña a borrador (re-programar = editar + enviar de nuevo).

    Reconciliación-lite: si Temporal ya no conoce el workflow (purga de
    namespace, deploy), igual se resetea — el estado del vault es el que ve
    el operador y no puede quedar `scheduled` huérfano.
    """
    campaign = _require_campaign(campaign_id)
    if campaign["status"] != STATUS_SCHEDULED:
        raise HTTPException(
            status_code=409,
            detail=f"Solo se cancela una campaña programada (está {campaign['status']!r})",
        )
    try:
        client = await get_temporal_client()
        await client.get_workflow_handle(f"campaign-send-{campaign_id}").cancel()
        workflow_cancelled = True
    except Exception as exc:  # noqa: BLE001 — reset igual; queda en el log
        log.warning("marketing.cancel: cancel del workflow falló: %s", exc)
        workflow_cancelled = False

    campaign["status"] = STATUS_DRAFT
    campaign["schedule_at_ms"] = None
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    return {"ok": True, "workflow_cancelled": workflow_cancelled}


#: Lo que puede traer un teléfono tecleado: dígitos, `+`, espacios y
#: separadores. Cualquier otra cosa (`/`, `.`, letras) se rechaza ANTES de
#: normalizar — el id termina en un Path del vault (ver `_SESSION_ID_RE`).
_PHONE_INPUT_RE = re.compile(r"^[\d\s+\-()]+$")


def _session_id_for_phone(phone: str) -> str | None:
    """`wa_<E.164 sin +>` o None si no es un celular usable.

    Misma regla que el CSV de audiencia (`normalize_phone`): "3001234567" y
    "+57 300 123 4567" caen en la MISMA sesión `wa_573001234567`, que es como
    la escribe el webhook. Sin esto el operador tecleaba su celular sin
    indicativo y el endpoint buscaba `wa_3001234567`, que no existe.
    """
    if not _PHONE_INPUT_RE.fullmatch(phone):
        return None
    normalized = normalize_phone(phone)
    return f"wa_{normalized}" if normalized else None


@router.post("/campaigns/{campaign_id}/test")
async def test_send(campaign_id: str, body: TestSendBody) -> dict:
    """Envío de prueba a UN número del operador, antes de disparar la campaña.

    NO exige conversación previa: igual que los contactos importados, el envío
    usa el número del negocio (`WHATSAPP_PHONE_NUMBER_ID`) cuando el número no
    tiene sesión, y el primer mensaje la crea. Si la tiene, el saludo lleva su
    nombre. Un rechazo de Meta (plantilla no aprobada, número inválido) sale
    como 502 con el motivo — nunca un 500 pelado.
    """
    campaign = _require_campaign(campaign_id)
    session_id = _session_id_for_phone(body.phone)
    if session_id is None or not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(
            status_code=422,
            detail=(
                f"El número {body.phone!r} no es un celular válido — escríbelo "
                "como 3001234567 o +57 300 123 4567"
            ),
        )
    session_metadata = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_id)
    variables = campaign_template_variables(
        campaign, customer_name=customer_name_from_metadata(session_metadata)
    )
    size_error = carousel_size_error(carousel_handles(campaign))
    if size_error:
        raise HTTPException(status_code=422, detail=size_error)
    await _validate_campaign_coupon(campaign)
    send_kwargs: dict[str, Any] = {}
    if carousel_handles(campaign):
        try:
            send_kwargs["carousel_cards"] = await resolve_campaign_carousel(
                campaign, now_ms=_now_ms()
            )
        except CarouselError as e:
            raise HTTPException(status_code=422, detail=f"Carrusel: {e}") from e
    try:
        result = await send_template_to_session(
            session_id, campaign_template_name(campaign), variables, **send_kwargs
        )
    except Exception as exc:  # noqa: BLE001 — superficie el motivo al operador
        log.exception("marketing: envío de prueba rechazado (campaña %s)", campaign_id)
        raise HTTPException(
            status_code=502, detail=f"WhatsApp rechazó el envío de prueba: {exc}"
        ) from exc

    campaign.setdefault("test_sends", []).append(
        {
            "phone": session_id.removeprefix("wa_"),
            "at_ms": _now_ms(),
            "wa_message_id": getattr(result, "wa_message_id", None),
        }
    )
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    # Igual que el envío real: el contacto queda con el touch (marcado como
    # prueba, la atribución lo ignora) para que si responde el bot sepa qué
    # campaña recibió (bug 2026-09-22).
    touch = build_campaign_touch(campaign, sent_at_ms=_now_ms(), test=True)
    FilesystemMetadataStore(WORKSPACE_VAULT_DIR).update(
        session_id, lambda metadata: append_campaign_touch(metadata, touch)
    )
    return {"ok": True, "session_id": session_id}


def get_order_facts_port():
    """Provider a nivel módulo (lazy + monkeypatcheable en tests).

    El import va DENTRO: `src.sdk.connectorkit.get_order_facts_port` arrastra
    la composición de Medusa, y este módulo tiene que poder importarse sin
    vendors (gate `test_sdk_lazy_surface`).
    """
    from src.sdk.connectorkit import get_order_facts_port as _factory

    return _factory()


def _order_facts(order_ids: set[str]) -> OrderFactsSnapshot:
    """Valor canónico (Orders) de los pedidos atribuidos — pedido #31.
    Endpoint sync → cruza al event loop con `anyio.from_thread`. Falla →
    todo `unresolved` (usa la copia congelada) + `stale` (la UI avisa)."""
    if not order_ids:
        return OrderFactsSnapshot()
    try:
        return from_thread.run(get_order_facts_port().get_facts, order_ids)
    except Exception:  # noqa: BLE001
        log.exception("marketing: no pude leer OrderFacts — uso totales congelados")
        return OrderFactsSnapshot(unresolved=frozenset(order_ids), stale=True)


@router.get("/campaigns/{campaign_id}/stats")
def get_campaign_stats(campaign_id: str) -> dict:
    """Resultado del envío + atribución (respuestas / ventas) de la campaña."""
    campaign = _require_campaign(campaign_id)
    sessions = [
        (s.session_id, s.metadata)
        for s in FilesystemAttributionStore(WORKSPACE_VAULT_DIR).scan_sessions()
    ]
    order_ids = {
        ep["order_id"]
        for _sid, md in sessions
        for ep in md.get("episodes") or []
        if isinstance(ep, dict) and isinstance(ep.get("order_id"), str) and ep["order_id"]
    }
    facts = _order_facts(order_ids)
    attribution = campaign_stats(campaign, sessions, order_facts=facts)
    send_result = campaign.get("send_result") or {}
    return {
        "campaign_id": campaign_id,
        "status": campaign["status"],
        "planned": send_result.get("planned"),
        "sent": send_result.get("sent"),
        "failed_count": len(send_result.get("failed") or []),
        "skipped_count": len(send_result.get("skipped") or []),
        "unit_cost_usd_micros": send_result.get("unit_cost_usd_micros"),
        "spent_usd_micros": send_result.get("spent_usd_micros"),
        **attribution,
        "orders_stale": facts.stale,
    }


# --- Audiencia resuelta + vista de conversación (read-only) ----------------


@router.get("/campaigns/{campaign_id}/audience")
def get_campaign_audience(campaign_id: str) -> dict:
    """La audiencia REAL de la campaña, con la misma lógica del envío.

    Muestra a quién le va a llegar (nombre/teléfono/segmento) y a quién NO
    con su razón (excluido = humano/opt-out; campana_reciente = cooldown
    48h). "fuera_de_segmento" no se lista (es todo el resto del vault).
    Quiet hours NO se evalúa acá a propósito: depende de la hora del
    DISPARO, no de la de este preview.
    """
    campaign = _require_campaign(campaign_id)
    sessions = [
        (s.session_id, s.metadata)
        for s in FilesystemAttributionStore(WORKSPACE_VAULT_DIR).scan_sessions()
    ]
    audience = resolve_campaign_audience(campaign, sessions, now_ms=_now_ms())
    recipients = [
        {
            "session_id": r.session_id,
            "phone": r.session_id.removeprefix("wa_"),
            "customer_name": r.customer_name,
            "segment": r.segment,
        }
        for r in audience.recipients
    ]
    store = _store()
    campaign_names: dict[str, str | None] = {}

    def _campaign_name(cid: str | None) -> str | None:
        # La campaña que provocó la baja puede haberse borrado: None, no 500.
        if not cid:
            return None
        if cid not in campaign_names:
            found = store.get(cid)
            campaign_names[cid] = found.get("name") if found else None
        return campaign_names[cid]

    skipped = [
        {
            "session_id": s.session_id,
            "phone": s.session_id.removeprefix("wa_"),
            "reason": s.reason,
            "opted_out_at_ms": s.opted_out_at_ms,
            "opted_out_source": s.opted_out_source,
            "opted_out_campaign_id": s.opted_out_campaign_id,
            "opted_out_campaign_name": _campaign_name(s.opted_out_campaign_id),
        }
        for s in audience.skipped
        if s.reason not in ("fuera_de_segmento", SKIP_SESION_DE_PRUEBA)
    ]
    return {
        "recipients": recipients,
        "skipped": skipped,
        "opted_out_count": sum(1 for s in skipped if s["reason"] == SKIP_DADO_DE_BAJA),
        "total": len(recipients),
    }


# Anti path-traversal, no política de formato: solo wa_ + dígitos (con o sin +).
_SESSION_ID_RE = re.compile(r"^wa_\+?\d{1,20}$")
#: Handle de producto Medusa (slug): letras/dígitos/guiones/underscore.
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,120}$")
#: Cupón: solo letras y números (espejo de `COUPON_CODE_RE` del SDK; ver PUT).
_COUPON_CODE_RE = re.compile(r"^[A-Z0-9]{1,14}$")

#: Cap de mensajes que devuelve la vista (las conversaciones largas no
#: aportan al preview de campaña; el operador tiene Chats para el detalle).
_CONVERSATION_TAIL = 60


@router.get("/audience/{session_id}/conversation")
def get_audience_conversation(session_id: str) -> dict:
    """Historial simplificado de UNA sesión, para el visor de audiencia.

    Read-only sobre el JSONL del vault (misma convención de layout que lee
    la agregación de ads: `<session>/sessions/<session>.jsonl`). Parse
    tolerante: una línea corrupta se salta, jamás rompe el visor.
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=422, detail="session_id inválido")
    history_path = (
        WORKSPACE_VAULT_DIR / session_id / "sessions" / f"{session_id}.jsonl"
    )
    messages: list[dict[str, Any]] = []
    if history_path.exists():
        try:
            with history_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    role = event.get("role")
                    if role not in ("user", "assistant"):
                        continue
                    messages.append(
                        {
                            "role": role,
                            "kind": event.get("kind") or "text",
                            "content": str(event.get("content") or ""),
                            "timestamp": event.get("timestamp"),
                        }
                    )
        except OSError:
            messages = []
    return {"session_id": session_id, "messages": messages[-_CONVERSATION_TAIL:]}


@router.get("/segments")
def list_segments() -> dict:
    """Conteo por segmento sobre el vault + costo unitario EXPLÍCITO.

    El costo por mensaje es la tarifa marketing del rate card vigente
    (Colombia: $0.0125 USD). El frontend multiplica por la audiencia elegida.
    """
    counts = dict.fromkeys(ALL_SEGMENTS, 0)
    excluded = 0
    sessions = FilesystemAttributionStore(WORKSPACE_VAULT_DIR).scan_sessions()
    for session in sessions:
        if is_test_session(session.metadata):
            # Seed de Ads: la audiencia las salta, el conteo también (no son
            # contactos — incidente card 16 vs audiencia 4).
            continue
        segment = segment_for_metadata(session.metadata)
        if segment is None:
            excluded += 1
        else:
            counts[segment] += 1

    rate = get_current_rate_card().rates.get("marketing")
    unit = rate.usd_micros_per_message if rate else 0

    return {
        "segments": [
            {
                "key": key,
                "label": _SEGMENT_LABELS[key][0],
                "description": _SEGMENT_LABELS[key][1],
                "count": counts[key],
            }
            for key in ALL_SEGMENTS
        ],
        "excluded_count": excluded,
        "unit_cost_usd_micros": unit or 0,
        "currency": "USD",
    }
