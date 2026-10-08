import type { z } from "zod";

import type {
  conversationRowSchema,
  conversationsSchema,
  engineDecisionSchema,
  episodeEvaluationSchema,
  evalResultSchema,
  evaluationsSchema,
  jevReportSchema,
  threadSchema,
  threadTurnSchema,
  turnTraceSchema,
} from "./contracts";

export type QualityConversationRow = z.infer<typeof conversationRowSchema>;
export type QualityConversations = z.infer<typeof conversationsSchema>;
export type QualityThread = z.infer<typeof threadSchema>;
export type QualityThreadTurn = z.infer<typeof threadTurnSchema>;
export type QualityTurnTrace = z.infer<typeof turnTraceSchema>;
export type QualityEngineDecision = z.infer<typeof engineDecisionSchema>;
export type QualityEvalResult = z.infer<typeof evalResultSchema>;
export type QualityEpisodeEvaluation = z.infer<typeof episodeEvaluationSchema>;
export type QualityEvaluations = z.infer<typeof evaluationsSchema>;
export type JevReport = z.infer<typeof jevReportSchema>;

/** El bot que respondió: `actual` es Botsito; `nuevo`, Colossus (el workflow nuevo, con Jev). */
export type QualityBot = "actual" | "nuevo";
