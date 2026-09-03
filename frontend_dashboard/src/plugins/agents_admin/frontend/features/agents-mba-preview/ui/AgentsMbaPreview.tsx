/**
 * Vista previa de lo que se le enviaría a Meta Business Agent (MBA) para un
 * agente: skills, business_info, FAQs y settings, con la forma EXACTA de los
 * endpoints `/agent_config/*` de Meta y la trazabilidad archivo → campo.
 *
 * Solo lectura. La normalización vive en el backend
 * (`GET /api/agents/{id}/mba-config`), derivada del workspace REAL del agente;
 * acá no se inventa nada ni se llama a Meta.
 */

import type { ReactNode } from "react";

import {
  BUSINESS_INFO_FIELDS,
  CONTACT_INFO_FIELDS,
  useMbaConfig,
  type MbaConfig,
  type MbaSkill,
} from "@plugins/agents_admin/frontend/entities/mba-config";
import { Icon, type IconName } from "@/shared/ui";

interface Props {
  agentId: string;
}

const fmt = (n: number) => n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");

function Chip({ children, tone = "mute" }: { children: ReactNode; tone?: "mute" | "warn" }) {
  return (
    <span
      className="pip"
      style={{
        display: "inline-block",
        padding: "1px 7px",
        borderRadius: 999,
        border: "1px solid " + (tone === "warn" ? "var(--warn, #d97706)" : "var(--border, rgba(255,255,255,0.12))"),
        color: tone === "warn" ? "var(--warn, #d97706)" : undefined,
        marginRight: 6,
        marginBottom: 4,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

function Sources({ sources }: { sources: string[] }) {
  if (sources.length === 0) return <Chip tone="warn">sin fuente</Chip>;
  return (
    <span>
      {sources.map((s) => (
        <Chip key={s}>{s}</Chip>
      ))}
    </span>
  );
}

function Empty() {
  return <span style={{ color: "var(--fg-faint)" }}>sin fuente</span>;
}

function Section({
  icon,
  title,
  desc,
  endpoint,
  count,
  children,
}: {
  icon: IconName;
  title: string;
  desc: string;
  endpoint?: { method: string; path: string };
  count?: string;
  children: ReactNode;
}) {
  const SectionIcon = Icon[icon];
  return (
    <div className="prompt-section">
      <div className="ps-head">
        <span className="ps-icon">
          <SectionIcon />
        </span>
        <div className="ps-meta">
          <h3>{title}</h3>
          <p>{desc}</p>
        </div>
        {count && <span className="ps-count">{count}</span>}
      </div>
      <div className="prompt-view">
        {endpoint && (
          <div className="prompt-bar">
            <span className="pip">{endpoint.method}</span>
            <span className="pip">{endpoint.path}</span>
          </div>
        )}
        <div className="prompt-body" style={{ whiteSpace: "normal" }}>
          {children}
        </div>
      </div>
    </div>
  );
}

function SkillCard({ skill }: { skill: MbaSkill }) {
  return (
    <details style={{ marginBottom: 12 }}>
      <summary style={{ cursor: "pointer", listStyle: "none" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <code style={{ fontWeight: 700 }}>{skill.title}</code>
          <span style={{ color: "var(--fg-muted)", fontSize: 12 }}>
            {fmt(skill.char_count)} / {fmt(skill.char_limit)} caracteres
          </span>
          {skill.over_limit && (
            <Chip tone="warn">excede el límite de {fmt(skill.char_limit)}</Chip>
          )}
        </div>
        <div style={{ fontSize: 12, color: "var(--fg-muted)", margin: "4px 0" }}>
          {skill.description}
        </div>
        <div>
          <Sources sources={skill.sources} />
        </div>
      </summary>
      <pre
        style={{
          whiteSpace: "pre-wrap",
          fontSize: 12,
          marginTop: 8,
          padding: 10,
          borderRadius: 6,
          background: "rgba(0,0,0,0.18)",
        }}
      >
        {skill.skill}
      </pre>
    </details>
  );
}

function BusinessInfo({ data }: { data: MbaConfig["business_info"] }) {
  return (
    <div>
      {BUSINESS_INFO_FIELDS.map((f) => (
        <div key={f.key} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <code style={{ fontWeight: 700 }}>{f.key}</code>
            <span style={{ fontSize: 11.5, color: "var(--fg-muted)" }}>{f.desc}</span>
          </div>
          <div style={{ whiteSpace: "pre-wrap", fontSize: 12.5, marginTop: 4 }}>
            {data[f.key] ? data[f.key] : <Empty />}
          </div>
        </div>
      ))}
      <div style={{ marginBottom: 12 }}>
        <code style={{ fontWeight: 700 }}>contact_info</code>
        {CONTACT_INFO_FIELDS.map((f) => (
          <div key={f.key} style={{ display: "flex", gap: 8, alignItems: "baseline", marginTop: 4 }}>
            <code>{f.key}</code>
            <span style={{ fontSize: 12.5, whiteSpace: "pre-wrap" }}>
              {data.contact_info[f.key] ?? <Empty />}
            </span>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 12, color: "var(--fg-muted)" }}>
        Fuentes: <Sources sources={data.sources} />
      </div>
    </div>
  );
}

function Settings({ settings }: { settings: MbaConfig["settings"] }) {
  const row = (label: string, value: ReactNode) => (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginBottom: 6 }}>
      <code style={{ minWidth: 200 }}>{label}</code>
      <span style={{ fontSize: 12.5 }}>{value}</span>
    </div>
  );
  return (
    <div>
      {row("rollout.enabled", <code>{String(settings.rollout_enabled)}</code>)}
      {row("ai_audience", <code>{settings.ai_audience}</code>)}
      {row("handoff.enabled", <code>{String(settings.handoff.enabled)}</code>)}
      {row("handoff.message_selection", <code>{settings.handoff.message_selection}</code>)}
      {row("handoff.message", settings.handoff.message ? <span>{settings.handoff.message}</span> : <Empty />)}
      {row("followup.enabled", <code>{String(settings.followup.enabled)}</code>)}
      {row(
        "followup.followup_interval_in_seconds",
        <code>{settings.followup.followup_interval_in_seconds}</code>,
      )}
      {row("followup.message", settings.followup.message ?? <Empty />)}
      <div style={{ marginTop: 10 }}>
        <code style={{ fontWeight: 700 }}>never_say_phrases</code>
        <span style={{ fontSize: 11.5, color: "var(--fg-muted)", marginLeft: 8 }}>
          {settings.never_say_phrases.length} frases · pasa el mouse para ver la fuente
        </span>
        <div style={{ marginTop: 6 }}>
          {settings.never_say_phrases.map((p) => (
            <Chip key={p.phrase}>
              <span title={p.source}>{p.phrase}</span>
            </Chip>
          ))}
        </div>
      </div>
    </div>
  );
}

function Connector({
  connector,
  toolsEndpoint,
}: {
  connector: NonNullable<MbaConfig["connector"]>;
  toolsEndpoint?: { method: string; path: string };
}) {
  const row = (label: string, value: ReactNode) => (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginBottom: 4 }}>
      <code style={{ minWidth: 160 }}>{label}</code>
      <span style={{ fontSize: 12.5 }}>{value}</span>
    </div>
  );
  return (
    <div>
      {row("name", <code>{connector.name}</code>)}
      {row("description", connector.description)}
      {row("base_url", <code>{connector.base_url}</code>)}
      {row("auth_type", <code>{connector.auth_type}</code>)}
      {row("auth header", <code>{connector.auth_header}</code>)}
      {row("requires_certificate", <code>{String(connector.requires_certificate)}</code>)}

      <div style={{ marginTop: 14, display: "flex", gap: 8, alignItems: "baseline" }}>
        <code style={{ fontWeight: 700 }}>tools</code>
        {toolsEndpoint && (
          <>
            <span className="pip">{toolsEndpoint.method}</span>
            <span className="pip">{toolsEndpoint.path}</span>
          </>
        )}
      </div>
      {connector.tools.map((t) => (
        <div
          key={t.name}
          style={{
            marginTop: 8,
            padding: 10,
            borderRadius: 6,
            border: "1px solid var(--border, rgba(255,255,255,0.10))",
          }}
        >
          <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <code style={{ fontWeight: 700 }}>{t.name}</code>
            <code>{t.method}</code>
            <code>{t.path}</code>
            {t.write && <Chip tone="warn">escritura</Chip>}
          </div>
          <div style={{ fontSize: 12.5, margin: "4px 0" }}>{t.description}</div>
          <div style={{ fontSize: 12, color: "var(--fg-muted)" }}>
            <span>query_parameters: </span>
            {t.query_parameters.length ? t.query_parameters.map((p) => <Chip key={p}>{p}</Chip>) : <Empty />}
          </div>
          <div style={{ fontSize: 12, color: "var(--fg-muted)" }}>
            <span>body: </span>
            {t.body_parameters.length ? t.body_parameters.map((p) => <Chip key={p}>{p}</Chip>) : <Empty />}
          </div>
          <div style={{ fontSize: 12, color: "var(--fg-muted)" }}>
            <span>bindings (macros de Meta): </span>
            {t.bindings.map((b) => (
              <Chip key={b}>{b}</Chip>
            ))}
          </div>
          <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 4 }}>{t.notes}</div>
        </div>
      ))}
    </div>
  );
}

function ToolTreatments({ treatments }: { treatments: MbaConfig["tool_treatments"] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
        <thead>
          <tr style={{ color: "var(--fg-muted)", textAlign: "left" }}>
            <th style={{ padding: "4px 8px" }}>tool LLM</th>
            <th style={{ padding: "4px 8px" }}>tratamiento</th>
            <th style={{ padding: "4px 8px" }}>en MBA</th>
          </tr>
        </thead>
        <tbody>
          {treatments.map((t) => (
            <tr key={t.llm_tool} style={{ borderTop: "1px solid var(--border, rgba(255,255,255,0.08))" }}>
              <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                <code>{t.llm_tool}</code>
                <div style={{ fontSize: 11.5, color: "var(--fg-muted)" }}>{t.when}</div>
              </td>
              <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                <code>{t.treatment}</code>
              </td>
              <td style={{ padding: "6px 8px", verticalAlign: "top", color: "var(--fg-muted)" }}>
                {t.detail}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AgentsMbaPreview({ agentId }: Props) {
  const { data, isLoading, isError } = useMbaConfig(agentId);

  if (isLoading) {
    return (
      <div className="ag-form">
        <div style={{ color: "var(--fg-mute)", fontSize: 13 }}>Normalizando el workspace…</div>
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="ag-form">
        <div style={{ color: "var(--fg-mute)", fontSize: 13 }}>
          No se pudo cargar la configuración para Meta Business Agent.
        </div>
      </div>
    );
  }

  const endpointFor = (section: string) => data.endpoints.find((e) => e.section === section);

  return (
    <div className="ag-form">
      <div style={{ fontSize: 12.5, color: "var(--fg-muted)", maxWidth: 720 }}>
        Esto es exactamente lo que se enviaría a Meta Business Agent para{" "}
        <code>{data.agent_id}</code> (canal <code>{data.channel}</code>), derivado de los
        archivos reales del workspace. Solo lectura: no se llama a Meta desde acá.
      </div>

      <Section
        icon="wand"
        title="Skills"
        desc="Instrucciones del sistema (persona, reglas, guion). Cada una ≤ 20.000 caracteres."
        endpoint={endpointFor("skills")}
        count={`${data.skills.length} skills`}
      >
        {data.skills.map((s) => (
          <SkillCard key={s.title} skill={s} />
        ))}
      </Section>

      <Section
        icon="notes"
        title="Business info"
        desc="Conocimiento del negocio por campo de la API de Meta."
        endpoint={endpointFor("business_info")}
      >
        <BusinessInfo data={data.business_info} />
      </Section>

      <Section
        icon="chat"
        title="FAQs"
        desc="Pregunta y respuesta tal cual las leería el cliente."
        endpoint={endpointFor("faqs")}
        count={`${data.faqs.length} FAQs`}
      >
        {data.faqs.length === 0 && <Empty />}
        {data.faqs.map((f) => (
          <div key={f.question} style={{ marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{f.question}</div>
            <div style={{ fontSize: 12.5, whiteSpace: "pre-wrap" }}>{f.answer}</div>
            <Chip>{f.source}</Chip>
          </div>
        ))}
      </Section>

      <Section
        icon="shield"
        title="Settings"
        desc="Rollout, audiencia, handoff, followup y frases que nunca debe decir."
        endpoint={endpointFor("settings")}
      >
        <Settings settings={data.settings} />
      </Section>

      <Section
        icon="bolt"
        title="Connector y tools"
        desc="La API de Hubara que MBA invoca para consultar catálogo y pedidos, y registrar órdenes."
        endpoint={endpointFor("connector")}
        count={data.connector ? `${data.connector.tools.length} tools` : undefined}
      >
        {data.connector ? (
          <Connector connector={data.connector} toolsEndpoint={endpointFor("connector_tools")} />
        ) : (
          <Empty />
        )}
      </Section>

      <Section
        icon="files"
        title="UI skills"
        desc="Componentes ricos nativos de MBA que reemplazan nuestras tools de presentación."
        endpoint={endpointFor("ui_skills")}
        count={`${data.ui_skills.length} UI skills`}
      >
        {data.ui_skills.length === 0 && <Empty />}
        {data.ui_skills.map((u) => (
          <div key={u.from_tool} style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
              <code style={{ fontWeight: 700 }}>{u.title}</code>
              <span style={{ fontSize: 11.5, color: "var(--fg-muted)" }}>component_type</span>
              <code>{u.component_type}</code>
              <span style={{ fontSize: 11.5, color: "var(--fg-muted)" }}>desde</span>
              <Chip>{u.from_tool}</Chip>
            </div>
            <div style={{ fontSize: 12.5, color: "var(--fg-muted)" }}>{u.instruction}</div>
          </div>
        ))}
      </Section>

      <Section
        icon="workflow"
        title="Mapa tool LLM → MBA"
        desc="Cada tool del agente de hoy y qué la reemplaza en MBA. Nada queda sin decidir."
        count={`${data.tool_treatments.length} tools`}
      >
        <ToolTreatments treatments={data.tool_treatments} />
      </Section>

      <Section
        icon="alert"
        title="Fuera de MBA"
        desc="Lo que deliberadamente NO se envía, y por qué."
        count={`${data.excluded.length} ítems`}
      >
        {data.excluded.map((e) => (
          <div key={e.source} style={{ marginBottom: 8 }}>
            <code>{e.source}</code>
            <div style={{ fontSize: 12.5, color: "var(--fg-muted)" }}>{e.reason}</div>
          </div>
        ))}
      </Section>
    </div>
  );
}
