"""Arma los tres candidatos de archify del motor de decisiones (arquitectura,
secuencia y workflow) con la interfaz del visor en español.

Uso (desde la raíz del repo): `python3 docs/motor-de-decisiones/fuentes/generar_diagramas.py`
y luego, por cada carpeta de `.archify/`, `node <archify>/bin/archify.mjs finalize <tipo>
<carpeta>/candidate.json <carpeta>/<salida>.html --repo-root . --quality showcase`.
Al cambiar el código citado, actualizar `revision` y las líneas de cada `src(...)`."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARCHIFY = Path(os.environ.get("ARCHIFY_HOME", Path.home() / ".claude/skills/archify"))
ES = json.loads((ARCHIFY / "examples/locales/es.json").read_text())
REPO = {
    "url": "https://github.com/einsteindark-edgm/AgencyHubara.git",
    "provider": "github",
    "link_mode": "web",
    "revision": "e2cfd981e718666a8c773866f72768bab1933bef",
}
S = "hubara_agency/src/plugins/chats/agent/sales"
DEC = f"{S}/decisions"
BUNDLES = "hubara_agency/src/plugins/chats/shared/decisions/bundles"


def src(path, line=None, end=None, label=None):
    ref = {"path": path}
    if line:
        ref["line"] = line
    if end:
        ref["end_line"] = end
    if label:
        ref["label"] = label
    return ref


def meta(title, output, **extra):
    m = {"title": title, "output": output, "locale": "es", "translations": ES,
         "quality_profile": "showcase", "repository": REPO}
    m.update(extra)
    return m


ARCH_DIR = ".archify/architecture-motor-decisiones-20261002-183500"
architecture = {
    "schema_version": 1,
    "diagram_type": "architecture",
    "meta": meta("Motor de decisiones: dónde entra en el sistema", f"{ARCH_DIR}/motor-decisiones.html", views=[
        {"id": "lugares", "label": "Dónde se decide", "focus": ["ingest", "turno", "egreso", "seguimiento", "resolutor"],
         "note": "Cada lugar pide la decisión por nombre al mismo resolutor: ninguno lleva la lógica adentro."},
        {"id": "como", "label": "Cómo decide", "focus": ["resolutor", "paquete", "jev", "modos"],
         "note": "El paquete dice qué preguntar y cómo decidir; el interruptor dice si decide Jev o la regla."},
        {"id": "registro", "label": "Qué queda registrado", "focus": ["resolutor", "registros", "calidad", "modos"],
         "note": "Cada veredicto con Jev queda con su conversación y se ve en Calidad LLM."},
        {"id": "seguro", "label": "Qué protege al paquete", "focus": ["config", "certificador", "paquete"],
         "note": "Terraform elige el paquete; el certificador lo rechaza antes de desplegar si no compila."},
    ]),
    "components": [
        {"id": "cliente", "type": "external", "label": "Cliente", "sublabel": "mensaje por WhatsApp",
         "pos": [40, 40], "size": [150, 64],
         "sources": [src(f"{S}/use_cases/ingest_inbound_message.py", 561, 563, "el ingest lee el mensaje")]},
        {"id": "ingest", "type": "backend", "label": "Ingest del mensaje", "sublabel": "al leer cada mensaje",
         "pos": [270, 40], "size": [200, 64],
         "sources": [src(f"{DEC}/readings.py", 104, 136, "compra · retoma · baja · cortesía"),
                     src(f"{DEC}/readings.py", 238, 270, "cupón · fuera de catálogo")]},
        {"id": "turno", "type": "backend", "label": "Turno del asesor", "sublabel": "LLM + herramientas",
         "pos": [550, 40], "size": [220, 64],
         "sources": [src(f"{S}/activities/build_prompt_stage.py", 110, 111, "cantidad"),
                     src(f"{DEC}/engine.py", 188, 188, "la ráfaga ① y ③ de Jev"),
                     src(f"{DEC}/guards.py", 134, 286, "decisiones dentro de las tools")]},
        {"id": "egreso", "type": "backend", "label": "Egreso", "sublabel": "antes y después de enviar",
         "pos": [850, 40], "size": [210, 64],
         "sources": [src(f"{DEC}/egress.py", 540, 580, "capacidades del texto"),
                     src(f"{DEC}/egress_activities.py", 43, 43, "activity del egreso")]},
        {"id": "envio", "type": "cloud", "label": "WhatsApp", "sublabel": "respuesta revisada",
         "pos": [1140, 40], "size": [160, 64],
         "sources": [src(f"{DEC}/egress_activities.py", 43, 43, "el texto sale ya revisado")]},
        {"id": "config", "type": "cloud", "label": "Terraform → SSM", "sublabel": "paquete · techos",
         "pos": [20, 240], "size": [180, 72],
         "sources": [src("infra/terraform/platform/tenants.auto.tfvars", 39, 39, "decisions_bundle"),
                     src("infra/terraform/platform/modules/lab-config/main.tf", 72, 72, "SALES_DECISIONS_BUNDLE"),
                     src("hubara_agency/src/plugins/chats/shared/store_pack.py", 32, 32, "lo lee el resolutor")]},
        {"id": "resolutor", "type": "backend", "label": "Resolutor + decide()", "sublabel": "capability('x') · un solo enchufe",
         "tag": "registry.py", "pos": [330, 240], "size": [680, 72],
         "sources": [src(f"{DEC}/registry.py", 132, 177, "paquete activo y capability()"),
                     src(f"{DEC}/capabilities/__init__.py", 175, 275, "decide(): regla, Jev, tabla, piso"),
                     src(f"{DEC}/bots.py", 196, 225, "quién decide en esta conversación")]},
        {"id": "seguimiento", "type": "backend", "label": "Remarketing y cierre", "sublabel": "seguimiento · abandono",
         "pos": [1120, 240], "size": [200, 72],
         "sources": [src(f"{DEC}/remarketing_context.py", 26, 63, "contactar · producto · fuera de catálogo"),
                     src(f"{DEC}/cierre.py", 63, 66, "etiqueta de cierre"),
                     src(f"{DEC}/contact.py", 28, 28, "escribirle de nuevo")]},
        {"id": "sentinel", "type": "backend", "label": "Order Sentinel", "sublabel": "su paquete: centinela",
         "pos": [20, 440], "size": [190, 72],
         "sources": [src("hubara_agency/src/plugins/order_sentinel/agent/cycle/use_cases/readings.py", 72, 124, "cambio y evidencia")]},
        {"id": "paquete", "type": "database", "label": "Paquete de decisión", "sublabel": "ventas-2@2 · YAML + CEL",
         "pos": [300, 440], "size": [210, 72],
         "sources": [src(f"{BUNDLES}/ventas-2/bundle.yaml", 10, 14, "id, versión, oráculo"),
                     src(f"{BUNDLES}/builtins.yaml", 430, 430, "las 29 que el código pide"),
                     src(f"{BUNDLES}/builtins.yaml", 495, 504, "lugares y qué resuelve cada una")]},
        {"id": "jev", "type": "cloud", "label": "Jev", "sublabel": "OpenRouter · jev-1.13",
         "pos": [580, 440], "size": [170, 72],
         "sources": [src("hubara_agency/src/platform/perception/adapters/openrouter_decisions.py", 45, 65, "adaptador de Jev"),
                     src(f"{DEC}/capabilities/__init__.py", 138, 162, "espera y reintento")]},
        {"id": "modos", "type": "database", "label": "Interruptores", "sublabel": "modo por capacidad",
         "pos": [820, 440], "size": [190, 72],
         "sources": [src(f"{DEC}/bots.py", 131, 183, "modos guardados en el vault"),
                     src(f"{DEC}/bots.py", 77, 77, "off · sombra · canary · on")]},
        {"id": "registros", "type": "database", "label": "Registros", "sublabel": "decisiones · métricas · desacuerdos",
         "pos": [1080, 440], "size": [230, 72],
         "sources": [src(f"{DEC}/decision_log.py", 110, 116, "decisions.jsonl por conversación"),
                     src("hubara_agency/src/platform/perception/metrics.py", 22, 22, "métricas por decisión"),
                     src("hubara_agency/src/platform/perception/disagreements.py", 55, 55, "cola de desacuerdos")]},
        {"id": "certificador", "type": "security", "label": "Certificador", "sublabel": "decisions check · CI · deploy",
         "pos": [300, 640], "size": [210, 72],
         "sources": [src("hubara_agency/src/platform/decisions/checker.py", 672, 677, "check_bundle / load_bundle"),
                     src(".github/workflows/architecture-gates.yml", 181, 181, "CI"),
                     src(".github/workflows/backend-deploy.yml", 158, 158, "deploy certifica el de SSM")]},
        {"id": "calidad", "type": "frontend", "label": "Calidad LLM · Laboratorio", "sublabel": "ver · comparar · cambiar modo",
         "pos": [1080, 640], "size": [230, 72],
         "sources": [src("hubara_agency/src/plugins/chats/api/perception.py", 213, 213, "pestaña Motor de decisiones"),
                     src("hubara_agency/src/plugins/chats/api/quality.py", 75, 98, "conversaciones turno por turno"),
                     src(f"{S}_lab/arms.py", 50, 50, "brazo B@paquete")]},
    ],
    "connections": [
        {"id": "llega", "from": "cliente", "to": "ingest", "label": "mensaje", "variant": "emphasis"},
        {"id": "rafaga", "from": "ingest", "to": "turno", "label": "ráfaga", "variant": "emphasis"},
        {"id": "texto", "from": "turno", "to": "egreso", "label": "texto", "variant": "emphasis"},
        {"id": "envia", "from": "egreso", "to": "envio", "label": "envía", "variant": "emphasis"},
        {"id": "pide-ingest", "from": "ingest", "to": "resolutor", "label": "7 decisiones", "fromSide": "bottom", "toSide": "top"},
        {"id": "pide-turno", "from": "turno", "to": "resolutor", "label": "10 + el turno", "fromSide": "bottom", "toSide": "top"},
        {"id": "pide-egreso", "from": "egreso", "to": "resolutor", "label": "8 decisiones", "fromSide": "bottom", "toSide": "top"},
        {"id": "pide-seguimiento", "from": "seguimiento", "to": "resolutor", "label": "5 decisiones", "fromSide": "left", "toSide": "right"},
        {"id": "elige", "from": "config", "to": "resolutor", "label": "paquete · techos", "variant": "dashed", "fromSide": "right", "toSide": "left"},
        {"id": "lee", "from": "resolutor", "to": "paquete", "label": "lee", "fromSide": "bottom", "toSide": "top"},
        {"id": "pregunta", "from": "resolutor", "to": "jev", "label": "pregunta", "fromSide": "bottom", "toSide": "top"},
        {"id": "quien", "from": "resolutor", "to": "modos", "label": "quién decide", "fromSide": "bottom", "toSide": "top"},
        {"id": "anota", "from": "resolutor", "to": "registros", "label": "veredicto", "variant": "dashed", "fromSide": "bottom", "toSide": "top"},
        {"id": "centinela", "from": "sentinel", "to": "paquete", "label": "centinela"},
        {"id": "certifica", "from": "certificador", "to": "paquete", "label": "certifica", "variant": "security"},
        {"id": "muestra", "from": "registros", "to": "calidad", "label": "muestra"},
        {"id": "cambia", "from": "calidad", "to": "modos", "label": "cambia el modo", "fromSide": "left", "toSide": "bottom"},
    ],
    "cards": [
        {"dot": "orange", "title": "Un solo enchufe", "items": [
            "Cada lugar pide la decisión por nombre: capability('baja'); ninguno lleva la lógica adentro",
            "El resolutor la toma del paquete que eligió Terraform; si el paquete no la trae es un error (DB003), nunca una clase de Python",
            "decide() nunca se cae por Jev: sin respuesta, con duda o con el paquete roto decide la regla de hoy"]},
        {"dot": "emerald", "title": "Quién decide", "items": [
            "off: la regla de hoy · sombra: decide la regla y Jev se mide · canary: Jev en las conversaciones de prueba · on: Jev",
            "Siempre dentro del techo de Terraform; bajar nunca se bloquea, subir exige la vara (días en sombra, caídas, desacuerdos ganados)",
            "El piso (la baja legal, el relevo) lo aplica el motor aunque el paquete no lo pida"]},
        {"dot": "violet", "title": "Qué queda", "items": [
            "Cada decisión con Jev queda con su conversación (decisions.jsonl) y su paquete id@versión",
            "Métricas por decisión y la cola de desacuerdos regla ↔ Jev, que califica Claude Code",
            "Calidad LLM las muestra turno por turno; el laboratorio compara un paquete nuevo (B@paquete) antes de promoverlo"]},
    ],
}

SEQ_DIR = ".archify/sequence-una-decision-20261002-183500"
sequence = {
    "schema_version": 1,
    "diagram_type": "sequence",
    "meta": meta("Una decisión, paso a paso", f"{SEQ_DIR}/una-decision.html", viewBox=[1060, 680], column_fit="spread"),
    "participants": [
        {"id": "lugar", "type": "backend", "label": "Lugar que decide", "sublabel": "ingest · tool · egreso",
         "sources": [src(f"{DEC}/readings.py", 111, 126, "el ingest pide 4 a la vez")]},
        {"id": "modos", "type": "database", "label": "Interruptores", "sublabel": "bot de la conversación",
         "sources": [src(f"{DEC}/bots.py", 196, 225, "bot_for_session")]},
        {"id": "resolutor", "type": "backend", "label": "Resolutor", "sublabel": "capability() · decide()",
         "sources": [src(f"{DEC}/registry.py", 159, 177, "capability(nombre)"),
                     src(f"{DEC}/capabilities/__init__.py", 212, 275, "_decide")]},
        {"id": "paquete", "type": "database", "label": "Paquete", "sublabel": "capacidad compilada",
         "sources": [src(f"{BUNDLES}/ventas/capabilities/acuse.yaml", 9, 33, "una capacidad: acuse")]},
        {"id": "jev", "type": "cloud", "label": "Jev", "sublabel": "datos tapados",
         "sources": [src(f"{DEC}/capabilities/__init__.py", 138, 162, "pregunta con espera y reintento")]},
        {"id": "registros", "type": "database", "label": "Registros", "sublabel": "vault de la tienda",
         "sources": [src(f"{DEC}/capabilities/__init__.py", 248, 265, "métrica y desacuerdo"),
                     src(f"{DEC}/capabilities/__init__.py", 200, 206, "decisión de la conversación")]},
    ],
    "segments": [
        {"from": 160, "to": 320, "label": "Quién decide"},
        {"from": 325, "to": 420, "label": "Preguntar a Jev"},
        {"from": 425, "to": 565, "label": "Decidir y anotar"},
    ],
    "messages": [
        {"id": "pide-bot", "from": "lugar", "to": "modos", "y": 180, "label": "¿quién decide aquí?"},
        {"id": "proveedor", "from": "modos", "to": "lugar", "y": 210, "label": "regla · sombra · jev", "variant": "return"},
        {"id": "pide-capacidad", "from": "lugar", "to": "resolutor", "y": 245, "label": "capability('acuse')", "variant": "emphasis"},
        {"id": "lee-paquete", "from": "resolutor", "to": "paquete", "y": 275, "label": "la del paquete activo"},
        {"id": "trae", "from": "paquete", "to": "resolutor", "y": 305, "label": "regla · preguntas · tabla", "variant": "return"},
        {"id": "decide", "from": "lugar", "to": "resolutor", "y": 340, "label": "decide(entrada)", "variant": "emphasis",
         "note": "Con la regla de hoy (off) termina aquí: decide la regla."},
        {"id": "pregunta", "from": "resolutor", "to": "jev", "y": 375, "label": "preguntas del paquete"},
        {"id": "responde", "from": "jev", "to": "resolutor", "y": 405, "label": "probabilidades", "variant": "return"},
        {"id": "tabla", "from": "resolutor", "to": "paquete", "y": 440, "label": "tabla when/then (CEL)"},
        {"id": "valor", "from": "paquete", "to": "resolutor", "y": 470, "label": "valor · duda · error", "variant": "return"},
        {"id": "anota", "from": "resolutor", "to": "registros", "y": 505, "label": "métrica · desacuerdo · decisión", "variant": "dashed"},
        {"id": "veredicto", "from": "resolutor", "to": "lugar", "y": 545, "label": "veredicto: valor · quién · motivo", "variant": "return"},
    ],
    "activations": [
        {"participant": "lugar", "from": 175, "to": 555, "type": "backend"},
        {"participant": "modos", "from": 178, "to": 214, "type": "database"},
        {"participant": "resolutor", "from": 243, "to": 550, "type": "backend"},
        {"participant": "paquete", "from": 273, "to": 309, "type": "database"},
        {"participant": "jev", "from": 372, "to": 409, "type": "cloud"},
        {"participant": "paquete", "from": 438, "to": 474, "type": "database"},
        {"participant": "registros", "from": 502, "to": 520, "type": "database"},
    ],
    "cards": [
        {"dot": "orange", "title": "Quién se queda con la decisión", "items": [
            "off: la regla de hoy, sin preguntar a Jev",
            "sombra: decide la regla; Jev se pregunta igual y se mide (métrica y desacuerdo)",
            "canary / on: decide Jev; si no contesta, duda, cambió de modelo o falló el paquete, decide la regla (respaldo)",
            "Con Jev, el piso del motor tiene la última palabra (un acuse con «?» nunca se absorbe)"]},
        {"dot": "emerald", "title": "La tabla", "items": [
            "Filas en orden: la primera cuya condición se cumple decide",
            "then: doubt = decide la regla; leer una respuesta que no llegó también es duda",
            "Un error de la tabla no es duda de Jev: el veredicto dice reason=bundle_error"]},
        {"dot": "violet", "title": "Lo que dice el veredicto", "items": [
            "value, by (reglas · jev · piso · respaldo), provider, reason",
            "Las respuestas de Jev, la regla, si coincidieron y el paquete id@versión",
            "Con eso se diagnostica un bug sin abrir el código"]},
    ],
}

WF_DIR = ".archify/workflow-bug-de-decision-20261002-183500"
workflow = {
    "schema_version": 2,
    "diagram_type": "workflow",
    "meta": meta("Un bug de decisión: del síntoma al paquete nuevo", f"{WF_DIR}/bug-de-decision.html", legend={"entries": {
        "frontend": {"label": "Pantalla"}, "backend": {"label": "Lectura o código"}, "security": {"label": "Decisión del programador"},
        "database": {"label": "Paquete (datos)"}, "cloud": {"label": "Despliegue"}}}),
    "lanes": [
        {"id": "produccion", "label": "Producción"},
        {"id": "diagnostico", "label": "Diagnóstico"},
        {"id": "codigo", "label": "Código (solo si falta)", "variant": "exception"},
        {"id": "paquete", "label": "Paquete nuevo y laboratorio"},
    ],
    "phases": [
        {"id": "ver", "label": "Ver", "fromCol": 0, "toCol": 1},
        {"id": "arreglar", "label": "Arreglar en el paquete", "fromCol": 2, "toCol": 3, "variant": "emphasis"},
        {"id": "probar", "label": "Medir y promover", "fromCol": 4, "toCol": 5, "variant": "dashed"},
    ],
    "groups": [
        {"id": "tdd", "label": "Rojo → verde", "lane": "paquete", "fromCol": 2, "toCol": 3, "variant": "emphasis"},
    ],
    "mainPath": ["sintoma", "veredicto", "causa", "ejemplo", "arreglo", "banco", "promover"],
    "nodes": [
        {"id": "sintoma", "lane": "produccion", "col": 0, "type": "frontend", "label": "Síntoma", "sublabel": "Calidad LLM · el turno", "width": 150,
         "sources": [src("hubara_agency/src/plugins/chats/api/quality.py", 98, 98, "decisiones del turno")]},
        {"id": "veredicto", "lane": "diagnostico", "col": 1, "type": "backend", "label": "Leer el veredicto", "sublabel": "capacidad · paquete · motivo", "width": 160,
         "sources": [src(f"{DEC}/capabilities/__init__.py", 63, 94, "qué trae un Verdict")]},
        {"id": "causa", "lane": "diagnostico", "col": 2, "type": "security", "label": "¿Dónde falló?", "sublabel": "pregunta · tabla · dato · modo", "width": 160},
        {"id": "ejemplo", "lane": "paquete", "col": 2, "type": "database", "label": "Ejemplo que falla", "sublabel": "examples: → DB010", "width": 150,
         "sources": [src(f"{BUNDLES}/ventas/capabilities/acuse.yaml", 30, 33, "examples de una capacidad")]},
        {"id": "arreglo", "lane": "paquete", "col": 3, "type": "database", "label": "Pregunta · umbral · fila", "sublabel": "en ventas-N, certificado", "width": 170,
         "sources": [src("hubara_agency/src/platform/decisions/checker.py", 672, 677, "decisions check")]},
        {"id": "builtin", "lane": "codigo", "col": 3, "type": "backend", "label": "Builtin con su prueba", "sublabel": "un dato o un cálculo nuevo", "width": 170,
         "sources": [src(f"{BUNDLES}/builtins.yaml", 87, 87, "catálogo de builtins")]},
        {"id": "modo", "lane": "produccion", "col": 3, "type": "security", "label": "Subir el modo", "sublabel": "sombra → canary → on", "width": 160,
         "sources": [src(f"{DEC}/capability_rollout.py", 1, 18, "la vara para subir")]},
        {"id": "banco", "lane": "paquete", "col": 4, "type": "frontend", "label": "Corrida B@ventas-N", "sublabel": "contra el banco real", "width": 160,
         "sources": [src(f"{S}_lab/arms.py", 50, 50, "arm_env")]},
        {"id": "promover", "lane": "produccion", "col": 5, "type": "cloud", "label": "Promover", "sublabel": "Terraform + deploy", "width": 150,
         "sources": [src("infra/terraform/platform/tenants.auto.tfvars", 39, 39, "decisions_bundle")]},
    ],
    "edges": [
        {"id": "abre", "from": "sintoma", "to": "veredicto", "label": "abrir", "variant": "emphasis"},
        {"id": "clasifica", "from": "veredicto", "to": "causa", "variant": "emphasis"},
        {"id": "rojo", "from": "causa", "to": "ejemplo", "label": "decidió mal", "variant": "emphasis"},
        {"id": "verde", "from": "ejemplo", "to": "arreglo", "label": "verde", "variant": "emphasis"},
        {"id": "falta-dato", "from": "causa", "to": "builtin", "label": "falta un dato", "role": "branch", "variant": "dashed"},
        {"id": "usa-builtin", "from": "builtin", "to": "arreglo", "role": "branch", "variant": "dashed"},
        {"id": "decidio-regla", "from": "causa", "to": "modo", "label": "decidió la regla", "role": "branch"},
        {"id": "mide", "from": "arreglo", "to": "banco", "label": "medir", "variant": "emphasis"},
        {"id": "gana", "from": "banco", "to": "promover", "label": "gana", "variant": "emphasis"},
        {"id": "vigila", "from": "promover", "to": "sintoma", "label": "vigilar", "role": "return", "variant": "dashed",
         "fromSide": "top", "toSide": "top", "route": "up-channel"},
    ],
    "cards": [
        {"dot": "orange", "title": "La regla de oro", "items": [
            "Un bug de decisión se arregla en el paquete: otra pregunta, otro umbral u otra fila, en una versión nueva",
            "Nunca un if, un regex o una guarda en el lugar que decide: ese era el camino de main"]},
        {"dot": "rose", "title": "Código solo si falta", "items": [
            "Si la tabla necesita un dato que la entrada no trae o un cálculo que CEL no hace, se agrega UN builtin genérico con su prueba y su entrada en el catálogo",
            "La decisión sigue en el YAML: el builtin solo trae el dato"]},
        {"dot": "emerald", "title": "Antes de promover", "items": [
            "decisions check en verde (CI y el deploy lo vuelven a correr)",
            "La huella de la versión nueva en test_decision_bundles_published.py",
            "El laboratorio muestra B → B@ventas-N; si gana, Terraform la nombra"]},
    ],
}

for name, doc, folder, fname in (
    ("architecture", architecture, ARCH_DIR, "candidate.json"),
    ("sequence", sequence, SEQ_DIR, "candidate.json"),
    ("workflow", workflow, WF_DIR, "candidate.json"),
):
    if len(sys.argv) > 1 and name not in sys.argv[1:]:
        continue
    out = ROOT / folder / fname
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("escrito", out.relative_to(ROOT))
