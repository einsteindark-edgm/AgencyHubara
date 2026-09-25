import type { z } from "zod";

import type { perceptionModeSchema, rolloutCheckSchema, rolloutSchema } from "./contracts";

export type PerceptionMode = z.infer<typeof perceptionModeSchema>;
export type RolloutCheck = z.infer<typeof rolloutCheckSchema>;
export type Rollout = z.infer<typeof rolloutSchema>;

export interface RolloutChange {
  mode: PerceptionMode;
  canary_percent?: number;
  test_numbers?: string[];
}
