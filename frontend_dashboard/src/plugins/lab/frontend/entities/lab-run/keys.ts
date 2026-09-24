import type { LaunchInput } from "./model";

/** TanStack Query key factory del laboratorio. */
export const labKeys = {
  all: ["lab"] as const,
  runs: () => [...labKeys.all, "runs"] as const,
  active: () => [...labKeys.all, "active"] as const,
  estimate: (input: LaunchInput) => [...labKeys.all, "estimate", input.arms.join(","), input.reps, input.bench] as const,
  run: (run: string) => [...labKeys.all, "run", run] as const,
  bench: (run: string) => [...labKeys.run(run), "bench"] as const,
  conversations: (run: string) => [...labKeys.run(run), "conversations"] as const,
  thread: (run: string, sid: string, episode: string | null) => [...labKeys.run(run), "thread", sid, episode ?? "all"] as const,
  trace: (run: string, sid: string, turnKey: string, arm: string, rep: number) =>
    [...labKeys.run(run), "trace", sid, turnKey, arm, rep] as const,
  evaluations: (run: string, sid: string, arm: string, rep: number) => [...labKeys.run(run), "evaluations", sid, arm, rep] as const,
} as const;
