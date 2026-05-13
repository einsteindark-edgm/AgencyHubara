/**
 * Wizard de importación con 5 pasos (Cargar fuentes → Análisis IA → Mapeo
 * → Vista previa → Subir). Header con stepper + footer con navegación.
 *
 * El estado se maneja con `useUploadWizard`. Los sub-pasos viven en este mismo
 * archivo porque son specifics de la feature — no se reutilizan en otra parte.
 */

import {
  CANONICAL_SCHEMA,
  PREVIEW_PRODUCTS,
  RAW_COLUMNS,
  WIZARD_STEPS,
  type PreviewProduct,
  type SchemaField,
} from "@/entities/import-job";
import { Icon, MacButton } from "@/shared/ui";
import { useState } from "react";
import { useUploadWizard } from "../model/useUploadWizard";

export function UploadWizard() {
  const w = useUploadWizard();

  return (
    <main className="up-canvas">
      <div className="up-head">
        <div className="ttl">
          <div>
            <h1>Catálogo Velas — Mayo</h1>
            <div className="sub">
              247 productos · Borrador · Editado hace 2 minutos
            </div>
          </div>
          <div className="actions">
            <MacButton ghost sm>
              <Icon.tag /> Etiquetar
            </MacButton>
            <MacButton ghost sm>
              <Icon.more />
            </MacButton>
          </div>
        </div>

        <div className="stepper">
          {WIZARD_STEPS.map((s, i) => (
            <div key={s.key} style={{ display: "contents" }}>
              <div
                className={
                  "step" +
                  (i < w.idx ? " done" : "") +
                  (i === w.idx ? " cur" : "")
                }
                onClick={() => w.setStep(s.key)}
              >
                <div className="sno">{i < w.idx ? <Icon.check /> : i + 1}</div>
                {s.title}
              </div>
              {i < WIZARD_STEPS.length - 1 && (
                <div className={"step-bar" + (i < w.idx ? " done" : "")} />
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="up-body">
        {w.step === "src" && (
          <StepSources
            excelLoaded={w.excelLoaded}
            setExcelLoaded={w.setExcelLoaded}
            folderLoaded={w.folderLoaded}
            setFolderLoaded={w.setFolderLoaded}
          />
        )}
        {w.step === "ai" && <StepAI />}
        {w.step === "map" && <StepMapping />}
        {w.step === "prev" && <StepPreview />}
        {w.step === "run" && (
          <StepRun
            progress={w.progress}
            running={w.running}
            setRunning={w.setRunning}
          />
        )}
      </div>

      <div className="wiz-nav">
        <MacButton ghost sm onClick={w.prev} disabled={w.idx === 0}>
          <Icon.back /> Atrás
        </MacButton>
        <span
          style={{ fontSize: 11.5, color: "var(--fg-mute)", marginLeft: 6 }}
        >
          Paso {w.idx + 1} de {WIZARD_STEPS.length} · {WIZARD_STEPS[w.idx].title}
        </span>
        <div className="right">
          <MacButton ghost sm>Guardar borrador</MacButton>
          {w.step !== "run" ? (
            <MacButton primary sm onClick={w.next}>
              {w.step === "prev" ? (
                <>
                  Ejecutar importación <Icon.upload />
                </>
              ) : (
                <>
                  Continuar <Icon.arrow />
                </>
              )}
            </MacButton>
          ) : (
            <MacButton primary sm disabled={w.progress < 100}>
              Finalizar
            </MacButton>
          )}
        </div>
      </div>
    </main>
  );
}

/* ── Steps ───────────────────────────────────────────────────────────── */

function SectionHead({
  title,
  sub,
  action,
}: {
  title: string;
  sub?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="sect-head">
      <div>
        <h3>{title}</h3>
        {sub && <p>{sub}</p>}
      </div>
      {action}
    </div>
  );
}

interface StepSourcesProps {
  excelLoaded: boolean;
  setExcelLoaded: (v: boolean) => void;
  folderLoaded: boolean;
  setFolderLoaded: (v: boolean) => void;
}

function StepSources({
  excelLoaded,
  setExcelLoaded,
  folderLoaded,
  setFolderLoaded,
}: StepSourcesProps) {
  return (
    <>
      <SectionHead
        title="Fuentes"
        sub="Sube el archivo Excel de productos y la carpeta con sus imágenes. La IA validará y normalizará todo antes de la subida."
      />

      <div className="drop-grid">
        {excelLoaded ? (
          <FilledDrop
            type="xls"
            name="catalogo-velas-mayo.xlsx"
            meta="247 filas · 8 columnas · 124 KB · subido hace 1 min"
            onClear={() => setExcelLoaded(false)}
          />
        ) : (
          <EmptyDrop
            kind="xls"
            title="Arrastra tu archivo Excel"
            desc="El archivo debe tener al menos las columnas: nombre, precio, descripción, categoría e imagen."
            formats={[".xlsx", ".xls", ".csv", ".numbers", ".tsv"]}
            onPick={() => setExcelLoaded(true)}
          />
        )}

        {folderLoaded ? (
          <FilledDrop
            type="folder"
            name="velas-fotos/"
            meta="235 imágenes · 56.2 MB · JPG, PNG, WebP"
            onClear={() => setFolderLoaded(false)}
            warn="12 nombres no coinciden con el Excel"
          />
        ) : (
          <EmptyDrop
            kind="folder"
            title="Carpeta de imágenes"
            desc="Las imágenes se asocian por nombre de archivo. También puedes subir un .zip y se descomprime automáticamente."
            formats={["carpeta", ".zip", ".rar"]}
            onPick={() => setFolderLoaded(true)}
          />
        )}
      </div>
    </>
  );
}

function EmptyDrop({
  kind,
  title,
  desc,
  formats,
  onPick,
}: {
  kind: "xls" | "folder";
  title: string;
  desc: string;
  formats: string[];
  onPick: () => void;
}) {
  return (
    <div className="drop" onClick={onPick}>
      <div className="dico">{kind === "xls" ? <Icon.xls /> : <Icon.folder />}</div>
      <h4>{title}</h4>
      <p>{desc}</p>
      <div className="formats">
        {formats.map((f) => (
          <span key={f}>{f}</span>
        ))}
      </div>
      <MacButton primary sm style={{ marginTop: 6 }}>
        Seleccionar archivo
      </MacButton>
    </div>
  );
}

function FilledDrop({
  type,
  name,
  meta,
  onClear,
  warn,
}: {
  type: "xls" | "folder";
  name: string;
  meta: string;
  onClear: () => void;
  warn?: string;
}) {
  return (
    <div className="drop filled">
      <div style={{ display: "flex", alignItems: "center", gap: 12, width: "100%" }}>
        <div className="dico">{type === "xls" ? <Icon.xls /> : <Icon.folder />}</div>
        <div className="filemeta">
          <span className="fn">{name}</span>
          <span className="fm">{meta}</span>
        </div>
        <button className="ico-btn" title="Quitar" onClick={onClear}>
          <Icon.x />
        </button>
      </div>
      {warn && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            marginTop: 10,
            fontSize: 11.5,
            color: "#ffb44a",
            padding: "7px 9px",
            background: "rgba(255,159,10,0.08)",
            border: "0.5px solid rgba(255,159,10,0.25)",
            borderRadius: 6,
            width: "100%",
          }}
        >
          <Icon.alert />
          <span style={{ flex: 1 }}>{warn}</span>
          <MacButton ghost sm>Ver detalle</MacButton>
        </div>
      )}
      <div className="swap">
        <MacButton ghost sm style={{ flex: 1 }}>
          Reemplazar
        </MacButton>
        <MacButton ghost sm>Vista previa</MacButton>
      </div>
    </div>
  );
}

function StepAI() {
  return (
    <>
      <div className="ai-card">
        <div className="av"><Icon.bot /></div>
        <div className="ab">
          <div className="ah">
            <span style={{ color: "var(--fg)" }}>Agente Limpieza</span>
            <span className="pulse" />
            <span
              style={{
                fontSize: 11,
                color: "var(--fg-mute)",
                fontWeight: 500,
              }}
            >
              v3.2 · claude-sonnet-4-5
            </span>
            <span className="ts">Análisis completado en 4.2 s</span>
          </div>
          <div className="at">
            Revisé tu archivo <b>catalogo-velas-mayo.xlsx</b>. Detecté{" "}
            <b>247 productos</b> en 8 columnas. Tu esquema cubre 7/7 campos
            requeridos del catálogo del software, aunque hay 1 columna no
            reconocida ("Última edición") que se ignorará. Identifiqué{" "}
            <b>23 problemas</b> en total: la mayoría son menores y puedo
            corregirlos automáticamente. <b>2 requieren tu revisión</b>.
          </div>
          <div className="stats">
            <div className="stat"><div className="k">Filas</div><div className="v">247</div></div>
            <div className="stat"><div className="k">Listas</div><div className="v" style={{ color: "#5be07b" }}>222</div></div>
            <div className="stat"><div className="k">Avisos</div><div className="v" style={{ color: "#ffb44a" }}>23</div></div>
            <div className="stat"><div className="k">Errores</div><div className="v" style={{ color: "#ff7269" }}>2</div></div>
          </div>
        </div>
      </div>

      <SectionHead
        title="Sugerencias del agente"
        sub="Aplicar todas mantiene el archivo original sin cambios — solo se transforma para la subida."
        action={
          <div style={{ display: "flex", gap: 6 }}>
            <MacButton ghost sm>Revisar una a una</MacButton>
            <MacButton primary sm>
              <Icon.spark /> Aplicar todas (21)
            </MacButton>
          </div>
        }
      />

      <div className="issues">
        <Issue level="info" title="Renombrar columnas a esquema canónico"
          desc="«Nombre Producto» → nombre, «Desc.» → descripcion, «Foto archivo» → imagen, «Cantidad disp.» → stock, «Código» → sku, «Tipo» → categoria"
          count="6 columnas" />
        <Issue level="warn" title="Limpiar formato de precios"
          desc="«$36.000», «42.000» y «29 990» normalizados a entero (36000, 42000, 29990). Detectado separador de miles inconsistente."
          count="247 valores" />
        <Issue level="warn" title="Normalizar categorías"
          desc="«aromaticas», «Aromaticas» y «Aromáticas» se unifican en «Aromáticas». «decoración» → «Decoración»."
          count="58 filas" />
        <Issue level="warn" title="Imágenes faltantes en carpeta"
          desc="12 productos referencian un archivo que no está en velas-fotos/. Listado: VLA-003, VLA-006, VLA-012, VLA-018…"
          count="12 productos" action="Ver lista" />
        <Issue level="err" title="Precio vacío"
          desc="Vela Lavanda Premium (VLA-006) tiene la celda de precio vacía. Requiere valor antes de subir."
          count="1 fila" action="Editar fila" />
        <Issue level="err" title="2 filas duplicadas"
          desc="Mismo SKU detectado en filas 89 y 154 («VLA-018»). Sugerido: mantener la última edición."
          count="2 filas" action="Resolver" />
        <Issue level="info" title="Eliminar columna no reconocida"
          desc="«Última edición» no existe en el catálogo. Se omitirá durante la subida (no se borra del archivo original)."
          count="1 columna" />
        <Issue level="ok" title="Stocks listos"
          desc="Todas las cantidades son enteros válidos. 4 filas con «—» se interpretarán como 0."
          count="247 filas" />
      </div>
    </>
  );
}

function Issue({
  level,
  title,
  desc,
  count,
  action,
}: {
  level: "info" | "warn" | "err" | "ok";
  title: string;
  desc: string;
  count: string;
  action?: string;
}) {
  const ico = level === "ok" ? "✓" : level === "err" ? "!" : level === "warn" ? "!" : "i";
  return (
    <div className={"issue " + level}>
      <div className="iico">{ico}</div>
      <div className="body">
        <div className="h">
          <span>{title}</span>
          <span className="ct">{count}</span>
        </div>
        <div className="d">{desc}</div>
        <div className="acts">
          {level !== "ok" && <MacButton ghost sm>Aplicar</MacButton>}
          {action && <MacButton ghost sm>{action}</MacButton>}
          <MacButton ghost sm>Ignorar</MacButton>
        </div>
      </div>
    </div>
  );
}

function StepMapping() {
  type Conn = { i: number; ti: number; conf: "high" | "med" | "low" | "skip" };
  const conns: Conn[] = [];
  RAW_COLUMNS.forEach((c, i) => {
    if (!c.target) return;
    const ti = CANONICAL_SCHEMA.findIndex((s) => s.key === c.target);
    conns.push({ i, ti, conf: c.conf });
  });

  return (
    <>
      <SectionHead
        title="Mapeo de columnas"
        sub="La IA conectó cada columna de tu Excel con un campo del catálogo. Toca cualquier flecha para reasignar."
        action={
          <MacButton ghost sm>
            <Icon.refresh /> Re-analizar
          </MacButton>
        }
      />

      <div className="map-grid">
        <div className="map-col">
          <h4>Tu Excel · 8 columnas</h4>
          {RAW_COLUMNS.map((c) => (
            <div className="map-row-l" key={c.key}>
              <div className="lh">
                <div className="ln">{c.key}</div>
                <div className="ls">{c.samples.slice(0, 2).join(" · ")}…</div>
              </div>
              <span className={"conf " + c.conf}>
                {c.conf === "skip"
                  ? "Ignorar"
                  : c.conf === "high"
                  ? "98%"
                  : c.conf === "med"
                  ? "76%"
                  : "52%"}
              </span>
            </div>
          ))}
        </div>

        <div className="map-mid">
          <svg viewBox="0 0 80 600" preserveAspectRatio="none">
            {conns.map((c, idx) => {
              const y1 = 30 + c.i * 51;
              const y2 = 30 + c.ti * 51;
              const cx = 40;
              const color =
                c.conf === "high"
                  ? "#5be07b"
                  : c.conf === "med"
                  ? "#ffb44a"
                  : "#ff7269";
              return (
                <path
                  key={idx}
                  d={`M 0 ${y1} C ${cx} ${y1}, ${cx} ${y2}, 80 ${y2}`}
                  fill="none"
                  stroke={color}
                  strokeWidth="1.4"
                  strokeOpacity="0.8"
                />
              );
            })}
          </svg>
        </div>

        <div className="map-col right">
          <h4>Catálogo del software · 7 campos</h4>
          {CANONICAL_SCHEMA.map((s) => (
            <div
              className={"map-row-r" + (s.required ? " req" : "")}
              key={s.key}
            >
              <div className="rh">
                <div className="rico"><SchemaIcon type={s.type} /></div>
                <div>
                  <span className="rn">{s.label}</span>
                  <span className="rt">{s.type}</span>
                </div>
              </div>
              <MacButton ghost sm>Editar</MacButton>
            </div>
          ))}
        </div>
      </div>

      <SectionHead title="Esquema de destino" sub="Campos que el software acepta. * obligatorios." />
      <div className="schema-list">
        {CANONICAL_SCHEMA.map((s) => (
          <div key={s.key} className={"sch" + (s.required ? "" : " opt")}>
            <span
              className="rico"
              style={{
                width: 18,
                height: 18,
                borderRadius: 4,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(10,132,255,0.15)",
                color: "var(--accent-fg)",
              }}
            >
              <SchemaIcon type={s.type} small />
            </span>
            <span className="sn">{s.label}</span>
            <span className="st">{s.type}</span>
            {s.required && <span className="req">obligatorio</span>}
          </div>
        ))}
      </div>
    </>
  );
}

function SchemaIcon({ type, small }: { type: SchemaField["type"]; small?: boolean }) {
  const size = small ? 10 : 12;
  const props = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    style: { width: size, height: size },
  };
  switch (type) {
    case "string":   return <svg {...props}><path d="M4 7h16M4 12h12M4 17h8" /></svg>;
    case "text":     return <svg {...props}><path d="M4 6h16M4 10h16M4 14h12M4 18h10" /></svg>;
    case "currency": return <svg {...props}><path d="M12 1v22M17 5H9a3 3 0 0 0 0 6h6a3 3 0 0 1 0 6H7" /></svg>;
    case "integer":  return <svg {...props}><path d="M5 6l3-2v16M14 8a3 3 0 1 1 4 4l-4 6h6" /></svg>;
    case "enum":     return <svg {...props}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
    case "file":     return <svg {...props}><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15l-5-5L5 21" /></svg>;
  }
}

function StepPreview() {
  const [filter, setFilter] = useState<"all" | "ok" | "warn" | "err">("all");
  const filtered = PREVIEW_PRODUCTS.filter((p) =>
    filter === "all" ? true : p.status === filter,
  );

  return (
    <>
      <SectionHead
        title="Vista previa normalizada"
        sub="Así quedará tu catálogo después de aplicar las transformaciones de la IA."
        action={
          <div style={{ display: "flex", gap: 6 }}>
            <MacButton ghost sm>
              <Icon.filter /> Filtros
            </MacButton>
            <MacButton ghost sm>Exportar Excel limpio</MacButton>
          </div>
        }
      />

      <div className="tbl-wrap">
        <div className="tbl-bar">
          <span
            className={"tbl-filter" + (filter === "all" ? " on" : "")}
            onClick={() => setFilter("all")}
          >
            Todas <span className="ct">247</span>
          </span>
          <span
            className={"tbl-filter" + (filter === "ok" ? " on" : "")}
            onClick={() => setFilter("ok")}
          >
            Listas <span className="ct">222</span>
          </span>
          <span
            className={"tbl-filter" + (filter === "warn" ? " on" : "")}
            onClick={() => setFilter("warn")}
          >
            Con avisos <span className="ct">23</span>
          </span>
          <span
            className={"tbl-filter" + (filter === "err" ? " on" : "")}
            onClick={() => setFilter("err")}
          >
            Errores <span className="ct">2</span>
          </span>
          <div className="right">
            <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>
              Mostrando {filtered.length} de 247
            </span>
            <MacButton ghost sm>
              <Icon.edit /> Edición masiva
            </MacButton>
          </div>
        </div>
        <table className="data-tbl">
          <thead>
            <tr>
              <th style={{ width: 36 }}><input type="checkbox" /></th>
              <th style={{ width: 46 }}>Img</th>
              <th>Nombre</th>
              <th>SKU</th>
              <th>Categoría</th>
              <th className="num">Precio</th>
              <th className="num">Stock</th>
              <th>Estado</th>
              <th style={{ width: 30 }} />
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <PreviewRow key={p.id} product={p} />
            ))}
          </tbody>
        </table>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 11.5,
          color: "var(--fg-mute)",
          padding: "0 4px",
        }}
      >
        <Icon.info />
        <span>
          Las filas resaltadas se transformarán automáticamente. Las marcadas
          con <b style={{ color: "#ff7269" }}>✗</b> requieren tu atención antes
          de continuar.
        </span>
      </div>
    </>
  );
}

function PreviewRow({ product: p }: { product: PreviewProduct }) {
  return (
    <tr>
      <td><input type="checkbox" /></td>
      <td>
        {p.img ? (
          <span
            className="thumb color"
            style={{ ["--c1" as never]: p.c1, ["--c2" as never]: p.c2 } as React.CSSProperties}
          >
            <Icon.img />
          </span>
        ) : (
          <span className="thumb miss" title="Imagen no encontrada">
            <Icon.img />
          </span>
        )}
      </td>
      <td className="name" title={p.desc}>
        {p.name}
      </td>
      <td>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10.5,
            color: "var(--fg-mute)",
          }}
        >
          {p.sku}
        </span>
      </td>
      <td className={p.warn === "cat" ? "hl-warn" : ""}>
        <span className="v">{p.category}</span>
      </td>
      <td className={"num " + (p.warn === "price" ? "hl-err" : "")}>
        <span className="v">
          {p.price ? "$ " + p.price.toLocaleString("es-CO") : "—"}
        </span>
      </td>
      <td className={"num " + (p.warn === "stock" ? "hl-warn" : "")}>
        {p.stock}
      </td>
      <td>
        <span className={"stcell " + p.status}>
          <span className="d" />
          {p.status === "ok"
            ? "Lista"
            : p.status === "warn"
            ? p.warnMsg ?? "Con avisos"
            : p.warnMsg ?? "Error"}
        </span>
      </td>
      <td>
        <button className="ico-btn">
          <Icon.more />
        </button>
      </td>
    </tr>
  );
}

function StepRun({
  progress,
  running,
  setRunning,
}: {
  progress: number;
  running: boolean;
  setRunning: (v: boolean) => void;
}) {
  const total = 247;
  const done = Math.floor((progress / 100) * total);
  const errors = Math.min(2, Math.floor(progress / 50));

  return (
    <div className="run-card">
      <div className="rh">
        <h3>Subiendo catálogo</h3>
        {running && progress < 100 && (
          <span className="live">EN VIVO</span>
        )}
        <div className="ctrl">
          <MacButton ghost sm onClick={() => setRunning(!running)}>
            {running ? "Pausar" : "Reanudar"}
          </MacButton>
        </div>
      </div>

      <div className="pbar">
        <div
          className="fill"
          style={{ width: Math.min(progress, 100) + "%" }}
        />
      </div>
      <div className="pmeta">
        <span>
          <b>{done}</b> de {total} productos
        </span>
        <span>
          <b>{Math.round(Math.min(progress, 100))}%</b>
        </span>
      </div>

      <div className="stats-row">
        <div className="stat">
          <div className="k">Subidos</div>
          <div className="v ok">{done}</div>
        </div>
        <div className="stat">
          <div className="k">Errores</div>
          <div className="v err">{errors}</div>
        </div>
        <div className="stat">
          <div className="k">Imágenes</div>
          <div className="v">{Math.floor(done * 0.95)}</div>
        </div>
        <div className="stat">
          <div className="k">Velocidad</div>
          <div className="v">~12/s</div>
        </div>
      </div>

      <div className="log-stream">
        {Array.from({ length: Math.min(12, done) }).map((_, i) => {
          const isError = i === 5;
          const cls = isError ? "err" : "upl";
          const symbol = isError ? "!" : "↑";
          return (
            <div key={i} className={"l " + cls}>
              <span className="t">12:01:0{i}</span>
              <span className="s">{symbol}</span>
              <span>
                {isError
                  ? "Error VLA-006 · precio vacío"
                  : `Subido VLA-00${(i % 9) + 1} · Vela Aromática Rosa Mística`}
              </span>
              <span className="id">#1{1240 + i}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
