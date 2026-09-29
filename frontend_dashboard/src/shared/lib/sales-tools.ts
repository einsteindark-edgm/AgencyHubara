/**
 * Las herramientas del bot de ventas en palabras del operador (revisión del
 * laboratorio 2026-09-29). El hilo del turno de Chats y del Laboratorio decía
 * `search_products`, `resultado` o `rechazada: url_not_whitelisted`; esto lo
 * dice como una acción («Buscar en el catálogo · «jesús»»), con lo que
 * encontró («no encontró nada») y, si la rechazaron, por qué.
 *
 * Función pura sobre lo que la traza trae de cada tool (`name`, `args`, `ok`,
 * `error`, `notes`). Una tool que no está en la lista sale con su nombre.
 */

export interface ToolCall {
  name: string;
  args?: unknown;
  ok?: boolean | null;
  error?: string | null;
  notes?: unknown;
}

export interface ToolDescription {
  /** La acción, en infinitivo: «Buscar en el catálogo». */
  action: string;
  /** Lo que el cliente ve, si manda un componente: «Tarjeta del producto». */
  shown: string | null;
  /** Con qué: «jesús», el producto, lo que anotó. */
  detail: string | null;
  /** Qué pasó: «no encontró nada», «8 productos», «listo» o «rechazada: …». */
  result: string;
  /** Manda algo que el cliente ve aparte del texto (tarjeta, botones, formulario). */
  component: boolean;
  failed: boolean;
}

type Args = Record<string, unknown>;

interface ToolSpec {
  action: string;
  /** Lo que ve el cliente (solo las que mandan un componente). */
  shown?: string;
  detail?: (args: Args) => string | null;
  /** Qué cuenta el `count:N` de sus notas. */
  counts?: [string, string];
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

const HANDLE = (args: Args) => text(args.handle);

/** Los valores anotados en el pedido: «producto: Velón Gorrión · color: lila». */
function slots(args: Args): string | null {
  const parts = Object.entries(args)
    .filter(([, v]) => typeof v === "string" || typeof v === "number")
    .map(([k, v]) => `${k}: ${String(v)}`);
  return parts.length ? parts.join(" · ") : null;
}

const TOOLS: Record<string, ToolSpec> = {
  search_products: {
    action: "Buscar en el catálogo",
    detail: (a) => (text(a.q) ? `«${text(a.q)}»` : text(a.category) ? `categoría ${text(a.category)}` : null),
    counts: ["producto", "productos"],
  },
  get_product_by_handle: { action: "Leer la ficha de un producto", detail: HANDLE },
  list_categories: { action: "Listar las categorías" },
  list_promotions: { action: "Consultar las promociones" },
  apply_coupon: { action: "Aplicar un cupón", detail: (a) => text(a.code) },
  check_order_status: { action: "Consultar el estado del pedido" },
  load_skill: { action: "Cargar las instrucciones de una etapa", detail: (a) => text(a.skill_name) },
  manage_conversation_tag: { action: "Etiquetar la conversación", detail: (a) => text(a.tag) },
  present_order_confirmation: { action: "Mostrar el resumen del pedido", shown: "Resumen del pedido" },
  present_product_detail: { action: "Mostrar un producto", shown: "Tarjeta del producto", detail: HANDLE },
  present_product_gallery: { action: "Mostrar una galería de fotos", shown: "Galería de fotos", detail: HANDLE },
  present_products: { action: "Mostrar productos", shown: "Lista de productos", counts: ["producto", "productos"] },
  present_variant_picker: { action: "Mostrar opciones para elegir", shown: "Selector de opciones", detail: HANDLE, counts: ["opción", "opciones"] },
  react_to_message: { action: "Reaccionar al mensaje", shown: "Reacción", detail: (a) => text(a.emoji) },
  register_order: { action: "Registrar el pedido" },
  request_shipping_details: { action: "Pedir los datos de envío (formulario)", shown: "Formulario de envío" },
  send_contact_card: { action: "Enviar un contacto", shown: "Contacto" },
  send_cta_url: { action: "Enviar un botón con enlace", shown: "Botón con enlace", detail: (a) => text(a.button_text) },
  send_quick_replies: { action: "Enviar botones de respuesta", shown: "Botones de respuesta" },
  send_reply: { action: "Responder al cliente" },
  send_shipping_rates: { action: "Enviar las tarifas de envío", shown: "Tarifas de envío" },
  set_order_slot: { action: "Anotar en el pedido", detail: slots },
  verify_order_for_checkout: { action: "Revisar el pedido antes de confirmar" },
  escalate_to_human: { action: "Pasar la conversación a una persona" },
};

/** Por qué una tool fue rechazada (códigos de las guardas de las tools). */
const REJECTIONS: Record<string, string> = {
  url_not_whitelisted: "el enlace no está permitido",
  url_blocked_pattern: "el enlace no está permitido",
  customer_deferred: "el cliente aplazó",
  purchase_not_confirmed: "el cliente no ha confirmado la compra",
  catalog_choice_not_allowed: "los botones no pueden elegir productos, aromas ni colores",
  catalog_unavailable: "el catálogo no respondió",
  handle_not_found: "ese producto no existe",
  unknown_handle: "ese producto no existe",
  design_not_found: "ese diseño no existe",
  price_mismatch: "el precio no coincide con el catálogo",
  shipping_mismatch: "el envío no coincide con la tarifa",
  invalid_variant_attribute: "esa opción no existe",
  missing_variant_attributes: "faltan el aroma o el color",
  not_enough_options: "no hay suficientes opciones",
  no_valid_buttons: "ningún botón era válido",
  order_registered: "el pedido ya estaba registrado",
  missing_receiver_name: "falta el nombre de quien recibe",
  missing_items: "faltan los productos",
  min_subtotal: "no alcanza el mínimo de compra",
  timeout: "se demoró demasiado",
};

/** La acción en minúscula, para frases como «pide buscar en el catálogo». */
export function toolActionPhrase(name: string): string {
  const action = TOOLS[name]?.action ?? name;
  return action.charAt(0).toLowerCase() + action.slice(1);
}

function count(notes: unknown): number | null {
  if (!Array.isArray(notes)) return null;
  for (const n of notes) {
    const m = typeof n === "string" ? /^count:(\d+)$/.exec(n) : null;
    if (m) return Number(m[1]);
  }
  return null;
}

export function describeTool(call: ToolCall): ToolDescription {
  const spec = TOOLS[call.name];
  const args = call.args && typeof call.args === "object" && !Array.isArray(call.args) ? (call.args as Args) : {};
  const failed = call.ok === false;
  let result = "listo";
  if (failed) {
    const code = text(call.error) ?? "error";
    result = `rechazada: ${REJECTIONS[code] ?? code}`;
  } else {
    const n = count(call.notes);
    if (spec?.counts && n !== null) {
      result = n === 0 && call.name === "search_products" ? "no encontró nada" : `${n} ${n === 1 ? spec.counts[0] : spec.counts[1]}`;
    }
  }
  return {
    action: spec?.action ?? call.name,
    shown: spec?.shown ?? null,
    detail: spec?.detail ? spec.detail(args) : null,
    result,
    component: spec?.shown !== undefined,
    failed,
  };
}
