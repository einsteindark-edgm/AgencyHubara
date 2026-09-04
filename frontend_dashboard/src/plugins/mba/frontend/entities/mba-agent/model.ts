import type { z } from "zod";
import type { mbaAgentSchema } from "./contracts";

export type MbaAgent = z.infer<typeof mbaAgentSchema>;
