/**
 * La configuración REAL de Meta Business Agent (MBA) de un agente: skills,
 * business_info, FAQs, settings, connector tools, UI skills y allowlist, con la
 * forma EXACTA de los endpoints de Meta y la trazabilidad archivo → campo.
 *
 * Cada sección muestra, además de la vista legible, el REQUEST literal
 * (método, URL, headers y body JSON) tal cual se enviaría; arriba va la
 * secuencia completa numerada en orden de envío.
 *
 * Solo lectura. La fuente de verdad son los archivos autorados del plugin
 * (`agents/<id>/agent.yaml` + `skills/*.md`) que sirve
 * `GET /api/mba/agents/{id}/config`; acá no se inventa nada ni se llama a Meta.
 */

import type { ReactNode } from "react";

import {
  BUSINESS_INFO_FIELDS,
  CONTACT_INFO_FIELDS,
  useMbaConfig,
  type MbaConfig,
  type MbaRequest,
  type MbaSkill,
} from "@plugins/mba/frontend/entities/mba-config";
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

const PRE_STYLE = {
  whiteSpace: "pre-wrap" as const,
  fontSize: 12,
  marginTop: 6,
  padding: 10,
  borderRadius: 6,
  background: "rgba(0,0,0,0.18)",
  overflowX: "auto" as const,
};

function RequestLine({ req }: { req: MbaRequest }) {
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
      <span className="pip">#{req.step}</span>
      <code style={{ fontWeight: 700 }}>{req.method}</code>
      <code style={{ fontSize: 11.5 }}>{req.url}</code>
    </span>
  );
}

/**
 * El request literal: headers + body JSON exactamente como viajaría a Meta.
 * `collapsible` lo pliega bajo una línea "ver request exacto".
 */
function RequestView({ req, collapsible = false }: { req: MbaRequest; collapsible?: boolean }) {
  const json = JSON.stringify(req.body, null, 2);
  const body = (
    <div>
      <div style={{ fontSize: 11.5, fontFamily: "var(--font-mono)", color: "var(--fg-muted)", marginTop: 6 }}>
        {Object.entries(req.headers).map(([k, v]) => (
          <div key={k}>{`${k}: ${v}`}</div>
        ))}
      </div>
      <pre style={PRE_STYLE}>{json}</pre>
      {req.notes && (
        <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 2 }}>{req.notes}</div>
      )}
    </div>
  );
  if (!collapsible) {
    return (
      <div style={{ marginTop: 8 }}>
        <RequestLine req={req} />
        {body}
      </div>
    );
  }
  return (
    <details style={{ marginTop: 8 }}>
      <summary style={{ cursor: "pointer", listStyle: "none" }}>
        <RequestLine req={req} />
        <span style={{ fontSize: 11.5, color: "var(--fg-muted)", marginLeft: 8 }}>
          {fmt(json.length)} caracteres · ▸ ver request exacto
        </span>
      </summary>
      {body}
    </details>
  );
}

const SECTION_KIND: Record<string, string> = {
  business_info: "business_info",
  faqs: "FAQ",
  skills: "skill",
  connector: "connector",
  connector_tools: "connector tool",
  ui_skills: "UI skill",
  settings: "settings",
  allowlist: "allowlist",
};

function payloadLabel(r: MbaRequest): string {
  const kind = SECTION_KIND[r.section] ?? r.section;
  return kind === r.label ? kind : `${kind} · ${r.label}`;
}

function SendSequence({ requests }: { requests: MbaRequest[] }) {
  return (
    <ol style={{ listStyle: "none", margin: 0, padding: 0, fontSize: 12.5 }}>
      {requests.map((r) => (
        <li
          key={r.step}
          style={{
            display: "grid",
            gridTemplateColumns: "34px 1fr",
            gap: 8,
            padding: "6px 0",
            borderTop: "1px solid var(--border, rgba(255,255,255,0.08))",
          }}
        >
          <span style={{ color: "var(--fg-muted)" }}>{r.step}</span>
          <span style={{ minWidth: 0 }}>
            <span style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <code style={{ fontWeight: 700 }}>{r.method}</code>
              <code style={{ fontSize: 11.5, overflowWrap: "anywhere" }}>{r.url}</code>
            </span>
            <span style={{ display: "block", color: "var(--fg-muted)", fontSize: 12 }}>
              <code>{payloadLabel(r)}</code>
              <span> · {fmt(JSON.stringify(r.body).length)} caracteres</span>
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
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

function SkillCard({ skill, req }: { skill: MbaSkill; req?: MbaRequest }) {
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
          <span style={{ fontSize: 11.5, color: "var(--fg-muted)" }}>▸ ver request exacto</span>
        </div>
        <div style={{ fontSize: 12, color: "var(--fg-muted)", margin: "4px 0" }}>
          {skill.description}
        </div>
        <div>
          <Sources sources={skill.sources} />
        </div>
      </summary>
      {req ? <RequestView req={req} /> : <pre style={PRE_STYLE}>{skill.skill}</pre>}
    </details>
  );
}

function BusinessInfo({ data, req }: { data: MbaConfig["business_info"]; req?: MbaRequest }) {
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
      {req && <RequestView req={req} collapsible />}
    </div>
  );
}

function Settings({ settings, req }: { settings: MbaConfig["settings"]; req?: MbaRequest }) {
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
      {req && <RequestView req={req} collapsible />}
    </div>
  );
}

function Connector({
  connector,
  toolsEndpoint,
  req,
  toolReqs,
}: {
  connector: NonNullable<MbaConfig["connector"]>;
  toolsEndpoint?: { method: string; path: string };
  req?: MbaRequest;
  toolReqs: Map<string, MbaRequest>;
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
      {req && <RequestView req={req} collapsible />}

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
          {toolReqs.get(t.name) && <RequestView req={toolReqs.get(t.name)!} collapsible />}
        </div>
      ))}
    </div>
  );
}

export function MbaConfigPreview({ agentId }: Props) {
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
  const reqsFor = (section: string) => data.requests.filter((r) => r.section === section);
  const reqFor = (section: string, label?: string) =>
    data.requests.find((r) => r.section === section && (label === undefined || r.label === label));
  const toolReqs = new Map(reqsFor("connector_tools").map((r) => [r.label, r]));

  return (
    <div className="ag-form">
      <div style={{ fontSize: 12.5, color: "var(--fg-muted)", maxWidth: 760 }}>
        Esto es exactamente lo que se envía a Meta Business Agent para{" "}
        <code>{data.display_name}</code> (canal <code>{data.channel}</code>, entity_id{" "}
        <code>{data.entity_id ?? "sin onboardear"}</code>). Solo lectura: no se llama a Meta
        desde acá.
        <div style={{ marginTop: 6 }}>
          Fuente de verdad: <code>{data.workspace}</code>
          <span> · agent.yaml + skills/*.md; las fuentes de cada sección son relativas a esa carpeta.</span>
        </div>
      </div>

      {data.problems.length > 0 && (
        <Section
          icon="alert"
          title="Problemas"
          desc="Lo que Meta rechazaría tal cual. Se arregla en los archivos del agente."
          count={`${data.problems.length}`}
        >
          {data.problems.map((p) => (
            <div key={p} style={{ marginBottom: 6 }}>
              <Chip tone="warn">{p}</Chip>
            </div>
          ))}
        </Section>
      )}

      <Section
        icon="workflow"
        title="Secuencia de envío"
        desc="Todas las llamadas a Meta, numeradas en el orden en que se enviarían. Cada sección de abajo muestra el request literal (headers + body JSON) de sus ítems."
        count={`${data.requests.length} requests`}
      >
        <SendSequence requests={data.requests} />
      </Section>

      <Section
        icon="wand"
        title="Skills"
        desc="Instrucciones del sistema (persona, reglas, guion). Cada una ≤ 20.000 caracteres."
        endpoint={endpointFor("skills")}
        count={`${data.skills.length} skills`}
      >
        {data.skills.map((s) => (
          <SkillCard key={s.title} skill={s} req={reqFor("skills", s.title)} />
        ))}
      </Section>

      <Section
        icon="notes"
        title="Business info"
        desc="Conocimiento del negocio por campo de la API de Meta."
        endpoint={endpointFor("business_info")}
      >
        <BusinessInfo data={data.business_info} req={reqFor("business_info")} />
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
            {reqFor("faqs", f.question) && <RequestView req={reqFor("faqs", f.question)!} collapsible />}
          </div>
        ))}
      </Section>

      <Section
        icon="shield"
        title="Settings"
        desc="Rollout, audiencia, handoff, followup y frases que nunca debe decir."
        endpoint={endpointFor("settings")}
      >
        <Settings settings={data.settings} req={reqFor("settings")} />
      </Section>

      <Section
        icon="bolt"
        title="Connector y tools"
        desc="La API de Hubara que MBA invoca para consultar catálogo y pedidos, y registrar órdenes."
        endpoint={endpointFor("connector")}
        count={data.connector ? `${data.connector.tools.length} tools` : undefined}
      >
        {data.connector ? (
          <Connector
            connector={data.connector}
            toolsEndpoint={endpointFor("connector_tools")}
            req={reqFor("connector")}
            toolReqs={toolReqs}
          />
        ) : (
          <Empty />
        )}
      </Section>

      <Section
        icon="files"
        title="UI skills"
        desc="Componentes interactivos de WhatsApp, declarados uno por uno."
        endpoint={endpointFor("ui_skills")}
        count={`${data.ui_skills.length} UI skills`}
      >
        <div
          style={{
            fontSize: 12.5,
            color: "var(--fg-muted)",
            marginBottom: 12,
            padding: 10,
            borderRadius: 6,
            border: "1px solid var(--border, rgba(255,255,255,0.10))",
            maxWidth: 760,
          }}
        >
          MBA renderiza estos componentes por su cuenta; nosotros no construimos la UI. Lo que sí
          hay que declarar, uno por uno, es cuándo puede enviarlos y con qué datos. Los{" "}
          <b>estáticos</b> llevan todo en la instrucción (URL fija, botones fijos, flow_id). Los{" "}
          <b>dinámicos</b> dependen de datos del catálogo o del connector, y la doc de Meta no dice
          cómo los puebla: quedan marcados "a verificar en F0" hasta probarlos en el sandbox.
        </div>
        {data.ui_skills.length === 0 && <Empty />}
        {data.ui_skills.map((u) => (
          <div key={u.title} style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
              <code style={{ fontWeight: 700 }}>{u.title}</code>
              <span style={{ fontSize: 11.5, color: "var(--fg-muted)" }}>component_type</span>
              <code>{u.component_type}</code>
              {u.kind === "dynamic" ? (
                <Chip tone="warn">dinámica · a verificar en F0</Chip>
              ) : (
                <Chip>estática</Chip>
              )}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--fg-muted)" }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>instruction: </span>
              {u.instruction}
            </div>
            {u.note && (
              <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 2 }}>{u.note}</div>
            )}
            {reqFor("ui_skills", u.title) && (
              <RequestView req={reqFor("ui_skills", u.title)!} collapsible />
            )}
          </div>
        ))}
      </Section>

      <Section
        icon="shield"
        title="Allowlist (F0)"
        desc="Con ai_audience=ALLOWLISTED_ONLY solo estos teléfonos hablan con MBA, sin facturación. Es el único valor que no sale del workspace."
        endpoint={endpointFor("allowlist")}
        count={`${reqsFor("allowlist").length} request`}
      >
        {reqsFor("allowlist").map((r) => (
          <RequestView key={r.step} req={r} />
        ))}
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
