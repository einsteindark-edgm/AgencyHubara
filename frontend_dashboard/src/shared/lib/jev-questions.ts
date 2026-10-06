/**
 * Las preguntas que Jev contesta en el paso «Jev lee el mensaje» (cuestionario
 * de la ráfaga, `rafaga-v5`) y en la revisión de la respuesta (`cover.*`), en
 * palabras del operador. El panel mostraba el id del cuestionario
 * (`topic.variante`) y el operador tenía que preguntar qué quería decir
 * (2026-09-30). El id queda a mano para depurar (el `title` de la fila).
 */

/** Cada asunto del cuestionario: la pregunta y su nombre corto. */
const TOPICS: Record<string, { question: string; label: string }> = {
  catalogo: { question: "¿Pide ver el catálogo o un tipo de velas?", label: "el catálogo" },
  precio: { question: "¿Pregunta cuánto vale algo?", label: "el precio" },
  envio: { question: "¿Pregunta por el envío o el domicilio?", label: "el envío" },
  tiempos: { question: "¿Pregunta cuánto tarda en llegar o cuándo está listo?", label: "los tiempos de entrega" },
  pagos: { question: "¿Pregunta cómo pagar?", label: "los medios de pago" },
  medidas: { question: "¿Pregunta por el tamaño o las medidas?", label: "las medidas" },
  variante: { question: "¿Pregunta por colores o variantes?", label: "los colores o variantes" },
  aroma: { question: "¿Pregunta por los aromas?", label: "los aromas" },
  personalizacion: { question: "¿Pide personalizar?", label: "la personalización" },
  disponibilidad: { question: "¿Pregunta si tienen algo o si hay disponible?", label: "la disponibilidad" },
  estado_pedido: { question: "¿Pregunta por un pedido que ya hizo?", label: "el estado del pedido" },
  datos_envio: { question: "¿Entrega sus datos de envío?", label: "los datos de envío" },
  confirma_compra: { question: "¿Confirma que quiere comprar?", label: "la confirmación de compra" },
  aplaza: { question: "¿Aplaza la compra?", label: "el aplazamiento" },
  queja: { question: "¿Se queja o reporta un problema?", label: "la queja" },
  foto: { question: "¿Pregunta por una foto que envió o citó?", label: "la foto" },
  saludo: { question: "¿Saluda?", label: "el saludo" },
  promocion: { question: "¿Pregunta por promociones, descuentos o un cupón?", label: "las promociones o cupones" },
};

/** Las demás preguntas del cuestionario, por su id. */
const QUESTIONS: Record<string, string> = {
  "thread.bot_asked": "¿Qué le preguntó el asesor en su último mensaje?",
  "thread.answers_bot": "¿Responde la última pregunta del asesor?",
  "thread.answer": "¿Qué responde a la última pregunta del asesor?",
  "precio.en_contexto": "¿Ya vio el precio de lo que pregunta?",
  "envio.costo": "¿Pregunta cuánto cuesta el envío?",
  "queja.pedido_hecho": "¿Reclama por un pedido que ya hizo?",
  "variantes.elige": "¿Elige color, aroma, diseño o cantidad?",
  "etapa.cambia_producto": "¿Quiere ver otros productos o cambiar el que eligió?",
  "datos.ciudad": "¿Da la ciudad de envío?",
  "datos.direccion": "¿Da la dirección de envío?",
  "datos.telefono": "¿Da un teléfono de contacto?",
  "datos.nombre_recibe": "¿Dice quién recibe el pedido?",
  "datos.metodo_pago": "¿Dice cómo va a pagar?",
  "cierre.confirma_resumen": "¿Confirma el resumen del pedido?",
  "cierre.pide_cambio": "¿Pide cambiar algo del pedido?",
  "postcierre.pregunta_pedido": "¿Pregunta por su pedido?",
  "postcierre.comprobante": "¿Manda o menciona un comprobante de pago?",
};

/** Las opciones de las preguntas de elección (fuera de los asuntos). */
const CHOICES: Record<string, string> = {
  confirmar_compra: "si confirma la compra o el pedido",
  confirmar_dato_envio: "si confirma un dato de envío",
  elegir_variante: "que elija una variante",
  ver_opciones: "si quiere ver opciones",
  otra_si_no: "otra pregunta de sí o no",
  pregunta_abierta: "una pregunta abierta",
  nada: "nada",
  si: "sí",
  no: "no",
  otra: "otra cosa (elige algo, pregunta o no la responde)",
  ninguno: "ningún asunto de la lista",
};

function humanized(id: string): string {
  return id.split(".").map((part) => part.replace(/_/g, " ")).join(" · ");
}

/** La pregunta a Jev en palabras del operador. */
export function jevQuestionLabel(q: string): string {
  const topic = /^topic\.([a-z_]+)$/.exec(q);
  if (topic && TOPICS[topic[1]]) return TOPICS[topic[1]].question;
  const cover = /^cover\.([a-z_]+)$/.exec(q);
  if (cover && TOPICS[cover[1]]) return `¿La respuesta atiende ${TOPICS[cover[1]].label}?`;
  const message = /^msg\.(\d+)\.topic$/.exec(q);
  if (message) return `¿Cuál es el asunto principal del mensaje ${message[1]}?`;
  return QUESTIONS[q] ?? humanized(q);
}

/** La opción que eligió Jev, en palabras. En el asunto de un mensaje, el
 *  nombre del asunto. */
export function jevChoiceLabel(q: string, choice: string): string {
  if (/^msg\.\d+\.topic$/.test(q) && TOPICS[choice]) return TOPICS[choice].label.replace(/^(el|la|los|las) /, "");
  return CHOICES[choice] ?? choice.replace(/_/g, " ");
}
