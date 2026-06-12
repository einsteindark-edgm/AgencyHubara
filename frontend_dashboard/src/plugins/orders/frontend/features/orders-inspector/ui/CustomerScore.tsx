import type { CustomerScore } from "@plugins/orders/frontend/entities/order";
import { fmtMoney } from "@/shared/lib";

export function CustomerScoreKVs({ score }: { score: CustomerScore }) {
  const tagColor = _tagColor(score.tag);
  const letterColor = _letterColor(score.score_letter);
  const relative = _relativeDate(score.last_purchase_at_ms);
  return (
    <div className="kv-grid" style={{ padding: "8px 12px 0" }}>
      <div className="kv">
        <span className="k">Valor total</span>
        <span className="v" style={{ fontVariantNumeric: "tabular-nums" }}>
          {fmtMoney(score.monetary_cop)}
        </span>
      </div>
      <div className="kv">
        <span className="k">Última compra</span>
        <span className="v" style={{ color: relative ? undefined : "var(--fg-muted)" }}>
          {relative ?? "—"}
        </span>
      </div>
      <div className="kv">
        <span className="k">Órdenes</span>
        <span
          className="v"
          title={
            score.episodes_total > score.frequency_total
              ? `${score.frequency_total} compra${score.frequency_total === 1 ? "" : "s"} de ${score.episodes_total} conversaciones (resto: rechazados, pendientes o activos)`
              : undefined
          }
        >
          <span style={{ fontWeight: 600 }}>
            {score.frequency_total}
          </span>
          {score.episodes_total > 0 && (
            <span style={{ color: "var(--fg-muted)", marginLeft: 4 }}>
              {" "}
              compra{score.frequency_total === 1 ? "" : "s"} de{" "}
              {score.episodes_total} episodio{score.episodes_total === 1 ? "" : "s"}
            </span>
          )}
        </span>
      </div>
      <div className="kv">
        <span className="k">Tag</span>
        <span
          className="v"
          style={{
            color: tagColor,
            fontWeight: 600,
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: 0.3,
          }}
        >
          {score.tag}
        </span>
      </div>
      <div className="kv">
        <span className="k">Score</span>
        <span
          className="v"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
          }}
          title={`${score.score_value}/100 — ${score.score_reason}`}
        >
          <span
            style={{
              background: letterColor,
              color: "#0a0a0a",
              padding: "1px 6px",
              borderRadius: 3,
              fontWeight: 700,
              fontSize: 10,
            }}
          >
            {score.score_letter}
          </span>
          <span style={{ color: "var(--fg-soft)" }}>{score.score_reason}</span>
        </span>
      </div>
    </div>
  );
}

export function CustomerScoreBreakdown({ score }: { score: CustomerScore }) {
  if (score.breakdown.length === 0) return null;
  return (
    <details style={{ padding: "0 12px 12px" }}>
      <summary
        style={{
          cursor: "pointer",
          fontSize: 10,
          color: "var(--fg-muted)",
          textTransform: "uppercase",
          letterSpacing: 0.5,
        }}
      >
        Cómo se calculó (v{score.rules_version})
      </summary>
      <table
        style={{
          width: "100%",
          fontSize: 11,
          marginTop: 6,
          borderCollapse: "collapse",
        }}
      >
        <tbody>
          {score.breakdown.map((b) => (
            <tr key={b.feature}>
              <td
                style={{
                  padding: "2px 6px",
                  color: "var(--fg-muted)",
                }}
              >
                {b.feature}
              </td>
              <td
                style={{
                  padding: "2px 6px",
                  fontFamily: "var(--font-mono)",
                  color: "var(--fg-soft)",
                  textAlign: "right",
                }}
              >
                {b.feature_value}
              </td>
              <td
                style={{
                  padding: "2px 6px",
                  fontWeight: 600,
                  color: b.points > 0 ? "#5be07b" : b.points < 0 ? "#ff7269" : "var(--fg-muted)",
                  textAlign: "right",
                  width: 50,
                }}
              >
                {b.points > 0 ? `+${b.points}` : b.points}
              </td>
            </tr>
          ))}
          <tr style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
            <td colSpan={2} style={{ padding: "4px 6px", fontWeight: 600 }}>
              Total
            </td>
            <td
              style={{
                padding: "4px 6px",
                fontWeight: 700,
                textAlign: "right",
              }}
            >
              {score.score_value}/100
            </td>
          </tr>
        </tbody>
      </table>
    </details>
  );
}

/* ── helpers de styling del score ──────────────────────────────────────── */

function _tagColor(tag: string): string {
  switch (tag) {
    case "VIP":
      return "#ffd166";
    case "Recurrente":
      return "#5be07b";
    case "Nuevo":
      return "#87b4ff";
    case "Frío":
      return "#ff7269";
    default:
      return "var(--fg-soft)";
  }
}

function _letterColor(letter: string): string {
  switch (letter) {
    case "A":
      return "#5be07b";
    case "B":
      return "#87b4ff";
    case "C":
      return "#ffb44a";
    case "D":
      return "#ff7269";
    default:
      return "rgba(255,255,255,0.15)";
  }
}

function _relativeDate(ms: number | null): string | null {
  if (ms === null) return null;
  const days = Math.floor((Date.now() - ms) / 86_400_000);
  if (days <= 0) return "hoy";
  if (days === 1) return "ayer";
  if (days < 7) return `hace ${days} días`;
  if (days < 30) return `hace ${Math.floor(days / 7)} semana${days < 14 ? "" : "s"}`;
  if (days < 365) return `hace ${Math.floor(days / 30)} mes${days < 60 ? "" : "es"}`;
  return `hace ${Math.floor(days / 365)} año${days < 730 ? "" : "s"}`;
}
