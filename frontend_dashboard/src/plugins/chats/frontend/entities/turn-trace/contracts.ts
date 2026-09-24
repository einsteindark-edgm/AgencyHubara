import { z } from "zod";

/**
 * Hilo de un turno del bot — `GET /api/chats/sessions/{sesión}/turns/trace`
 * (plan del laboratorio PR 17). Misma forma que el hilo del Laboratorio: la
 * traza v2 con sus pasos en orden (la ráfaga primero), o la v1 sintetizada
 * sin tiempos. Los pasos se pasan tal cual al diagrama de `@/shared/ui`.
 */
const stepSchema = z
  .object({
    i: z.number().optional(),
    at_ms: z.number().nullable().catch(null).default(null),
    kind: z.string().catch("desconocido"),
    dur_ms: z.number().nullable().optional().catch(null),
  })
  .passthrough();

/** Un paso roto se descarta solo: no se lleva el resto del hilo (L-10). */
const stepsSchema = z
  .array(z.unknown())
  .catch([])
  .transform((items) =>
    items.flatMap((item) => {
      const step = stepSchema.safeParse(item);
      return step.success ? [step.data] : [];
    }),
  );

export const turnThreadSchema = z.object({
  fidelity: z.enum(["v1", "v2"]).catch("v1"),
  trace: z.record(z.string(), z.unknown()).catch({}).default({}),
  steps: stepsSchema.default([]),
});
