import type { z } from "zod";

import type {
  evalCandidateDetailSchema,
  evalCandidateSummarySchema,
  evalCandidateTurnSchema,
} from "./contracts";

export type EvalCandidateSummary = z.infer<typeof evalCandidateSummarySchema>;
export type EvalCandidateDetail = z.infer<typeof evalCandidateDetailSchema>;
export type EvalCandidateTurn = z.infer<typeof evalCandidateTurnSchema>;
