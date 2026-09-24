import type { z } from "zod";

import type {
  arenaArmSchema,
  armMetricsSchema,
  armSummarySchema,
  intervalSchema,
  runDiffSchema,
  runReportSchema,
  activeRunSchema,
  activeStatusSchema,
  benchReportSchema,
  burstMessageSchema,
  checkVerdictSchema,
  conversationRowSchema,
  conversationsSchema,
  episodeEvaluationSchema,
  episodeVerdictSchema,
  estimateSchema,
  evalResultSchema,
  evaluationsSchema,
  runSchema,
  runsSchema,
  threadMessageSchema,
  threadSchema,
  threadTurnSchema,
  topicCoverageSchema,
  turnTraceSchema,
} from "./contracts";

export type EpisodeVerdict = z.infer<typeof episodeVerdictSchema>;
export type CheckVerdict = z.infer<typeof checkVerdictSchema>;
export type LabRun = z.infer<typeof runSchema>;
export type LabRuns = z.infer<typeof runsSchema>;
export type LabEstimate = z.infer<typeof estimateSchema>;
export type ActiveStatus = z.infer<typeof activeStatusSchema>;
export type ActiveRun = z.infer<typeof activeRunSchema>;
export type BenchReport = z.infer<typeof benchReportSchema>;
export type ConversationRow = z.infer<typeof conversationRowSchema>;
export type Conversations = z.infer<typeof conversationsSchema>;
export type ThreadMessage = z.infer<typeof threadMessageSchema>;
export type BurstMessage = z.infer<typeof burstMessageSchema>;
export type ThreadTurn = z.infer<typeof threadTurnSchema>;
export type LabThread = z.infer<typeof threadSchema>;
export type TurnTrace = z.infer<typeof turnTraceSchema>;
export type TopicCoverage = z.infer<typeof topicCoverageSchema>;
export type EvalResult = z.infer<typeof evalResultSchema>;
export type EpisodeEvaluation = z.infer<typeof episodeEvaluationSchema>;
export type Evaluations = z.infer<typeof evaluationsSchema>;
export type ArmSummary = z.infer<typeof armSummarySchema>;
export type Interval = z.infer<typeof intervalSchema>;
export type RunDiff = z.infer<typeof runDiffSchema>;
export type ArmMetrics = z.infer<typeof armMetricsSchema>;
export type ArenaArm = z.infer<typeof arenaArmSchema>;
export type RunReport = z.infer<typeof runReportSchema>;

/** Brazo de una corrida: A0 = producción real; A1/B/C = simulados. */
export type Arm = "A0" | "A1" | "B" | "C";

export interface LaunchInput {
  arms: string[];
  reps: number;
  bench: string;
}

export interface TurnRef {
  run: string;
  sid: string;
  turnKey: string;
  arm: string;
  rep: number;
}
