/**
 * Re-export. La función original vivía acá; ahora que la consume también
 * `entities/chat` (adapter del backend → diseño macOS), se promovió a
 * `shared/lib`. Este shim mantiene los imports del feature legado.
 */

export { formatHourMinute } from "@/shared/lib";
