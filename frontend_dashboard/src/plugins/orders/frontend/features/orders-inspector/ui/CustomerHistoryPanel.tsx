import { useState } from "react";
import {
  useCustomerScore,
  useGenerateCustomerSummary,
  type CustomerSummary,
  type Order,
} from "@plugins/orders/frontend/entities/order";
import { Icon, InsBlock, MacButton, MissingData } from "@/shared/ui";
import { CustomerScoreBreakdown, CustomerScoreKVs } from "./CustomerScore";

export function CustomerHistoryPanel({ order }: { order: Order }) {
  const scoreQuery = useCustomerScore(order.id);
  const summarize = useGenerateCustomerSummary();
  const [llmResult, setLlmResult] = useState<CustomerSummary | null>(null);

  if (scoreQuery.isLoading) {
    return (
      <InsBlock title="Historial cliente" open={false}>
        <div style={{ padding: 12, color: "var(--fg-muted)", fontSize: 12 }}>
          Calculando…
        </div>
      </InsBlock>
    );
  }

  const score = scoreQuery.data;

  // "Sin datos" → renderear MissingData (cliente no tiene historial en el vault).
  if (!score || score.tag === "Sin datos") {
    return (
      <InsBlock title="Historial cliente" open={false}>
        <MissingData
          variant="block"
          label="Sin historial de cliente"
          reason={
            score?.score_reason ??
            "Este cliente no tiene actividad previa registrada en el sistema."
          }
        />
      </InsBlock>
    );
  }

  const handleSummarize = () => {
    summarize.mutate(
      { orderId: order.id },
      { onSuccess: (data) => setLlmResult(data) },
    );
  };

  return (
    <InsBlock title="Historial cliente" open={false}>
      <CustomerScoreKVs score={score} />

      {/* Botón LLM on-demand. La summary aparece debajo cuando vuelve. */}
      <div style={{ padding: "8px 12px 12px" }}>
        {!llmResult && (
          <MacButton sm onClick={handleSummarize} disabled={summarize.isPending}>
            <Icon.wand />{" "}
            {summarize.isPending ? "Generando…" : "Resumir con IA"}
          </MacButton>
        )}
        {summarize.isError && (
          <div
            style={{
              marginTop: 6,
              fontSize: 11,
              color: "#ff7269",
            }}
          >
            No pudimos generar el resumen ahora. Probá de nuevo.
          </div>
        )}
        {llmResult && <CustomerSummaryDisplay result={llmResult} />}
      </div>

      {/* Breakdown del score — tooltip-like, colapsable */}
      <CustomerScoreBreakdown score={score} />
    </InsBlock>
  );
}

function CustomerSummaryDisplay({ result }: { result: CustomerSummary }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div
        style={{
          padding: 10,
          background: "rgba(135,180,255,0.06)",
          border: "1px solid rgba(135,180,255,0.18)",
          borderRadius: 6,
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--fg-soft)",
          whiteSpace: "pre-wrap",
        }}
      >
        {result.summary}
      </div>
      <div
        style={{
          marginTop: 4,
          fontSize: 9,
          color: "var(--fg-muted)",
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        <span>modelo: {result.model}</span>
        <span>·</span>
        <span>{result.latency_ms}ms</span>
        {result.error_detail && (
          <>
            <span>·</span>
            <span style={{ color: "#ffb44a" }} title={result.error_detail}>
              fallback (LLM falló)
            </span>
          </>
        )}
      </div>
    </div>
  );
}
