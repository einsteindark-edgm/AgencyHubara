/**
 * Inspector simplificado del Upload Wizard. Por feedback del usuario:
 * sólo se mantiene el tab "Info" (sin atajos), y la lista de agentes
 * disponibles es informativa (Activo/Inactivo, sin toggle).
 */

import { Icon, InsBlock } from "@/shared/ui";

export function UploadInspector() {
  return (
    <aside className="inspector">
      <div className="insp-tabs">
        <button className="insp-tab on" title="Información">
          <Icon.info />
        </button>
      </div>

      <div className="insp-body">
        <InsBlock title="Resumen del trabajo">
          <div className="form-row">
            <span className="lbl">Nombre</span>
            <span className="val">Catálogo Velas Mayo</span>
          </div>
          <div className="form-row">
            <span className="lbl">Productos</span>
            <span className="val">247</span>
          </div>
          <div className="form-row">
            <span className="lbl">Estado</span>
            <span className="val">Borrador</span>
          </div>
          <div className="form-row">
            <span className="lbl">Última edición</span>
            <span className="val">hace 2 min</span>
          </div>
        </InsBlock>

        <InsBlock title="Esquema requerido">
          <div className="schema-list">
            <SchemaItem label="Nombre"      type="string"   required />
            <SchemaItem label="Precio"      type="currency" required />
            <SchemaItem label="Descripción" type="text"     required />
            <SchemaItem label="Categoría"   type="enum"     required />
            <SchemaItem label="Imagen"      type="file"     required />
            <SchemaItem label="Stock"       type="integer" />
            <SchemaItem label="SKU"         type="string" />
          </div>
        </InsBlock>

        <InsBlock title="Agentes disponibles" open={false}>
          <AgentLine name="Agente Limpieza" model="claude-sonnet-4-5" active />
          <AgentLine name="Mapeador" model="claude-haiku-4-5" active />
          <AgentLine name="Validador de imágenes" model="vision-tools" />
        </InsBlock>
      </div>
    </aside>
  );
}

function SchemaItem({
  label,
  type,
  required,
}: {
  label: string;
  type: string;
  required?: boolean;
}) {
  return (
    <div className={"sch" + (required ? "" : " opt")}>
      <span className="sn">{label}</span>
      <span className="st">{type}</span>
      {required && <span className="req">*</span>}
    </div>
  );
}

function AgentLine({
  name,
  model,
  active,
}: {
  name: string;
  model: string;
  active?: boolean;
}) {
  return (
    <div className="form-row" style={{ alignItems: "center" }}>
      <div>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{name}</div>
        <div
          style={{
            fontSize: 10.5,
            color: "var(--fg-mute)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {model}
        </div>
      </div>
      <span
        className="pay-badge"
        style={{
          background: active
            ? "rgba(48,209,88,0.15)"
            : "rgba(255,255,255,0.06)",
          color: active ? "#5be07b" : "var(--fg-mute)",
        }}
      >
        {active ? "Activo" : "Inactivo"}
      </span>
    </div>
  );
}
