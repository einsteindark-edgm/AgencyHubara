import { useQuery } from "@tanstack/react-query";
import { importJobKeys } from "./keys";
import { SAMPLE_JOBS } from "./model";

export function useImportJobs() {
  return useQuery({
    queryKey: importJobKeys.list(),
    queryFn: async () => SAMPLE_JOBS,
    staleTime: Infinity,
  });
}
