/** El `detail` del backend cuando existe (explica el porqué del rechazo, p.ej.
 *  la ventana 24h cerrada); si no, el mensaje del error. */
export function apiErrorDetail(error: unknown): string {
  const body = (error as { body?: unknown } | null)?.body;
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  return error instanceof Error ? error.message : "error desconocido";
}
