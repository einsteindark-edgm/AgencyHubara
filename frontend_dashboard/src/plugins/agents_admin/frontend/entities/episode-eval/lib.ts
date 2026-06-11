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
