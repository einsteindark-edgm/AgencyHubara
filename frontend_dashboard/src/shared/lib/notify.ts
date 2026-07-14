/**
 * Notificaciones del sistema, agnósticas de plataforma.
 *
 * En Tauri (app Android / desktop) usa `@tauri-apps/plugin-notification`
 * (import dinámico — el chunk solo se carga dentro de Tauri, el bundle web no
 * lo paga). En browser puro cae al Web Notification API. Si no hay permiso o
 * el entorno no soporta nada (jsdom), es un no-op silencioso — notificar es
 * best-effort, NUNCA rompe el flujo que la dispara.
 *
 * Android 13+: el permiso POST_NOTIFICATIONS es runtime — `requestPermission`
 * del plugin abre el diálogo del sistema la primera vez.
 */

import { IS_DESKTOP } from "./runtime";

/** Pide permiso si hace falta. Devuelve true si podemos notificar. */
export async function ensureNotificationPermission(): Promise<boolean> {
  try {
    if (IS_DESKTOP) {
      const plugin = await import("@tauri-apps/plugin-notification");
      if (await plugin.isPermissionGranted()) return true;
      return (await plugin.requestPermission()) === "granted";
    }
    if (typeof Notification === "undefined") return false;
    if (Notification.permission === "granted") return true;
    if (Notification.permission === "denied") return false;
    return (await Notification.requestPermission()) === "granted";
  } catch {
    return false;
  }
}

/** Dispara una notificación del sistema. Best-effort: nunca lanza.
 *
 * `opts.onClick` (PM2-M7): en el fallback web, tocar la notificación enfoca la
 * app Y ejecuta el callback (p.ej. abrir el chat que la disparó). En Tauri
 * Android el plugin no expone click-callback desde JS — el tap solo trae la
 * app al frente (documentado en el runbook como parte del spike de device). */
export async function sendSystemNotification(
  title: string,
  body: string,
  opts?: { onClick?: () => void },
): Promise<void> {
  try {
    if (!(await ensureNotificationPermission())) return;
    if (IS_DESKTOP) {
      const plugin = await import("@tauri-apps/plugin-notification");
      plugin.sendNotification({ title, body });
      return;
    }
    const n = new Notification(title, { body });
    if (opts?.onClick) {
      n.onclick = () => {
        try {
          window.focus();
          opts.onClick?.();
        } catch {
          /* best-effort */
        }
      };
    }
  } catch {
    // best-effort — sin permiso / entorno sin soporte, no rompemos nada.
  }
}
