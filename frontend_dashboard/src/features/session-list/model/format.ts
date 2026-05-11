/**
 * Helpers de formato locales al feature. Si otro feature los necesita,
 * promover a `shared/lib`.
 */

/** Unix epoch (segundos) → "HH:MM" en locale del usuario. */
export function formatHourMinute(unixSeconds: number): string {
  if (!unixSeconds) return "";
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
