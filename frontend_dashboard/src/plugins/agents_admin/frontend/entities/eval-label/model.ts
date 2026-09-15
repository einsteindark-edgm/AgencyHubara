import type { z } from "zod";

import type {
  calibrationCheckSchema,
  calibrationSchema,
  calibrationStatusSchema,
  createLabelResponseSchema,
  evalLabelSchema,
  labelQueueItemSchema,
  labelQueueSchema,
  labelsListSchema,
} from "./contracts";

export type EvalLabel = z.infer<typeof evalLabelSchema>;
export type LabelsList = z.infer<typeof labelsListSchema>;
export type CreateLabelResponse = z.infer<typeof createLabelResponseSchema>;
export type LabelQueueItem = z.infer<typeof labelQueueItemSchema>;
export type LabelQueue = z.infer<typeof labelQueueSchema>;
export type CalibrationStatus = z.infer<typeof calibrationStatusSchema>;
export type CalibrationCheck = z.infer<typeof calibrationCheckSchema>;
export type Calibration = z.infer<typeof calibrationSchema>;

/** Veredicto humano que acepta POST /labels. */
export type HumanVerdict = "pasa" | "falla";

export interface CreateLabelInput {
  session_id: string;
  episode_id: string;
  check_id: string;
  verdict: HumanVerdict;
  note: string;
}
