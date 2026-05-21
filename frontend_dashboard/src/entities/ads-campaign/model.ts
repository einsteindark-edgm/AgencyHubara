/**
 * Entidad "ads-campaign" — campañas Meta Ads → WhatsApp.
 *
 * Modela una campaña activa/pausada con sus métricas Meta (impresiones, clics,
 * inversión) + el embudo WhatsApp (chats por estado conversacional + ingresos
 * atribuidos). Los plugins solo leen este modelo; el avance de la campaña
 * (cambios de estado) es responsabilidad del backend Meta sync (fuera de scope
 * del plugin frontend-only de hoy).
 */

/* ── Estados de conversación específicos del embudo de ads ──────────────── */

export type AdsState =
  | "no_reply"
  | "nuevo"
  | "activo"
  | "calificado"
  | "cotizado"
  | "ganado"
  | "perdido";

export interface AdsStateMeta {
  key: AdsState;
  label: string;
  color: string;
  bg: string;
}

export const ADS_STATES: Record<AdsState, AdsStateMeta> = {
  no_reply:   { key: "no_reply",   label: "Sin respuesta",    color: "#8e8e93", bg: "rgba(142,142,147,0.22)" },
  nuevo:      { key: "nuevo",      label: "Nuevo",            color: "#5fa9ff", bg: "rgba(95,169,255,0.22)" },
  activo:     { key: "activo",     label: "En conversación",  color: "#5fdcff", bg: "rgba(95,220,255,0.22)" },
  calificado: { key: "calificado", label: "Calificado",       color: "#d68aff", bg: "rgba(214,138,255,0.22)" },
  cotizado:   { key: "cotizado",   label: "Cotizado",         color: "#ffb44a", bg: "rgba(255,180,74,0.22)" },
  ganado:     { key: "ganado",     label: "Ganado",           color: "#5be07b", bg: "rgba(91,224,123,0.22)" },
  perdido:    { key: "perdido",    label: "Perdido",          color: "#ff7269", bg: "rgba(255,114,105,0.22)" },
};

export const ADS_STATE_ORDER: AdsState[] = [
  "no_reply",
  "nuevo",
  "activo",
  "calificado",
  "cotizado",
  "ganado",
  "perdido",
];

/* ── Campaña ───────────────────────────────────────────────────────────── */

export type CampaignStatus = "active" | "paused";
export type CampaignTendency = "up" | "flat" | "down";

export type AdsConversationCounts = Record<AdsState, number>;

export interface AdsCampaign {
  id: string;
  name: string;
  status: CampaignStatus;
  objective: string;
  placement: string;
  audience: string;
  /** Rango formateado para mostrar tal cual ("1 may → 14 may 2026"). */
  dates: string;
  daysRun: number;
  metaCampaignId: string;
  adSet: string;
  creativeTitle: string;
  template: string;
  /** Inversión en COP. */
  spend: number;
  impressions: number;
  reach: number;
  clicks: number;
  /** Chats iniciados (CTWA). */
  started: number;
  conversations: AdsConversationCounts;
  /** Ingresos atribuidos en COP. */
  revenue: number;
  /** Ticket promedio en COP. */
  avgTicket: number;
  /** Tiempo a 1ª respuesta del bot (texto formateado tipo "2m 14s"). */
  firstResp: string;
  tendency: CampaignTendency;
}

/* ── Conversaciones atribuidas (WhatsApp chats originados por un anuncio) ── */

export type AvatarColor = "a" | "b" | "c" | "d" | "e" | "f";

export interface AttributedConversation {
  id: string;
  name: string;
  /** Iniciales para el avatar (2 letras). */
  short: string;
  color: AvatarColor;
  city: string;
  state: AdsState;
  /** Valor estimado en COP (0 si no aplica). */
  value: number;
  /** "Sofía", "Diego", "IA Soporte", "—". */
  agent: string;
  /** "Hoy 11:42", "Ayer 19:02", "9 may". */
  started: string;
  /** "Hoy 12:18", "—". */
  lastMsg: string;
  msgs: number;
  /** Nombre del anuncio que originó el chat (para multi-campaña). */
  ad: string;
}

/* ── Serie diaria — conversaciones iniciadas por día/estado final ────────── */

export interface AdsDailyPoint {
  /** Etiqueta del día ("12 may"). */
  d: string;
  ganado: number;
  cotizado: number;
  calificado: number;
  activo: number;
  nuevo: number;
  no_reply: number;
  perdido: number;
}

/* ── Mock data ──────────────────────────────────────────────────────────── */

export const CAMPAIGNS: AdsCampaign[] = [
  {
    id: "c-velas-madre",
    name: "Velas vainilla · Día de la Madre",
    status: "active",
    objective: "Mensajes (CTWA)",
    placement: "Reels · Feed · Stories",
    audience: "Mujeres 28-55 · Bogotá, Medellín, Cali",
    dates: "1 may → 14 may 2026",
    daysRun: 12,
    metaCampaignId: "120203928401234",
    adSet: "Pack 6 — Adquisición",
    creativeTitle: "Regala aroma este 12 de mayo",
    template: "promo_dia_madre_v3",
    spend: 1840000,
    impressions: 184320,
    reach: 96420,
    clicks: 4128,
    started: 612,
    conversations: { no_reply: 184, nuevo: 92, activo: 138, calificado: 87, cotizado: 54, ganado: 41, perdido: 16 },
    revenue: 6420000,
    avgTicket: 156600,
    firstResp: "2m 14s",
    tendency: "up",
  },
  {
    id: "c-pack-premium",
    name: "Pack Premium 6 unidades",
    status: "active",
    objective: "Mensajes (CTWA)",
    placement: "Feed · Stories",
    audience: "25-45 · Colombia",
    dates: "5 may → 18 may 2026",
    daysRun: 8,
    metaCampaignId: "120203928455891",
    adSet: "Premium — interés aromas",
    creativeTitle: "Tu próxima velada cuesta menos",
    template: "promo_pack_premium",
    spend: 920000,
    impressions: 88200,
    reach: 51200,
    clicks: 1840,
    started: 244,
    conversations: { no_reply: 81, nuevo: 38, activo: 51, calificado: 32, cotizado: 22, ganado: 14, perdido: 6 },
    revenue: 2184000,
    avgTicket: 156000,
    firstResp: "3m 02s",
    tendency: "up",
  },
  {
    id: "c-remarketing",
    name: "Remarketing carrito 7d",
    status: "active",
    objective: "Mensajes (CTWA)",
    placement: "Feed · Reels",
    audience: "Visitantes web 7d sin compra",
    dates: "20 abr → continuo",
    daysRun: 22,
    metaCampaignId: "120203928309887",
    adSet: "Recupero · catálogo dinámico",
    creativeTitle: "Aún tenemos tu pedido",
    template: "remarketing_carrito_7d",
    spend: 480000,
    impressions: 32100,
    reach: 14800,
    clicks: 1124,
    started: 198,
    conversations: { no_reply: 42, nuevo: 18, activo: 39, calificado: 28, cotizado: 25, ganado: 38, perdido: 8 },
    revenue: 4180000,
    avgTicket: 110000,
    firstResp: "1m 48s",
    tendency: "up",
  },
  {
    id: "c-bogota-envio",
    name: "Bogotá · Envío gratis fin de semana",
    status: "active",
    objective: "Mensajes (CTWA)",
    placement: "Stories · Reels",
    audience: "Bogotá 22-40",
    dates: "8 may → 12 may 2026",
    daysRun: 5,
    metaCampaignId: "120203928466721",
    adSet: "Envío gratis · CTA fin de semana",
    creativeTitle: "Envío gratis este fin de semana en Bogotá",
    template: "envio_gratis_bog",
    spend: 320000,
    impressions: 41800,
    reach: 22600,
    clicks: 920,
    started: 134,
    conversations: { no_reply: 38, nuevo: 21, activo: 28, calificado: 19, cotizado: 12, ganado: 11, perdido: 5 },
    revenue: 1380000,
    avgTicket: 125400,
    firstResp: "2m 51s",
    tendency: "flat",
  },
  {
    id: "c-aromaterapia",
    name: "Aromaterapia · Descubre tu aroma",
    status: "paused",
    objective: "Mensajes (CTWA)",
    placement: "Feed",
    audience: "Lookalike 1% — compradores",
    dates: "15 abr → 28 abr 2026",
    daysRun: 14,
    metaCampaignId: "120203927845112",
    adSet: "LAL 1% · aromas suaves",
    creativeTitle: "Encuentra el aroma que te describe",
    template: "test_aroma_quiz",
    spend: 1240000,
    impressions: 112000,
    reach: 64500,
    clicks: 2210,
    started: 318,
    conversations: { no_reply: 142, nuevo: 0, activo: 22, calificado: 41, cotizado: 28, ganado: 67, perdido: 18 },
    revenue: 6820000,
    avgTicket: 101800,
    firstResp: "4m 11s",
    tendency: "down",
  },
  {
    id: "c-cliente-nuevo",
    name: "Cliente nuevo · -20% primera compra",
    status: "active",
    objective: "Mensajes (CTWA)",
    placement: "Feed · Stories · Reels",
    audience: "Sin compra · 18-50",
    dates: "1 may → continuo",
    daysRun: 12,
    metaCampaignId: "120203928412009",
    adSet: "Adquisición · descuento bienvenida",
    creativeTitle: "Tu primer aroma con 20% off",
    template: "bienvenida_20off",
    spend: 1120000,
    impressions: 96500,
    reach: 58200,
    clicks: 2680,
    started: 411,
    conversations: { no_reply: 128, nuevo: 64, activo: 89, calificado: 58, cotizado: 38, ganado: 28, perdido: 6 },
    revenue: 3360000,
    avgTicket: 120000,
    firstResp: "2m 27s",
    tendency: "up",
  },
];

export const ATTRIBUTED_CONVERSATIONS: AttributedConversation[] = [
  { id: "wa-7821", name: "María Camila Restrepo",  short: "MR", color: "a", city: "Bogotá",      state: "ganado",     value: 198000, agent: "Sofía",      started: "Hoy 11:42",  lastMsg: "Hoy 12:18",  msgs: 14, ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7820", name: "Andrea Pulido",          short: "AP", color: "b", city: "Medellín",    state: "calificado", value: 156000, agent: "Sofía",      started: "Hoy 10:31",  lastMsg: "Hoy 11:50",  msgs: 9,  ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7819", name: "Daniela Ortiz",          short: "DO", color: "c", city: "Cali",        state: "cotizado",   value: 124500, agent: "Diego",      started: "Hoy 09:18",  lastMsg: "Hoy 11:22",  msgs: 11, ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7818", name: "Sara Castillo",          short: "SC", color: "d", city: "Bogotá",      state: "activo",     value: 0,      agent: "IA Soporte", started: "Hoy 08:45",  lastMsg: "Hoy 09:12",  msgs: 6,  ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7817", name: "Valentina Cárdenas",     short: "VC", color: "e", city: "Cartagena",   state: "ganado",     value: 215000, agent: "Sofía",      started: "Ayer 19:02", lastMsg: "Ayer 20:14", msgs: 16, ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7816", name: "Juliana Bedoya",         short: "JB", color: "f", city: "Bogotá",      state: "perdido",    value: 0,      agent: "Diego",      started: "Ayer 16:48", lastMsg: "Ayer 17:22", msgs: 4,  ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7815", name: "Mariana Lozano",         short: "ML", color: "a", city: "Medellín",    state: "calificado", value: 156000, agent: "Sofía",      started: "Ayer 14:18", lastMsg: "Hoy 10:01",  msgs: 12, ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7814", name: "Isabela Naranjo",        short: "IN", color: "b", city: "Bogotá",      state: "no_reply",   value: 0,      agent: "—",          started: "Ayer 12:33", lastMsg: "—",          msgs: 1,  ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7813", name: "Camila Rojas",           short: "CR", color: "c", city: "Cali",        state: "nuevo",      value: 0,      agent: "IA Soporte", started: "Ayer 11:12", lastMsg: "Ayer 11:18", msgs: 2,  ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7812", name: "Laura Vélez",            short: "LV", color: "d", city: "Bucaramanga", state: "ganado",     value: 124000, agent: "Sofía",      started: "9 may",      lastMsg: "10 may",     msgs: 18, ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7811", name: "Paula Forero",           short: "PF", color: "e", city: "Bogotá",      state: "cotizado",   value: 168000, agent: "Diego",      started: "9 may",      lastMsg: "Hoy 09:33",  msgs: 14, ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7810", name: "Sofía Acosta",           short: "SA", color: "f", city: "Cali",        state: "activo",     value: 0,      agent: "Sofía",      started: "9 may",      lastMsg: "Hoy 08:51",  msgs: 8,  ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7809", name: "Luisa Gómez",            short: "LG", color: "a", city: "Bogotá",      state: "perdido",    value: 0,      agent: "Diego",      started: "8 may",      lastMsg: "8 may",      msgs: 5,  ad: "Velas vainilla · Día de la Madre" },
  { id: "wa-7808", name: "Natalia Mejía",          short: "NM", color: "b", city: "Bogotá",      state: "ganado",     value: 184500, agent: "Sofía",      started: "8 may",      lastMsg: "9 may",      msgs: 22, ad: "Velas vainilla · Día de la Madre" },
];

export const DAILY_SERIES: AdsDailyPoint[] = [
  { d: "29 abr", ganado: 2, cotizado: 3, calificado: 4,  activo: 5,  nuevo: 2, no_reply: 8,  perdido: 1 },
  { d: "30 abr", ganado: 3, cotizado: 4, calificado: 5,  activo: 6,  nuevo: 3, no_reply: 11, perdido: 2 },
  { d: "1 may",  ganado: 4, cotizado: 5, calificado: 7,  activo: 8,  nuevo: 4, no_reply: 14, perdido: 2 },
  { d: "2 may",  ganado: 3, cotizado: 4, calificado: 6,  activo: 9,  nuevo: 3, no_reply: 12, perdido: 1 },
  { d: "3 may",  ganado: 5, cotizado: 6, calificado: 8,  activo: 10, nuevo: 4, no_reply: 18, perdido: 2 },
  { d: "4 may",  ganado: 4, cotizado: 5, calificado: 6,  activo: 8,  nuevo: 3, no_reply: 13, perdido: 1 },
  { d: "5 may",  ganado: 6, cotizado: 7, calificado: 9,  activo: 11, nuevo: 5, no_reply: 16, perdido: 2 },
  { d: "6 may",  ganado: 5, cotizado: 6, calificado: 8,  activo: 9,  nuevo: 4, no_reply: 14, perdido: 2 },
  { d: "7 may",  ganado: 4, cotizado: 5, calificado: 7,  activo: 10, nuevo: 4, no_reply: 12, perdido: 1 },
  { d: "8 may",  ganado: 6, cotizado: 7, calificado: 10, activo: 12, nuevo: 6, no_reply: 19, perdido: 2 },
  { d: "9 may",  ganado: 5, cotizado: 6, calificado: 9,  activo: 13, nuevo: 5, no_reply: 17, perdido: 2 },
  { d: "10 may", ganado: 4, cotizado: 5, calificado: 7,  activo: 11, nuevo: 4, no_reply: 14, perdido: 1 },
  { d: "11 may", ganado: 7, cotizado: 8, calificado: 11, activo: 14, nuevo: 7, no_reply: 21, perdido: 2 },
  { d: "12 may", ganado: 6, cotizado: 7, calificado: 10, activo: 12, nuevo: 6, no_reply: 18, perdido: 2 },
];

/* ── Helpers de dominio ─────────────────────────────────────────────────── */

/** Suma todos los estados conversacionales de una campaña. */
export function totalConversations(c: AdsCampaign): number {
  return ADS_STATE_ORDER.reduce((acc, s) => acc + (c.conversations[s] || 0), 0);
}
