import type { z } from "zod";

import type { frequencySchema, ladderStepSchema } from "./contracts";

export type RemarketingFrequency = z.infer<typeof frequencySchema>;
export type LadderStep = z.infer<typeof ladderStepSchema>;
