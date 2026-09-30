/**
 * Las decisiones del motor del bot nuevo en frases (revisión 2026-09-29): el
 * operador no entendía `{"deferral":null,"courtesy":false}`, `—, text` ni
 * `p 0,91`. Cada capacidad dice QUÉ decidió («Le avisa al modelo que no
 * vendemos: «jesús»») y cada pregunta de Jev se lee como pregunta con su
 * respuesta y qué tan seguro estaba («¿Algún mensaje ya saluda? no (94 %)»).
 *
 * El significado de cada valor sale del código del motor
 * (`sales/decisions/capabilities/*.py`, `decisions/egress.py`). Ojo con los
 * que se leen al revés: en `saludo`, `true` es «hay que AGREGAR la
 * bienvenida» y la pregunta es «¿ya saluda?»; en `destinatario`, `true` es
 * «NO es para el cliente»; en `fuera_de_catalogo` Jev solo confirma que el
 * término es algo que el cliente pide — que no esté en el catálogo lo afirma
 * la regla. Una capacidad que no está acá se muestra con su valor tal cual.
 */

import { BOGOTA_TZ } from "@/shared/lib";

import { formatDecisionValue } from "./lib";
import type { EngineDecision } from "./model";

export type DecisionTone = "neutral" | "warn" | "bad";

type Sentence = { text: string; tone: DecisionTone };

const plain = (text: string): Sentence => ({ text, tone: "neutral" });
const warn = (text: string): Sentence => ({ text, tone: "warn" });
const bad = (text: string): Sentence => ({ text, tone: "bad" });

const DATE = new Intl.DateTimeFormat("es-CO", { weekday: "long", day: "numeric", month: "long", timeZone: BOGOTA_TZ });

/** Los datos de envío (slots del pedido). */
const SLOT_LABELS: Record<string, string> = {
  ciudad: "ciudad",
  barrio: "barrio",
  direccion: "dirección",
  telefono: "teléfono",
  nombre_recibe: "nombre de quien recibe",
  cedula: "cédula",
  metodo_pago: "método de pago",
};

const VARIANT_KIND: Record<string, [string, string]> = {
  scent: ["aroma", "aromas"],
  color: ["color", "colores"],
};

const CLOSING_TAGS: Record<string, string> = {
  CONFIRMADO_SIN_DATOS: "confirmó sin dar sus datos",
  INTERESADO: "interesado",
  RECHAZO: "rechazo",
  COMPRA_EXITOSA: "compra exitosa",
};

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function obj(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function quoted(values: unknown[]): string {
  return values.map((v) => `«${String(v)}»`).join(", ");
}

const SENTENCES: Record<string, (value: unknown) => Sentence> = {
  compra: (value) => {
    const [kind, source] = list(value);
    if (kind === "affirmation") {
      const how = source === "cart" ? " (con el carrito)" : source === "button" ? " (con el botón Confirmar)" : "";
      return plain(`Confirma la compra${how}`);
    }
    if (kind === "deferral") return plain("Aplaza la compra");
    return plain("No hay señal de compra");
  },
  retoma: (value) => {
    const v = obj(value);
    const deferral = obj(v.deferral);
    if (deferral.kind === "fecha" && typeof deferral.until_ms === "number") return plain(`Aplaza hasta el ${DATE.format(new Date(deferral.until_ms))}`);
    if (deferral.kind === "abierto") return plain("Aplaza sin fecha: no se le escribe por 7 días");
    return plain(v.courtesy === true ? "Solo cortesía: no aplaza" : "No aplaza la conversación");
  },
  baja: (value) => (value === true ? warn("Pide no recibir más mensajes") : plain("No pide la baja")),
  acuse: (value) => plain(value === true ? "Es solo un acuse a la despedida: el bot no responde" : "No es solo un acuse: el bot responde"),
  cupon: (value) => plain(value === true ? "Habla del cupón" : "No habla del cupón"),
  fuera_de_catalogo: (value) => {
    const terms = list(value);
    return terms.length
      ? warn(`Avisa al modelo que no aparece por nombre en el catálogo: ${quoted(terms)}`)
      : plain("Nada fuera del catálogo");
  },
  cantidad: (value) => {
    const n = obj(value).cantidad;
    return plain(typeof n === "number" ? `Cantidad: ${n}` : "No dio cantidad");
  },
  categoria: (value) => {
    const slug = str(obj(value).categoria);
    return plain(slug ? `Categoría: ${slug}` : "Ninguna categoría del catálogo");
  },
  familia_de_color: (value) => {
    const v = obj(value);
    if (v.estado === "resolved" && str(v.color)) return plain(`Color: ${String(v.color)}`);
    if (v.estado === "ambiguous") return warn(`Color dudoso, hay que preguntar: ${list(v.candidatas).map(String).join(", ")}`);
    if (v.estado === "not_offered") return warn("Ese color no está para este producto");
    return plain("Color no reconocido");
  },
  item_del_pedido: (value) => {
    const product = str(obj(value).producto);
    return plain(product ? `Va al producto: ${product}` : "Producto del pedido sin definir");
  },
  zona_de_envio: (value) => {
    const zone = obj(value).zona;
    return plain(zone === "bogota" ? "Envío a Bogotá" : zone === "nacional" ? "Envío nacional" : "Zona de envío sin definir");
  },
  datos: (value) => {
    const slots = list(value).map((s) => SLOT_LABELS[String(s)] ?? String(s));
    return slots.length ? warn(`No guarda ${slots.join(", ")}: el cliente no lo dio`) : plain("Guarda todos los datos");
  },
  producto_nombrado: (value) => {
    const titles = list(value);
    return plain(titles.length ? `Productos que nombra: ${titles.map(String).join(", ")}` : "No nombra ningún producto");
  },
  persona: (value) => {
    const n = list(value).length;
    return n ? warn(`Quita ${count(n, "oración que delata", "oraciones que delatan")} que es un bot`) : plain("Nada que delate que es un bot");
  },
  enumeracion: (value) => {
    const [kind, options] = list(value);
    const names = VARIANT_KIND[String(kind)];
    if (!names) return plain("No había lista de opciones");
    const n = list(options).length;
    return warn(`Cambia la lista de ${count(n, names[0], names[1])} por un selector`);
  },
  monto: (value) => {
    const n = list(value).length;
    return plain(n ? `Revisa ${count(n, "precio", "precios")} contra el catálogo` : "Ningún precio de producto que revisar");
  },
  selector: (value) => {
    const titles = list(value);
    return titles.length ? warn(`Rechaza los botones: ${titles.map(String).join(", ")}`) : plain("Los botones salen");
  },
  contactar: (value) => plain(value === true ? "No hace falta escribirle" : "Hace falta escribirle"),
  cierre: (value) => {
    const tag = str(value);
    return plain(tag ? `Cierra como ${CLOSING_TAGS[tag] ?? tag}` : "El modelo elige la etiqueta");
  },
  afirmacion: (value) => (value === true ? bad("Afirma algo sin haberlo consultado") : plain("No afirma nada sin haberlo consultado")),
  preambulo: (value) => {
    const cut = str(value);
    return cut ? warn(`Corta la muletilla «${cut}»`) : plain("No había muletilla que cortar");
  },
  destinatario: (value) => {
    if (Array.isArray(value)) {
      return value.length ? warn(`Quita ${count(value.length, "oración que no es", "oraciones que no son")} para el cliente`) : plain("Todas las oraciones son para el cliente");
    }
    return value === true ? bad("El texto no es para el cliente: se frena") : plain("El texto es para el cliente");
  },
  rescate: (value) => {
    const kept = str(value);
    return kept ? warn(`Rescata para el cliente: «${kept}»`) : bad("No quedó nada para el cliente: no sale texto");
  },
  portavelas: () => plain("Revisa lo que dice del portavelas"),
  saludo: (value) => plain(value === true ? "Agrega la bienvenida" : "No hacía falta agregar la bienvenida"),
};

/** Qué decidió la capacidad, en una frase, y si merece atención. */
export function decisionSentence(d: EngineDecision): Sentence {
  const sentence = SENTENCES[d.capability];
  // Una capacidad nueva cae al valor tal cual: no rompe la vista.
  return sentence ? sentence(d.value) : plain(formatDecisionValue(d.value));
}

// ── Las preguntas de Jev ─────────────────────────────────────────────────────

const QUESTIONS: Record<string, string> = {
  "compra.que_hace": "¿Qué hace el cliente con su mensaje?",
  "compra.pregunta_compra": "¿El asesor le había preguntado si confirma la compra?",
  "retoma.aplaza": "¿Dice que retomará más adelante?",
  "retoma.cuando": "¿Cuándo retoma?",
  "retoma.cortesia": "¿Es solo cortesía (gracias, ok, un emoji)?",
  "baja.pide": "¿Pide dejar de recibir mensajes?",
  "acuse.solo_cortesia": "¿Es solo un acuse a la despedida?",
  "cupon.habla": "¿Habla del cupón o del descuento?",
  "cantidad.pregunto": "¿El asesor preguntó cuántas unidades?",
  "cantidad.dio": "¿Qué cantidad dio?",
  "categoria.cual": "¿Qué categoría busca?",
  "color.cual": "¿Qué color pide?",
  "item.cual": "¿A qué producto del pedido se refiere?",
  "zona.cual": "¿A qué zona va el envío?",
  "producto.nombrado": "¿Qué producto nombra?",
  "enumeracion.que": "¿Qué enumera el texto?",
  "selector.elige": "¿Los botones piden elegir un producto o una variante?",
  "contactar.sobra": "¿Sobra escribirle?",
  "cierre.etiqueta": "¿Con qué etiqueta cierra?",
  "afirmacion.sin_consultar": "¿Afirma algo que requiere consultar, sin haber consultado?",
  "egreso.destinatario": "¿Para quién es el texto?",
  "egreso.saludo": "¿Algún mensaje ya saluda?",
};

/** Preguntas numeradas: `persona.2`, `egreso.oracion.1`… */
const NUMBERED: Array<[RegExp, (n: string) => string]> = [
  [/^persona\.(\d+)$/, (n) => `¿La oración ${n} delata que es un bot?`],
  [/^monto\.(\d+)$/, (n) => `¿La oración ${n} da el precio de un producto?`],
  [/^preambulo\.(\d+)$/, (n) => `¿La oración ${n} es una muletilla del modelo?`],
  [/^egreso\.oracion\.(\d+)$/, (n) => `¿Para quién es la oración ${n}?`],
  [/^egreso\.rescate\.(\d+)$/, (n) => `¿Para quién es el párrafo ${n}?`],
  [/^egreso\.portavelas\.(\d+)$/, (n) => `¿La oración ${n} promete el portavelas?`],
];

const CHOICES: Record<string, string> = {
  confirma: "confirma la compra",
  aplaza: "aplaza",
  rechaza: "rechaza",
  pregunta: "pregunta",
  da_datos: "da sus datos",
  elige: "elige",
  se_despide: "se despide",
  otro: "otra cosa",
  hoy: "hoy mismo",
  otro_dia_con_fecha: "otro día, con fecha",
  otro_dia_sin_fecha: "otro día, sin fecha",
  no_aplica: "no aplica",
  ninguna: "ninguna",
  ninguno: "ninguno",
  otra: "otra",
  ambiguo: "no está claro",
  bogota: "Bogotá",
  nacional: "resto del país",
  aromas: "aromas",
  colores: "colores",
  combinaciones_cupon: "combinaciones del cupón",
  productos: "productos",
  nada: "nada",
  mensaje_al_cliente: "para el cliente",
  razonamiento: "razonamiento del modelo",
  reporte_interno: "reporte interno",
  acuse_al_sistema: "acuse al sistema",
  deliberacion: "deliberación del modelo",
};

function percent(p: number): string {
  return `${Math.round(p * 100)} %`;
}

function questionText(d: EngineDecision, q: string): string {
  if (QUESTIONS[q]) return QUESTIONS[q];
  const term = /^fuera_de_catalogo\.termino_(\d+)$/.exec(q);
  if (term) {
    // Jev revisa los términos que propuso la regla, en su orden.
    const terms = list(d.rule !== undefined ? d.rule : d.value);
    const word = terms[Number(term[1]) - 1];
    return word !== undefined ? `¿«${String(word)}» es algo que el cliente pide o muestra?` : `¿El término ${term[1]} es algo que el cliente pide o muestra?`;
  }
  const slot = /^datos\.([a-z_]+)$/.exec(q);
  if (slot) return `¿El cliente sí dio ${SLOT_LABELS[slot[1]] ?? slot[1]}?`;
  for (const [re, label] of NUMBERED) {
    const m = re.exec(q);
    if (m) return label(m[1]);
  }
  return `${q}:`;
}

/** Cada pregunta que se le hizo a Jev, con su respuesta y qué tan seguro estaba. */
export function jevAnswers(d: EngineDecision): string[] {
  return d.answers.flatMap((a) => {
    if (!a.q) return [];
    const question = questionText(d, a.q);
    if (a.choice !== undefined) {
      const choice = CHOICES[a.choice] ?? a.choice;
      return [`${question} ${choice}${a.confidence !== undefined ? ` (${percent(a.confidence)})` : ""}`];
    }
    if (a.p !== undefined) return [`${question} ${a.p >= 0.5 ? `sí (${percent(a.p)})` : `no (${percent(1 - a.p)})`}`];
    return [];
  });
}
