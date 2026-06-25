/**
 * Query key factory del dominio graphagents-run. Las features y el page del
 * plugin queryean por estas keys — nunca array literals sueltos (las
 * invalidaciones del stream targetean la factory).
 */
export const graphagentsRunKeys = {
  all: ["graphagents-run"] as const,
  agents: () => [...graphagentsRunKeys.all, "agents"] as const,
  detail: (runId: string) =>
    [...graphagentsRunKeys.all, "detail", runId] as const,
} as const;
