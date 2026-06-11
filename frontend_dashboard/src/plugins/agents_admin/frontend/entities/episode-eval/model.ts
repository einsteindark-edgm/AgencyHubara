import type { z } from "zod";

import type {
  conversationEvalSchema,
  conversationEvalsSchema,
  episodeEvalRunSchema,
  evalTranscriptSchema,
  evalTrendDirectionSchema,
  failedMetricSchema,
  transcriptTurnSchema,
} from "./contracts";

export type ConversationEval = z.infer<typeof conversationEvalSchema>;
export type ConversationEvals = z.infer<typeof conversationEvalsSchema>;
export type EpisodeEvalRun = z.infer<typeof episodeEvalRunSchema>;
export type EvalTranscript = z.infer<typeof evalTranscriptSchema>;
export type EvalTrendDirection = z.infer<typeof evalTrendDirectionSchema>;
export type FailedMetric = z.infer<typeof failedMetricSchema>;
export type TranscriptTurn = z.infer<typeof transcriptTurnSchema>;
