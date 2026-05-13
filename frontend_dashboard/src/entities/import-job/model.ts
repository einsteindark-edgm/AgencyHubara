/**
 * Entidad "import-job" — trabajos de importación de catálogo Excel.
 * Refleja el wizard de 5 pasos. Las features sólo leen este modelo;
 * el avance del wizard es UI-state local de la feature.
 */

export type JobStatus = "draft" | "live" | "ok" | "err";

export interface ImportJob {
  id: string;
  status: JobStatus;
  name: string;
  count: number;
  /** Etiqueta relativa ("hace 2 min", "hace 5 días"). */
  when: string;
  progress: number;
}

export interface WizardStep {
  key: "src" | "ai" | "map" | "prev" | "run";
  title: string;
}

export const WIZARD_STEPS: WizardStep[] = [
  { key: "src",  title: "Cargar fuentes" },
  { key: "ai",   title: "Análisis IA" },
  { key: "map",  title: "Mapeo de columnas" },
  { key: "prev", title: "Vista previa" },
  { key: "run",  title: "Subir" },
];

/* ── Mock data del prototipo ────────────────────────────────────────── */

export interface RawColumn {
  key: string;
  samples: string[];
  target: string | null;
  conf: "high" | "med" | "low" | "skip";
  note?: string;
}

export const RAW_COLUMNS: RawColumn[] = [
  { key: "Nombre Producto", samples: ["Vela Sagrado Rostro", "Vela Ángel Guardián", "Vela Inmaculada"], target: "nombre",      conf: "high" },
  { key: "Precio (COP)",    samples: ["$36.000", "42.000", "29 990"],                                  target: "precio",      conf: "high", note: "Formato mixto: limpiar separadores y prefijos." },
  { key: "Desc.",           samples: ["Vela artesanal con esencia de mirra…", "—", "Vela blanca…"],    target: "descripcion", conf: "high", note: "1 fila vacía." },
  { key: "Tipo",            samples: ["Aromaticas", "Aromáticas", "decoración"],                       target: "categoria",   conf: "med",  note: "Normalizar mayúsculas / acentos." },
  { key: "Foto archivo",    samples: ["sagrado-rostro.jpg", "angel_guardian.jpg", ""],                 target: "imagen",      conf: "high", note: "12 imágenes faltantes en carpeta." },
  { key: "Cantidad disp.",  samples: ["12", "8", "—"],                                                 target: "stock",       conf: "high" },
  { key: "Código",          samples: ["VLA-001", "VLA-002", "VLA-003"],                                target: "sku",         conf: "high" },
  { key: "Última edición",  samples: ["02/04/2026", "—", "—"],                                         target: null,          conf: "skip", note: "Sin coincidencia. Se ignorará." },
];

export interface SchemaField {
  key: string;
  label: string;
  type: "string" | "currency" | "text" | "enum" | "file" | "integer";
  required: boolean;
}

export const CANONICAL_SCHEMA: SchemaField[] = [
  { key: "nombre",      label: "Nombre",      type: "string",   required: true },
  { key: "precio",      label: "Precio",      type: "currency", required: true },
  { key: "descripcion", label: "Descripción", type: "text",     required: true },
  { key: "categoria",   label: "Categoría",   type: "enum",     required: true },
  { key: "imagen",      label: "Imagen",      type: "file",     required: true },
  { key: "stock",       label: "Stock",       type: "integer",  required: false },
  { key: "sku",         label: "SKU",         type: "string",   required: false },
];

export interface PreviewProduct {
  id: number;
  name: string;
  price: number;
  desc: string;
  category: string;
  img: string;
  stock: number;
  sku: string;
  status: "ok" | "warn" | "err";
  c1: string;
  c2: string;
  warn?: "img" | "stock" | "cat" | "price";
  warnMsg?: string;
}

export const PREVIEW_PRODUCTS: PreviewProduct[] = [
  { id:1,  name:"Vela Sagrado Rostro",  price:36000, desc:"Vela artesanal con esencia de mirra y sándalo.", category:"Aromáticas",   img:"sagrado-rostro.jpg",   stock:12, sku:"VLA-001", status:"ok",   c1:"#7a5af8", c2:"#5b3ad6" },
  { id:2,  name:"Vela Ángel Guardián",  price:42000, desc:"Vela en forma de ángel, ideal para regalo.",     category:"Decoración",   img:"angel_guardian.jpg",   stock:8,  sku:"VLA-002", status:"ok",   c1:"#f59e0b", c2:"#d97706" },
  { id:3,  name:"Vela Inmaculada",      price:29990, desc:"Vela blanca litúrgica de 7 días.",               category:"Devocionales", img:"inmaculada.jpg",       stock:24, sku:"VLA-003", status:"warn", c1:"#60a5fa", c2:"#3b82f6", warn:"img",   warnMsg:"Imagen no encontrada en carpeta" },
  { id:4,  name:"Vela San Miguel",      price:38500, desc:"Vela esculpida San Miguel Arcángel, color rojo.", category:"Devocionales", img:"san-miguel.jpg",      stock:6,  sku:"VLA-004", status:"ok",   c1:"#ef4444", c2:"#b91c1c" },
  { id:5,  name:"Vela Citronela",       price:18000, desc:"Vela exterior, repele insectos.",                category:"Aromáticas",   img:"citronela.jpg",        stock:0,  sku:"VLA-005", status:"warn", c1:"#84cc16", c2:"#65a30d", warn:"stock", warnMsg:"Stock = 0" },
  { id:6,  name:"Vela Lavanda Premium", price:0,     desc:"Vela de masaje 100% cera vegetal.",              category:"Aromáticas",   img:"",                     stock:4,  sku:"VLA-006", status:"err",  c1:"#a855f7", c2:"#7e22ce", warn:"price", warnMsg:"Precio vacío en fila origen" },
  { id:7,  name:"Vela Rosa Mística",    price:34000, desc:"Vela aromática con pétalos secos de rosa.",      category:"Aromáticas",   img:"rosa-mistica.jpg",     stock:15, sku:"VLA-007", status:"ok",   c1:"#ec4899", c2:"#be185d" },
  { id:8,  name:"Vela Buda Zen",        price:48000, desc:"Vela esculpida en forma de Buda.",               category:"Decoración",   img:"buda-zen.jpg",         stock:3,  sku:"VLA-008", status:"ok",   c1:"#f97316", c2:"#c2410c" },
  { id:9,  name:"Vela Eucalipto",       price:22000, desc:"Vela con esencia de eucalipto.",                 category:"Aromáticas",   img:"eucalipto.jpg",        stock:18, sku:"VLA-009", status:"warn", c1:"#10b981", c2:"#047857", warn:"cat",  warnMsg:"Tipo: 'aromaticas' → 'Aromáticas'" },
  { id:10, name:"Vela Sagrado Corazón", price:39000, desc:"Vela en honor al Sagrado Corazón.",              category:"Devocionales", img:"sagrado-corazon.jpg",  stock:9,  sku:"VLA-010", status:"ok",   c1:"#dc2626", c2:"#991b1b" },
  { id:11, name:"Vela Cocoa & Vainilla",price:31000, desc:"Vela aromática con notas de cacao.",             category:"Aromáticas",   img:"cocoa-vainilla.jpg",   stock:22, sku:"VLA-011", status:"ok",   c1:"#92400e", c2:"#78350f" },
  { id:12, name:"Vela Virgen María",    price:36000, desc:"Vela esculpida Virgen María Auxiliadora.",       category:"Devocionales", img:"",                     stock:5,  sku:"VLA-012", status:"warn", c1:"#3b82f6", c2:"#1e40af", warn:"img",  warnMsg:"Imagen no encontrada" },
];

export const SAMPLE_JOBS: ImportJob[] = [
  { id:"j-now", status:"draft", name:"Catálogo Velas — Mayo",       count:247, when:"hace 2 min",   progress:0   },
  { id:"j-1",   status:"ok",    name:"Catálogo Velas Marzo 2026",   count:189, when:"hace 5 días",  progress:100 },
  { id:"j-2",   status:"ok",    name:"Promo Día de la Madre",        count:42,  when:"hace 2 sem.",  progress:100 },
  { id:"j-3",   status:"err",   name:"Importación Diciembre 2025",   count:312, when:"hace 4 meses", progress:71  },
  { id:"j-4",   status:"ok",    name:"Reposición Inventario",        count:64,  when:"hace 5 meses", progress:100 },
];
