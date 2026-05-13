import { useQuery } from "@tanstack/react-query";
import { trackedOrderKeys } from "./keys";
import type { TrackedOrder } from "./model";

const TRACKED: TrackedOrder[] = [
  {
    id: "#1247", customer: "María Camila Restrepo", short: "MR", color: "a", city: "Bogotá · Chapinero",
    current: "shipping", channel: "WhatsApp", needs: true, payType: "confirmed", total: 124500, messagesUnread: 1,
    events: [
      { stage: "preparing", time: "08:15", date: "hoy",   note: "3 productos. Empacado en bolsa kraft.", agentMsg: "¡Hola María Camila! Soy el asistente de seguimiento. Tu pedido #1247 acaba de entrar en preparación. Tu pago ya está confirmado, así que cuando llegue el paquete solo tienes que recibirlo 🙌", reply: "¡Perfecto, gracias!" },
      { stage: "ready",     time: "10:42", date: "hoy",   note: "Listo. Empaque para regalo confirmado.", agentMsg: "¡Buenas noticias! Tu pedido ya está empacado y listo para que la transportadora pase a recogerlo. Te escribo apenas salga a ruta.", reply: "Gracias 💛 ¿cuándo lo recogen?" },
      { stage: "shipping",  time: "11:20", date: "hoy",   note: "Coordinadora · guía 215XXX.", agentMsg: "Tu pedido salió hace un momento con Coordinadora. Guía 215XXX1129. Llega hoy entre 14:00 y 17:00. Recuerda que tu pedido ya está pagado — no tienes que hacer ningún pago al recibirlo.", reply: "Hola, una pregunta — ¿puedo cambiar la dirección a Cra 13 #94-30? Me cambiaron de oficina", flagged: true, flag: "Cliente solicitó cambio de dirección después de despacho" },
    ],
  },
  {
    id: "#1246", customer: "Carlos Andrés Vélez", short: "CV", color: "b", city: "Medellín · El Poblado",
    current: "preparing", channel: "Web", needs: false, payType: "confirmed", total: 78000, messagesUnread: 0,
    events: [
      { stage: "preparing", time: "09:02", date: "hoy", note: "En cola de empaque (2 órdenes antes).", agentMsg: "¡Hola Carlos! Tu pedido #1246 entró en preparación. Tu pago ya está confirmado. Te aviso apenas esté empacado.", reply: null },
    ],
  },
  {
    id: "#1244", customer: "Andrés Felipe Torres", short: "AT", color: "d", city: "Bogotá · Usaquén",
    current: "ready", channel: "WhatsApp", needs: false, payType: "confirmed", total: 182000, messagesUnread: 0,
    events: [
      { stage: "preparing", time: "07:30", date: "hoy", note: "Pedido grande · 4 productos.", agentMsg: "¡Hola Andrés! Tu pedido #1244 está en preparación. Pago confirmado. Te aviso apenas esté listo.", reply: "Genial" },
      { stage: "ready",     time: "10:18", date: "hoy", note: "Listo. Pendiente coordinar con transportadora.", agentMsg: "Tu pedido está empacado. Despachamos hoy mismo antes de las 18:00. Recuerda que ya está pagado.", reply: null },
    ],
  },
  {
    id: "#1243", customer: "Daniela Ortiz Pérez", short: "DO", color: "e", city: "Barranquilla · Norte",
    current: "shipping", channel: "Web", needs: true, payType: "cod", total: 215000, messagesUnread: 2,
    events: [
      { stage: "preparing", time: "07:45", date: "ayer", note: "5 productos · contra entrega.", agentMsg: "¡Hola Daniela! Tu pedido #1243 entró en preparación. Recuerda que este pedido es contra entrega: deberás pagar $ 215.000 en efectivo o transferencia cuando recibas el paquete. Te aviso en cada paso 🙌", reply: null },
      { stage: "ready",     time: "15:20", date: "ayer", note: "Empacado · confirmar monto contra entrega.", agentMsg: "Tu pedido está empacado. Sale a ruta mañana temprano. 💡 Recuerda tener listos $ 215.000 para pagar al recibir.", reply: "perfecto" },
      { stage: "shipping",  time: "08:10", date: "hoy", note: "Servientrega · guía 308XXX. Cobro contra entrega activo.", agentMsg: "Tu pedido salió esta mañana con Servientrega. Guía 308XXX2245. ⚠️ Al recibirlo deberás pagar $ 215.000 al repartidor (efectivo o transferencia). Estimado de entrega: hoy entre 11:00 y 14:00.", reply: "Hola! No he recibido nada y ya son las 4pm, ¿qué pasó? Necesito el pedido hoy sí o sí", flagged: true, flag: "Cliente reporta retraso en entrega · queja activa" },
    ],
  },
  {
    id: "#1240", customer: "Juan Sebastián Mora", short: "JM", color: "b", city: "Pereira · Centro",
    current: "preparing", channel: "WhatsApp", needs: false, payType: "confirmed", total: 96000, messagesUnread: 0,
    events: [
      { stage: "preparing", time: "06:55", date: "hoy", note: "3 productos · listo hoy.", agentMsg: "¡Hola Juan Sebastián! Tu pedido #1240 está en preparación. Pago confirmado. Te aviso aquí en cada paso.", reply: "Gracias!" },
    ],
  },
  {
    id: "#1239", customer: "Camila Rojas Suárez", short: "CR", color: "c", city: "Bogotá · Salitre",
    current: "shipping", channel: "Instagram", needs: false, payType: "cod", total: 72500, messagesUnread: 0,
    events: [
      { stage: "preparing", time: "08:00", date: "ayer", note: "Contra entrega.", agentMsg: "¡Hola Camila! Tu pedido #1239 está en preparación. Recuerda que es contra entrega: pagarás $ 72.500 al recibir.", reply: null },
      { stage: "ready",     time: "14:35", date: "ayer", note: "Listo y empacado.", agentMsg: "Tu pedido está listo. Sale mañana con Servientrega. Ten listos $ 72.500 para el pago contra entrega.", reply: "ok 👍" },
      { stage: "shipping",  time: "09:15", date: "hoy", note: "Servientrega · guía 411XXX. 💰 Cobrar $ 72.500.", agentMsg: "Tu pedido va en camino 🚚. Llega hoy entre 13:00 y 16:00. Recuerda: pagas $ 72.500 al repartidor al recibir. Guía 411XXX0987.", reply: null },
    ],
  },
  {
    id: "#1238", customer: "Felipe Quintero", short: "FQ", color: "d", city: "Manizales · La Sultana",
    current: "out", channel: "Tienda", needs: false, payType: "confirmed", total: 268000, messagesUnread: 0,
    events: [
      { stage: "preparing", time: "06:30", date: "ayer", note: "6 productos · alta prioridad.", agentMsg: "¡Hola Felipe! Tu pedido #1238 está en preparación. Pago confirmado. Pedido grande, te aviso paso a paso.", reply: "Gracias" },
      { stage: "ready",     time: "11:15", date: "ayer", note: "Listo · 14 unidades empacadas.", agentMsg: "Tu pedido está listo. Sale temprano mañana.", reply: null },
      { stage: "shipping",  time: "07:00", date: "hoy", note: "Coordinadora · guía 502XXX.", agentMsg: "Tu pedido salió esta mañana. Llega entre 11:00 y 14:00. Recuerda que ya está pagado — no tienes que pagar nada al recibirlo.", reply: null },
      { stage: "out",       time: "11:42", date: "hoy", note: "Repartidor: Andrés M.", agentMsg: "¡Tu pedido está en reparto! El repartidor llega en aprox. 30 min. Pago confirmado, solo recibe el paquete 🙌", reply: null },
    ],
  },
];

export function useTrackedOrders() {
  return useQuery({
    queryKey: trackedOrderKeys.list(),
    queryFn: async () => TRACKED,
    staleTime: Infinity,
  });
}
