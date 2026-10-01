"""Order draft: breadcrumb determinista de los datos del pedido.

Resuelve el problema "el agente vuelve a preguntar el color aunque el cliente
ya lo dijo". Hoy la unica memoria de slots es la instruccion de prompt
"ACUMULA EN MEMORIA" (`workspace/TOOLS.md`) + el historial conversacional del
LLM -- sin respaldo determinista. Este modulo le da respaldo: los datos que el
cliente confirma se persisten estructurados y se RE-INYECTAN en el prompt cada
turno, asi el LLM no necesita rebuscar en el historial.

Determinismo PREVENTIVO, no correctivo:
  * No es el patron de `order_registered_decision` (que corre una activity de
    red de seguridad para converger un side-effect). "Volver a preguntar" no es
    un side-effect que se pueda converger despues -- para cuando el LLM genero
    la pregunta, ya esta hecha. Asi que el mecanismo es inyectar el dato antes
    (proyeccion al prompt), no arreglar despues.
  * El DATO es 100% determinista (persistido + re-inyectado verbatim). El USO
    del dato por el LLM sigue siendo blando (tiene que leer el breadcrumb y no
    re-preguntar), pero pasa de "rebuscar 40 turnos" a "leer una tabla pineada".

ADVISORY, no fuente de la verdad:
  * El order_draft es una AYUDA-MEMORIA conversacional. La fuente de la verdad
    de la orden es lo que `register_order` manda a Medusa. NUNCA armar la orden
    desde el draft -- si divergen (draft dice "Blanco", register_order recibe
    "Azul"), gana register_order.

EPISODIO-SCOPED por construccion:
  * El draft vive DENTRO del episodio activo (`episodes[-1]["order_draft"]`), no
    en la raiz de metadata. Asi hereda el ciclo de vida del episodio gratis:
    un episodio nuevo (re-engagement) arranca sin draft -> no se proyecta ->
    NO hay leak entre episodios (el mismo bug que `build_episode_boundary_note`
    mitiga para el historial; aca se evita por construccion). Es el mismo
    mecanismo que resetea `metadata.tag` en `ensure_active_episode`.

Tres transiciones del draft:
  1. Episodio activo            -> MUTABLE (set/overwrite/clear por slot).
  2. `register_order` exitoso   -> deja de proyectarse (el episodio queda con
                                   `order_id`; la orden es la fuente de verdad).
  3. Cierre del episodio        -> congelado dentro del episodio cerrado
                                   (auditoria); el proximo episodio arranca limpio.

DEHA: funciones puras que MUTAN el dict `metadata` recibido por argumento. No
tocan filesystem ni Temporal (el caller persiste con `FilesystemMetadataStore`).
`now_ms` por DI para tests deterministicos. Mismo estilo que `episode_lifecycle`.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    ensure_active_episode,
    get_active_episode,
)
from src.plugins.chats.shared.draft_items import (
    ITEM_FIELDS,
    draft_items,
    product_key,
)

# Slots reconocidos, en orden de presentacion, con su etiqueta humana para el
# breadcrumb. `notas` es el escape-hatch para cualquier dato libre (ej. pedido
# multi-producto que no entra en los campos fijos). El set tambien acota lo que
# `SetOrderSlotTool` expone como parametros (la tool refleja estas claves).
KNOWN_SLOTS: tuple[tuple[str, str], ...] = (
    ("producto", "Producto"),
    ("aroma", "Aroma"),
    ("color", "Color"),
    ("diseno", "Diseño/Signo"),
    ("cantidad", "Cantidad"),
    ("ciudad", "Ciudad"),
    ("barrio", "Barrio"),
    ("direccion", "Direccion"),
    ("telefono", "Telefono"),
    ("nombre_recibe", "Recibe"),
    ("cedula", "Cedula (opcional)"),
    ("metodo_pago", "Metodo de pago"),
    ("notas", "Notas"),
)
_KNOWN_KEYS: frozenset[str] = frozenset(key for key, _ in KNOWN_SLOTS)
_ITEM_KEYS: frozenset[str] = frozenset(ITEM_FIELDS)


def _normalize(value: Any) -> str | None:
    """Normaliza un valor de slot a str trimmeado, o None si vacio.

    El draft es advisory/legible, asi que guardamos todo como string (incluida
    `cantidad`). `None` o string vacio significa "borrar el slot" (el cliente
    cambio de idea o lo dejo indefinido).
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def update_order_draft(
    metadata: dict[str, Any],
    *,
    slots: dict[str, Any],
    now_ms: int,
    remove_product: bool = False,
) -> dict[str, Any]:
    """Mergea `slots` en el order_draft del episodio activo. Mutates `metadata`.

    - Garantiza un episodio activo (defensivo, igual que
      `attach_order_to_active_episode`): si no hay, crea uno.
    - Campos de producto (`ITEM_FIELDS`) → al ítem de `slots["producto"]`, o
      al ítem en curso si no viene (ver `_apply_to_item`). `remove_product`
      quita el ítem de ese producto. El resto → datos del pedido.
    - Merge por slot: valor no vacio -> set/overwrite; valor vacio (`""`/`None`)
      -> remove (overwrite discipline: el cliente puede cambiar de idea).
    - Setea `updated_at_ms`.

    Returns el dict `order_draft` resultante (`{"slots": {...}, "updated_at_ms": ...}`).
    """
    episode = get_active_episode(metadata)
    if episode is None:
        episode = ensure_active_episode(metadata, now_ms=now_ms)

    draft: dict[str, Any] = episode.setdefault("order_draft", {})
    draft_slots: dict[str, Any] = draft.setdefault("slots", {})

    item_updates = {k: v for k, v in slots.items() if k in _ITEM_KEYS}
    if item_updates or "items" in draft:
        # Primera escritura de un borrador viejo (un producto en los slots
        # planos): se migra a `items` antes de tocarlo.
        items = draft_items(draft)
        draft["items"] = items
        if item_updates:
            _apply_to_item(draft, items, item_updates, remove=remove_product)
        _mirror_items_into_slots(draft_slots, items)
    slots = {k: v for k, v in slots.items() if k not in _ITEM_KEYS}

    for key, raw in slots.items():
        norm = _normalize(raw)
        if norm is None:
            draft_slots.pop(key, None)
        elif key == "notas":
            # Notas es memoria ACUMULATIVA, no un valor puntual (incidente
            # 2026-07-17 run 019f6db3: un LLM sin historial pisó las notas
            # que contenían los signos elegidos, destruyendo el pedido).
            # Append con separador; valor ya contenido no se duplica.
            # `""`/None arriba sigue limpiando (cambio de idea explícito).
            existing = draft_slots.get(key)
            if not existing:
                draft_slots[key] = norm
            else:
                # Dedupe por segmento EXACTO, no por substring (premortem
                # PR #183: "factura" ⊂ "factura urgente" dropeaba en
                # silencio una nota genuinamente nueva).
                segments = [s.strip() for s in existing.split(" | ")]
                if norm not in segments:
                    draft_slots[key] = f"{existing} | {norm}"
        else:
            draft_slots[key] = norm

    draft["updated_at_ms"] = now_ms
    return draft


#: Lo que distingue una línea de otra del mismo producto.
_VARIANT_KEYS: tuple[str, ...] = ("aroma", "color", "diseno")


def _quantity(raw: Any) -> int | None:
    """Cantidad de una línea: entero positivo, o None."""
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _lines_of(items: list[dict[str, Any]], producto: Any) -> list[dict[str, Any]]:
    """Las líneas del pedido que son de `producto`, en orden."""
    key = product_key(producto)
    return [i for i in items if key and product_key(i.get("producto")) == key]


def _differs(lines: list[dict[str, Any]], field: str) -> bool:
    """¿Las líneas tienen valores distintos en `field`? (lo que las distingue)."""
    return len({product_key(line.get(field)) for line in lines}) > 1


def _same_variant(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(product_key(a.get(k)) == product_key(b.get(k)) for k in _VARIANT_KEYS)


def set_product_lines(
    metadata: dict[str, Any],
    *,
    producto: str,
    lines: list[dict[str, Any]],
    now_ms: int,
) -> dict[str, Any]:
    """Un producto en VARIAS variantes, cada una con su cantidad. Mutates
    `metadata`.

    Laboratorio, caso 4567 (turno 22): «Mejor 2, una lila y otra azul» quedó
    como `cantidad=2`, `color=Lila` y la otra variante en `notas`, y la
    confirmación salió con dos lilas. `lines` (`{color/aroma/diseno,
    cantidad}`) reemplaza las líneas que el producto tenía, en el lugar de la
    primera. Lo que todas las líneas anteriores compartían y la línea nueva no
    dice (el aroma) se conserva. Las líneas con la misma variante se juntan
    sumando la cantidad; una sola línea es un ítem como cualquier otro.
    """
    episode = get_active_episode(metadata)
    if episode is None:
        episode = ensure_active_episode(metadata, now_ms=now_ms)
    draft: dict[str, Any] = episode.setdefault("order_draft", {})
    draft_slots: dict[str, Any] = draft.setdefault("slots", {})
    items = draft_items(draft)
    own = _lines_of(items, producto)
    name = (own[0].get("producto") if own else None) or str(producto).strip()
    shared = {
        k: own[0][k] for k in _VARIANT_KEYS if own and own[0].get(k) and not _differs(own, k)
    }
    new_lines: list[dict[str, Any]] = []
    for line in lines:
        qty = _quantity(line.get("cantidad"))
        if qty is None:
            continue
        values = {k: v for k in _VARIANT_KEYS if (v := _normalize(line.get(k))) is not None}
        variant = {**shared, **values}
        same = next((n for n in new_lines if _same_variant(n, variant)), None)
        if same is not None:
            same["cantidad"] = str(int(same["cantidad"]) + qty)
        else:
            new_lines.append({"producto": name, **variant, "cantidad": str(qty)})
    if not new_lines:
        return draft
    at = items.index(own[0]) if own else len(items)
    key = product_key(name)
    draft["items"] = (
        [i for i in items[:at] if product_key(i.get("producto")) != key]
        + new_lines
        + [i for i in items[at:] if product_key(i.get("producto")) != key]
    )
    draft["current_item"] = key
    _mirror_items_into_slots(draft_slots, draft["items"])
    draft["updated_at_ms"] = now_ms
    return draft


def line_conflicts(
    items: list[dict[str, Any]], producto: str | None, updates: dict[str, Any]
) -> list[str]:
    """Lo que no se puede escribir en un producto repartido en varias líneas
    sin decir las líneas: la variante que las distingue y una cantidad que no
    es la que ya suman. Lo que todas comparten sí (se cambia en todas). Vacío
    si el producto tiene una sola línea."""
    own = _lines_of(items, producto)
    if len(own) < 2:
        return []
    conflicts = [k for k in _VARIANT_KEYS if k in updates and _differs(own, k)]
    if "cantidad" in updates:
        total = sum(_quantity(line.get("cantidad")) or 0 for line in own)
        if _quantity(updates["cantidad"]) != total:
            conflicts.append("cantidad")
    return [k for k in ITEM_FIELDS if k in conflicts]


def line_label(line: dict[str, Any]) -> str:
    """«1× Lila · Lavanda»: la cantidad y la variante de una línea del
    borrador (el producto, si la línea no tiene variante)."""
    variant = " · ".join(str(line[k]) for k in ("color", "aroma", "diseno") if line.get(k))
    return f"{line.get('cantidad') or '?'}× {variant or line.get('producto') or ''}".strip()


def split_lines_mismatch(
    metadata: dict[str, Any],
    lines: Sequence[tuple[str, int, str | None, str | None]],
) -> list[str]:
    """Los productos repartidos en varias líneas (`set_product_lines`) que el
    pedido a confirmar o registrar no respeta: «Velón Gorrión: 1× Lila ·
    Lavanda y 1× Azul · Lavanda». `lines` trae `(producto, cantidad, color,
    aroma)` de cada línea del pedido; se compara cuántas unidades van en cada
    color y aroma, sin importar el orden. Vacío si coinciden, si ningún
    producto está repartido o si el pedido ya se registró."""
    episode = get_active_episode(metadata)
    if episode is None or episode.get("order_id"):
        return []
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in draft_items(episode.get("order_draft")):
        key = product_key(item.get("producto"))
        if key:
            groups.setdefault(key, []).append(item)
    mismatched: list[str] = []
    for key, own in groups.items():
        if len(own) < 2:
            continue
        wanted = sorted(
            (product_key(i.get("color")), product_key(i.get("aroma")), _quantity(i.get("cantidad")) or 0)
            for i in own
        )
        sent = sorted(
            (product_key(color), product_key(aroma), int(quantity))
            for title, quantity, color, aroma in lines
            if product_key(title) == key
        )
        if sent != wanted:
            mismatched.append(f"{own[0]['producto']}: " + " y ".join(line_label(i) for i in own))
    return mismatched


def get_active_draft(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """El `order_draft` crudo del episodio activo (o None)."""
    episode = get_active_episode(metadata)
    draft = (episode or {}).get("order_draft")
    return draft if isinstance(draft, dict) else None


def current_item(
    draft: dict[str, Any] | None, items: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """El ítem del que se está hablando: el último producto que se tocó."""
    if not items:
        return None
    current = (draft or {}).get("current_item")
    return next(
        (i for i in items if product_key(i.get("producto")) == current),
        items[-1],
    )


def item_for_values(current: int, accepts: Sequence[bool | None]) -> int:
    """A cuál ítem del pedido van aroma/color/diseño que llegan SIN producto
    (la regla de hoy de `set_order_slot`, capacidad `item_del_pedido`).

    `accepts[k]`: True si el producto del ítem `k` acepta todos los valores,
    False si rechaza alguno, None si no se puede validar (fuera del
    catálogo). Va al ítem en curso, salvo que su producto los rechace y otro
    ítem los acepte todos: el primero, en orden (2026-09-22: «Escorpio» se
    validaba contra la Trilogía y el Duo Zodiacal nunca entró)."""
    if accepts[current] is not False or len(accepts) < 2:
        return current
    return next((k for k, ok in enumerate(accepts) if k != current and ok is True), current)


def _apply_to_item(
    draft: dict[str, Any],
    items: list[dict[str, Any]],
    updates: dict[str, Any],
    *,
    remove: bool = False,
) -> None:
    """Escribe los campos de producto en SU ítem (mutates `items`).

    Con `producto` → el ítem de ese producto (se agrega si es nuevo; un ítem
    sin producto todavía lo adopta). Sin `producto` → el ítem en curso (el
    último que se tocó), que es del que se está hablando. `remove` quita el
    ítem del producto (el cliente lo descartó o lo cambió por otro).
    """
    producto = _normalize(updates.get("producto"))
    target: dict[str, Any] | None = None
    if producto is not None:
        key = product_key(producto)
        target = next(
            (i for i in items if product_key(i.get("producto")) == key), None
        )
        if target is not None:
            # Mismo producto escrito distinto: conserva el nombre que ya tenía.
            updates = {k: v for k, v in updates.items() if k != "producto"}
            if remove:
                # Todas sus líneas (un producto repartido en variantes).
                items[:] = [i for i in items if product_key(i.get("producto")) != key]
                if draft.get("current_item") == key:
                    draft["current_item"] = (
                        product_key(items[-1].get("producto")) if items else ""
                    )
                return
        elif remove:
            return
        else:
            target = next((i for i in items if not i.get("producto")), None)
    else:
        target = current_item(draft, items)
    if target is None:
        target = {}
        items.append(target)
    # Producto repartido en varias líneas (`set_product_lines`): lo que todas
    # comparten se escribe en todas; la variante que las distingue y la
    # cantidad solo cambian diciendo las líneas (la tool lo rechaza antes).
    # La cantidad nunca se copia en cada línea: la que ya suman no cambia
    # nada y otra sería 2 + 2.
    targets = _lines_of(items, target.get("producto")) or [target]
    if len(targets) > 1:
        blocked = {"cantidad", *line_conflicts(items, target.get("producto"), updates)}
        updates = {k: v for k, v in updates.items() if k not in blocked}
    for line in targets:
        for key, raw in updates.items():
            norm = _normalize(raw)
            if norm is None:
                line.pop(key, None)
            else:
                line[key] = norm
    items[:] = [i for i in items if i]
    draft["current_item"] = product_key(target.get("producto"))


def _mirror_items_into_slots(
    draft_slots: dict[str, Any], items: list[dict[str, Any]]
) -> None:
    """La vista plana de siempre, derivada de `items`.

    Un producto → los slots quedan IDÉNTICOS a la forma previa al 2026-09-23.
    Varios → `producto` junta los nombres ("A + B") para los lectores que solo
    preguntan si hay producto o necesitan una etiqueta; las variantes viven
    solo en `items` (a nivel pedido serían ambiguas).
    """
    for key in ITEM_FIELDS:
        draft_slots.pop(key, None)
    if len(items) == 1:
        draft_slots.update(items[0])
    elif items:
        # Un producto repartido en varias líneas se nombra una vez.
        names: dict[str, str] = {}
        for item in items:
            if item.get("producto"):
                names.setdefault(product_key(item["producto"]), item["producto"])
        if names:
            draft_slots["producto"] = " + ".join(names.values())


def get_projectable_draft(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Devuelve los slots del draft del episodio activo SI son proyectables.

    Gate (los tres deben cumplirse):
      1. Hay episodio ACTIVO (no cerrado). Un episodio nuevo de re-engagement
         no tiene draft -> None -> NO leak entre episodios.
      2. El episodio NO tiene `order_id`. Post-`register_order` la orden es la
         fuente de verdad; el draft deja de proyectarse.
      3. El draft tiene slots no vacios.

    Returns el dict de slots (`{"color": "Blanco", ...}`) o `None`. Con varios
    productos trae además `items` (un dict por producto con sus variantes);
    con uno solo es la forma plana de siempre.
    """
    episode = get_active_episode(metadata)
    if episode is None:
        return None
    if episode.get("order_id"):
        return None
    draft = episode.get("order_draft")
    if not isinstance(draft, dict):
        return None
    slots = draft.get("slots")
    if not isinstance(slots, dict) or not slots:
        return None
    items = draft_items(draft)
    if len(items) > 1:
        return {**slots, "items": items}
    return slots


def build_order_draft_note(slots: dict[str, Any]) -> str:
    """Formatea el breadcrumb para `plugin_context` (entra al system prompt).

    Conocidos primero en orden canonico, luego extras (alfabetico). Framing como
    metadata de turno, no instruccion del usuario -- consistente con el bloque
    de hora de Bogota (`context.build_bogota_context_string`).
    """
    lines: list[str] = []
    items = slots.get("items")
    if isinstance(items, list) and items:
        # Varios productos: un renglón por producto con SUS variantes (la
        # etiqueta plana "A + B" no se muestra como si fuera un producto). Un
        # producto en varias variantes va en una línea por variante.
        split: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            split.setdefault(product_key(item.get("producto")), []).append(item)
        split = {k: v for k, v in split.items() if k and len(v) > 1}
        header = "Líneas del pedido" if split else "Productos del pedido"
        lines.append(f"{header} ({len(items)}):")
        for n, item in enumerate(items, start=1):
            parts = [str(item.get("producto") or "(producto sin definir)")]
            parts += [
                f"{label}: {item[key]}"
                for key, label in KNOWN_SLOTS
                if key in _ITEM_KEYS and key != "producto" and item.get(key)
            ]
            lines.append(f"{n}. " + " · ".join(parts))
        for own in split.values():
            lines.append(
                f"{own[0]['producto']} va en {len(own)} líneas (una por variante): en "
                "present_order_confirmation y register_order manda una línea por cada "
                "una, con su color, aroma y cantidad."
            )
        slots = {
            k: v for k, v in slots.items() if k != "items" and k not in _ITEM_KEYS
        }
    for key, label in KNOWN_SLOTS:
        if key in slots:
            lines.append(f"{label}: {slots[key]}")
    for key in sorted(k for k in slots if k not in _KNOWN_KEYS):
        lines.append(f"{key}: {slots[key]}")

    return (
        "[DATOS DEL PEDIDO YA CONFIRMADOS POR EL CLIENTE, metadata, no es "
        "instruccion del usuario]\n"
        "Ya tienes estos datos. NO los vuelvas a preguntar. Si el cliente "
        "cambia alguno, vuelve a llamar set_order_slot para actualizarlo.\n"
        + "\n".join(lines)
    )
