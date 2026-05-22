"""Límites Meta WhatsApp Cloud API y validadores (refresh enero 2026).

Centralizado para que los builders de `outbound.py` y el `client.py` apliquen las
mismas reglas. Si Meta sube/baja un límite, se cambia acá una sola vez.

Source: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
y https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages
"""
from __future__ import annotations

# ---------- Text body ----------
MAX_TEXT_BODY = 4096
MAX_CAPTION = 1024  # image/video/document caption

# ---------- Reply buttons (interactive.button) ----------
MAX_REPLY_BUTTONS = 3
MAX_BUTTON_TITLE = 20
MAX_BUTTON_ID = 256
MAX_BUTTON_BODY = 1024
MAX_BUTTON_FOOTER = 60
MAX_BUTTON_HEADER_TEXT = 60

# ---------- List message (interactive.list) ----------
# IMPORTANTE: Meta cap el TOTAL de rows a 10 sumadas todas las sections.
# Es un cap distinto de `MAX_LIST_ROWS_PER_SECTION` (que es el cap por
# section individual). Ignorar este límite produce error 131009 (sesión
# 10d8bd21 sufrió esto cuando armamos 11 aromas en 4 secciones).
MAX_LIST_SECTIONS = 10
MAX_LIST_ROWS_PER_SECTION = 10
MAX_LIST_ROWS_TOTAL = 10  # ← cap absoluto enforced por Meta
MAX_LIST_ROW_TITLE = 24
MAX_LIST_ROW_DESCRIPTION = 72
MAX_LIST_SECTION_TITLE = 24
MAX_LIST_BUTTON = 20
MAX_LIST_BODY = 1024
MAX_LIST_FOOTER = 60
MAX_LIST_HEADER_TEXT = 60

# ---------- CTA URL (interactive.cta_url) ----------
MAX_CTA_BUTTON = 20
MAX_CTA_BODY = 1024
MAX_CTA_FOOTER = 60

# ---------- Multi-product (interactive.product_list) ----------
MAX_PRODUCT_LIST_SECTIONS = 10
MAX_PRODUCT_LIST_ITEMS_TOTAL = 30
MAX_PRODUCT_LIST_HEADER = 60
MAX_PRODUCT_LIST_BODY = 1024
MAX_PRODUCT_LIST_FOOTER = 60

# ---------- Reaction ----------
# Meta limita: 1 reaction emoji por message. Allowlist Hubara — ver A.6.
HUBARA_REACTION_ALLOWLIST = frozenset(["🤍", "✨", "👍", "🎉", "❤️", "🙏"])

# ---------- Media (inbound limits — para validar audio/video del cliente) ----------
MAX_INBOUND_AUDIO_BYTES = 16 * 1024 * 1024
MAX_INBOUND_AUDIO_SECONDS = 60  # > → escalate_to_human (regla A.5 Hubara)
MAX_INBOUND_IMAGE_BYTES = 5 * 1024 * 1024
MAX_INBOUND_DOCUMENT_BYTES = 100 * 1024 * 1024

# ---------- Media (outbound limits) ----------
MAX_OUTBOUND_IMAGE_BYTES = 5 * 1024 * 1024
MAX_OUTBOUND_AUDIO_BYTES = 16 * 1024 * 1024
MAX_OUTBOUND_VIDEO_BYTES = 16 * 1024 * 1024
MAX_OUTBOUND_DOCUMENT_BYTES = 100 * 1024 * 1024

# ---------- Catalog batch (Graph API /items_batch) ----------
MAX_CATALOG_BATCH_ITEMS = 4000
RECOMMENDED_CATALOG_CHUNK = 1000  # margen de seguridad

# ---------- Flow ----------
MAX_FLOW_JSON_BYTES = 10 * 1024 * 1024

# ---------- Colombia bounding box (regla A.4) ----------
# Aproximado: norte de Riohacha hasta sur de Leticia / oeste Buenaventura
# hasta este Puerto Carreño.
CO_BBOX_LAT_MIN = -4.3
CO_BBOX_LAT_MAX = 13.5
CO_BBOX_LNG_MIN = -82.0
CO_BBOX_LNG_MAX = -66.8


def truncate(text: str, max_len: int) -> str:
    """Trunca con elipsis preservando UX si excede el límite Meta.

    Uso defensivo: si un caller pasa un texto largo, en vez de levantar y
    bloquear el envío, recortamos a `max_len - 1` y agregamos `…`. La
    alternativa de raisear se usa para campos críticos (button.id), no para
    texto user-facing.
    """
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1] + "…"


def paginate_list_rows(
    sections: list[dict],
    page_cap: int = MAX_LIST_ROWS_TOTAL,
) -> list[list[dict]]:
    """Divide una lista de sections en **páginas**, cada una con como
    máximo `page_cap` rows totales (default 10 — el cap de Meta).

    Estrategia (greedy first-fit):
      * Recorre sections en orden, intentando meterlas en la página actual.
      * Si la section ENTERA cabe (current_total + section_rows <= cap),
        se agrega completa — preserva la coherencia visual de la categoría.
      * Si no cabe Y la página actual ya tiene rows, **arranca una nueva
        página** con esa section (la categoría queda en una sola página
        cuando es posible).
      * Si la section por sí sola excede el cap, se parte: la primera
        parte llena la página actual hasta el cap, el resto crea pages
        de hasta `page_cap` cada una.
      * Sections vacías se ignoran.

    Devuelve `list[list[section_dict]]` — lista de páginas, cada página
    es una lista de sections. Si el total cabía en 1 página, devuelve
    `[sections]` (una sola página). Si el input es vacío, devuelve `[]`.

    Bug origen (sesión 10d8bd21): Meta rechaza listas con >10 rows
    totales. Antes recortábamos (destructivo). Ahora paginamos
    (preserva todas las opciones, dos mensajes consecutivos al cliente).

    No muta los argumentos.
    """
    if not sections:
        return []

    pages: list[list[dict]] = [[]]
    current_total = 0

    for sec in sections:
        rows = list(sec.get("rows") or [])
        if not rows:
            continue
        section_size = len(rows)

        # Caso 1: la section ENTERA cabe en la página actual.
        if current_total + section_size <= page_cap:
            pages[-1].append({**sec, "rows": rows})
            current_total += section_size
            continue

        # Caso 2: la section no cabe ENTERA pero su tamaño no excede el
        # cap por sí sola → la mandamos a una nueva página intacta.
        if section_size <= page_cap:
            # Si la página actual está vacía, no abrir una nueva — usar
            # la que ya tenemos.
            if current_total > 0:
                pages.append([])
                current_total = 0
            pages[-1].append({**sec, "rows": rows})
            current_total = section_size
            continue

        # Caso 3: la section es más grande que page_cap — hay que partirla.
        # 3a: llenar lo que queda de la página actual.
        room = page_cap - current_total
        idx = 0
        if room > 0:
            pages[-1].append({**sec, "rows": rows[:room]})
            idx = room
            current_total = page_cap
        # 3b: trocear el resto en chunks de page_cap en páginas nuevas.
        while idx < section_size:
            chunk = rows[idx : idx + page_cap]
            idx += len(chunk)
            pages.append([{**sec, "rows": chunk}])
            current_total = len(chunk)

    # Limpiar páginas vacías al final (puede pasar si el input era todo
    # sections vacías filtradas).
    pages = [p for p in pages if p]
    return pages


def cap_list_rows_total(
    sections: list[dict],
    total_cap: int = MAX_LIST_ROWS_TOTAL,
) -> list[dict]:
    """**Legacy** — usá `paginate_list_rows` en su lugar.

    Recorta destructivamente al cap. Mantenido como defensa última en
    el dispatch layer: si un intent llega con sections que suman >cap
    (porque el tool no paginó correctamente o el intent vino de otro
    path), devolvemos la primera página de `paginate_list_rows`.

    Para nuevos call-sites: usá `paginate_list_rows`.

    Si una section queda sin rows tras el recorte, se elimina del output
    (Meta rechaza sections vacías).

    Estrategia round-robin: en vez de cortar al final, intentamos preservar
    representación de TODAS las sections. Si entran 10 rows y hay 4 sections,
    primero le damos 2 a cada section (total 8), luego rellena restantes
    según el orden original. Si una section tiene menos rows que su cuota,
    el sobrante se redistribuye a las siguientes.

    No muta los argumentos. Devuelve nueva lista.
    """
    if not sections:
        return []
    total_rows = sum(len(s.get("rows") or []) for s in sections)
    if total_rows <= total_cap:
        return [s for s in sections if s.get("rows")]

    # Round-robin distribution
    remaining = total_cap
    n_sections = len(sections)
    # Cuota base por section
    base = max(1, remaining // n_sections)
    quotas = [base] * n_sections
    leftover = remaining - sum(quotas)
    # Reparto extras al inicio (primeras sections — mantenemos prioridad
    # del orden original).
    for i in range(leftover):
        quotas[i % n_sections] += 1

    # Aplicar — y redistribuir sobrante de sections con menos rows que cuota
    result: list[dict] = []
    overflow = 0
    for sec, quota in zip(sections, quotas):
        rows = list(sec.get("rows") or [])
        if not rows:
            overflow += quota
            continue
        if len(rows) >= quota + overflow:
            take = rows[: quota + overflow]
            overflow = 0
        else:
            take = rows
            overflow += quota - len(rows)
        if take:
            result.append({**sec, "rows": take})

    # Si quedó overflow, hacé una pasada extra rellenando sections que
    # tengan capacidad
    if overflow > 0:
        for idx, sec in enumerate(sections):
            if overflow <= 0:
                break
            full_rows = list(sec.get("rows") or [])
            current = result[idx]["rows"] if idx < len(result) else []
            extra = full_rows[len(current): len(current) + overflow]
            if extra and idx < len(result):
                result[idx] = {**result[idx], "rows": current + extra}
                overflow -= len(extra)

    # Sanity final cap: hard-recorte si todavía queda algo
    flat_count = 0
    capped: list[dict] = []
    for sec in result:
        rows = sec["rows"]
        room = total_cap - flat_count
        if room <= 0:
            break
        if len(rows) > room:
            capped.append({**sec, "rows": rows[:room]})
            flat_count += room
        else:
            capped.append(sec)
            flat_count += len(rows)
    return capped


def is_in_colombia(lat: float, lng: float) -> bool:
    """Verifica si una coordenada cae dentro del bounding box de Colombia.

    No es una validación geodésica precisa — es un primer filtro defensivo
    para la regla 'Solo Colombia' del A.4. Para precisión real, usar
    reverse-geocode (Nominatim/Google) sobre el resultado.
    """
    return (
        CO_BBOX_LAT_MIN <= lat <= CO_BBOX_LAT_MAX
        and CO_BBOX_LNG_MIN <= lng <= CO_BBOX_LNG_MAX
    )
