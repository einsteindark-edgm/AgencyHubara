import type { z } from "zod";

import type {
  evalTrendPointSchema,
  evalTrendSchema,
  evalTrendSeriesSchema,
} from "./contracts";

export type EvalTrend = z.infer<typeof evalTrendSchema>;
export type EvalTrendSeries = z.infer<typeof evalTrendSeriesSchema>;
export type EvalTrendPoint = z.infer<typeof evalTrendPointSchema>;
