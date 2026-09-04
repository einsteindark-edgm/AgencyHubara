/**
 * Inspector de Meta Business Agent: el estado del agente en Meta, en modo
 * lectura (entity_id, rollout, audiencia, handoff, seguimientos).
 */
import { useMbaConfig } from "@plugins/mba/frontend/entities/mba-config";
import { Panel } from "@/shared/ui";

interface Props {
  agentId: string;
}

export function MbaInspector({ agentId }: Props) {
  const { data } = useMbaConfig(agentId);
  if (!data) return null;
  const row = (label: string, value: string) => (
    <div className="form-row">
      <span className="lbl">{label}</span>
      <span className="val mono">{value}</span>
    </div>
  );
  return (
    <aside className="inspector">
      <div className="insp-body">
        <div style={{ padding: "12px 14px 8px" }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em" }}>Estado en Meta</div>
          <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 2 }}>{data.display_name}</div>
        </div>
        <Panel title="Número">
          {row("entity_id", data.entity_id ?? "sin onboardear")}
          {row("canal", data.channel)}
        </Panel>
        <Panel title="Rollout">
          {row("rollout.enabled", String(data.settings.rollout_enabled))}
          {row("ai_audience", data.settings.ai_audience)}
          {row("allowlist", `${data.allowlist.length} teléfono(s)`)}
        </Panel>
        <Panel title="Conversación">
          {row("handoff", data.settings.handoff.enabled ? data.settings.handoff.message_selection : "off")}
          {row("followup", data.settings.followup.enabled ? `${data.settings.followup.followup_interval_in_seconds}s` : "off (Hubara)")}
          {row("never_say", `${data.settings.never_say_phrases.length} frases`)}
        </Panel>
        <Panel title="Envío">
          {row("requests", String(data.requests.length))}
          {row("problemas", String(data.problems.length))}
        </Panel>
      </div>
    </aside>
  );
}
