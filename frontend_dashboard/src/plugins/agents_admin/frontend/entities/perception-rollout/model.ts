import type { z } from "zod";

import type {
  capabilityControlSchema,
  decisionEngineSchema,
  engineDecisionAboutSchema,
  perceptionModeSchema,
  rolloutCheckSchema,
  rolloutSchema,
  workflowControlSchema,
  workflowModeSchema,
} from "./contracts";

export type PerceptionMode = z.infer<typeof perceptionModeSchema>;
export type RolloutCheck = z.infer<typeof rolloutCheckSchema>;
export type Rollout = z.infer<typeof rolloutSchema>;

export interface RolloutChange {
  mode: PerceptionMode;
  canary_percent?: number;
  test_numbers?: string[];
}

export type CapabilityControl = z.infer<typeof capabilityControlSchema>;
export type WorkflowMode = z.infer<typeof workflowModeSchema>;
export type WorkflowControl = z.infer<typeof workflowControlSchema>;

/** Motor de decisiones (F7): mover UNA capacidad. */
export interface CapabilityChange {
  capability: string;
  mode: PerceptionMode;
}

/** Motor de decisiones (F7): mover la versión del workflow de ventas. */
export interface WorkflowChange {
  mode: WorkflowMode;
}

/** El motor de decisiones: su versión y lo que decide (2026-10-02). */
export type DecisionEngine = z.infer<typeof decisionEngineSchema>;
export type EngineDecisionAbout = z.infer<typeof engineDecisionAboutSchema>;
