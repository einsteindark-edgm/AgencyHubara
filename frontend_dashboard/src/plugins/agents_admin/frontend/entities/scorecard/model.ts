import type { z } from "zod";

import type { QualityStatus } from "@/shared/lib";

import type {
  checkDefinitionSchema,
  checkFamilySchema,
  checkKindSchema,
  checkLevelSchema,
  checkRegistrySchema,
  checkResultSchema,
  checkVerdictSchema,
  episodeVerdictSchema,
  failurePointSchema,
  fidelitySchema,
  legacyScoreSchema,
  scorecardDetailSchema,
  scorecardListSchema,
  scorecardRowSchema,
  scorecardWithResultsSchema,
  trajectorySchema,
  trajectoryToolSchema,
  trajectoryTurnSchema,
  verdictCountsSchema,
} from "./contracts";

export type EpisodeVerdict = z.infer<typeof episodeVerdictSchema>;
export type CheckVerdict = z.infer<typeof checkVerdictSchema>;
export type CheckLevel = z.infer<typeof checkLevelSchema>;
export type CheckKind = z.infer<typeof checkKindSchema>;
export type Fidelity = z.infer<typeof fidelitySchema>;
export type CheckFamily = z.infer<typeof checkFamilySchema>;
export type CheckDefinition = z.infer<typeof checkDefinitionSchema>;
export type CheckRegistry = z.infer<typeof checkRegistrySchema>;
export type FailurePoint = z.infer<typeof failurePointSchema>;
export type VerdictCounts = z.infer<typeof verdictCountsSchema>;
export type ScorecardRow = z.infer<typeof scorecardRowSchema>;
export type ScorecardList = z.infer<typeof scorecardListSchema>;
export type CheckResult = z.infer<typeof checkResultSchema>;
export type ScorecardWithResults = z.infer<typeof scorecardWithResultsSchema>;
export type TrajectoryTool = z.infer<typeof trajectoryToolSchema>;
export type TrajectoryTurn = z.infer<typeof trajectoryTurnSchema>;
export type Trajectory = z.infer<typeof trajectorySchema>;
export type LegacyScore = z.infer<typeof legacyScoreSchema>;
export type ScorecardDetail = z.infer<typeof scorecardDetailSchema>;

/** Estado visual de un check: la falla toma su nivel; sin resultado = no
 * evaluado; `sin_senal` solo existe en el modo turno del laboratorio. */
export type CheckStatus = QualityStatus;

/** Episodio identificado por sesión + episodio (unidad del scorecard). */
export interface EpisodeRef {
  sessionId: string;
  episodeId: string;
}
