/**
 * Clave única de una conversación evaluada: `<session>::<episode>`.
 *
 * Espeja el unit id del backend (`EPISODE_SEP = "::"` en reconstruct.py) — así el
 * frontend identifica un episodio con la misma forma con la que el harness lo
 * evaluó. `episode_id` vacío = sesión entera (evaluación legacy pre-episodio).
 */
export function episodeUnitKey(c: { session_id: string; episode_id: string }): string {
  return `${c.session_id}::${c.episode_id}`;
}

/**
 * ¿Un episodio amerita alerta? — `last_avg < umbral` (score genuinamente bajo)
 * O es candidato a golden (`is_candidate` incluye fallas de métrica CRÍTICA como
 * alucinación, que el promedio podía esconder — caso ep_010: avg 0.72 pero
 * inventó un color).
 *
 * Reemplaza el viejo `!last_passed` ("falló cualquiera de las 9 métricas
 * estrictas"), que era ruido: un happy path de 0.96 con el tono apenas bajo
 * marcaba "falla" sin ser un problema (caso ep_011). UNA definición compartida
 * por el badge de alerta y el filtro "solo con fallas" — espeja
 * `is_flagged_conversation` del backend (que ordena la lista con el mismo criterio).
 */
export function isFlaggedEpisode(
  c: { last_avg: number | null; is_candidate: boolean },
  threshold: number,
): boolean {
  return (c.last_avg !== null && c.last_avg < threshold) || c.is_candidate;
}
