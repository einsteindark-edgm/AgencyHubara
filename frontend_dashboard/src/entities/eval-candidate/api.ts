import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import {
  evalCandidateDetailSchema,
  evalCandidatesListSchema,
} from "./contracts";
import { evalCandidateKeys } from "./keys";
import type { EvalCandidateDetail, EvalCandidateSummary } from "./model";

const BASE = "/api/chats/evals/candidates";

async function fetchCandidates(): Promise<EvalCandidateSummary[]> {
  const raw = await apiClient.get<unknown>(BASE);
  return evalCandidatesListSchema.parse(raw).candidates;
}

async function fetchCandidate(id: string): Promise<EvalCandidateDetail> {
  const raw = await apiClient.get<unknown>(`${BASE}/${encodeURIComponent(id)}`);
  return evalCandidateDetailSchema.parse(raw);
}

export function useEvalCandidates() {
  return useQuery({
    queryKey: evalCandidateKeys.list(),
    queryFn: fetchCandidates,
  });
}

export function useEvalCandidate(id: string | null) {
  return useQuery({
    queryKey: evalCandidateKeys.detail(id ?? ""),
    queryFn: () => fetchCandidate(id!),
    enabled: !!id,
  });
}

export function useApproveCandidate() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { id: string; expected_outcome?: string }>({
    mutationFn: ({ id, expected_outcome }) =>
      apiClient.post<unknown>(`${BASE}/${encodeURIComponent(id)}/approve`, {
        expected_outcome,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: evalCandidateKeys.all }),
  });
}

export function useDiscardCandidate() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (id) =>
      apiClient.delete<unknown>(`${BASE}/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: evalCandidateKeys.all }),
  });
}
