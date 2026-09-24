import type { z } from "zod";

import type { turnThreadSchema } from "./contracts";

export type TurnThread = z.infer<typeof turnThreadSchema>;
