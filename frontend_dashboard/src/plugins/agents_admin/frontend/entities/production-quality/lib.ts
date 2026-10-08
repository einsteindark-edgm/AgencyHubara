import type { QualityBot } from "./model";

/** Cómo se nombra cada bot en Calidad LLM (operador, 2026-10-08): Botsito es
 *  el workflow actual, sin Jev; Colossus, el workflow nuevo con Jev y el motor
 *  de decisiones. */
export const BOT_LABEL: Record<QualityBot, string> = { actual: "Botsito", nuevo: "Colossus" };
