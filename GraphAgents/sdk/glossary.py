"""El GLOSARIO de convenciones del explorer — **fuente única** del vocabulario que el viewer
muestra (niveles de certificación + reglas G-*/T-*/CASE-* + cómo leer el grafo). Mismo patrón
que `sdk.graph`: una fuente, varios frontends (el endpoint `/api/legend` del viewer; mañana el
CLI). El `term` de cada regla es el CÓDIGO EXACTO que emiten los checks (`[G-RUN-SIG]`, …) →
`test_glossary` prueba que ningún código de check queda sin su glosa (la regla de oro aplicada
al vocabulario: ninguna sigla sin su explicación).
"""
from __future__ import annotations


def glossary() -> dict:
    """El vocabulario, en secciones. Read-only, sin estado."""
    return {"sections": [
        {"title": "Niveles de certificación", "blurb": "el badge de cada nodo (qué tan confiable es).", "items": [
            {"term": "none", "meaning": "algo declarado no existe o el manifest/tool no carga."},
            {"term": "C0", "meaning": "schema roto o una referencia no resuelve — no compila."},
            {"term": "C1", "meaning": "carga y corre, pero con warnings (mejorable, todavía no mergeable como C2)."},
            {"term": "C2", "meaning": "TCK verde: mergeable y componible. Es el PISO para usar o reusar un agente/tool."},
            {"term": "C3", "meaning": "reservado — certificación reforzada (verificado en vivo / conformance extendida)."},
        ]},
        {"title": "Reglas de agente / supervisor", "blurb": "lo que garantiza un nodo agente (sección CERTIFICACIÓN).", "items": [
            {"term": "C1-CAP", "meaning": "la capability (módulo:función) del agente importa y existe."},
            {"term": "G-RUN-SIG", "meaning": "la capability expone run(input, *, ports, tools) — la firma uniforme que inyecta el loader."},
            {"term": "G-PROTO", "meaning": "la capability satisface el protocolo del kit (sdk.capability): run con la firma de inyección + build keyword-only si existe. Hace a TODAS las capabilities uniformes y, por el mapping tools, TRAZABLES (sdk.tooltrace)."},
            {"term": "G-BIND", "meaning": "toda tool uses: resuelve al catálogo y su with: nombra SOLO inputs del contrato de esa tool."},
            {"term": "SUP", "meaning": "el supervisor es coherente: declara agents y strategy."},
            {"term": "G-WIRE", "meaning": "un supervisor que COMPONE declara inputs: en cada agente — así el task graph se cablea y corre."},
            {"term": "G-BIND-AGENT", "meaning": "toda referencia uses: agent://<id> resuelve a un agente del catálogo."},
            {"term": "G-CONTRACT", "meaning": "si el agente referenciado declara contract: {inputs, outputs}, el wiring inputs: del miembro solo nombra inputs declarados y cablea todos los required — es lo que valida conectar agente↔agente en el explorer."},
            {"term": "G-DUR", "meaning": "una tool inline que parece outward (muta afuera) debería pedir approval (warning)."},
        ]},
        {"title": "Reglas de tool", "blurb": "lo que garantiza un nodo tool del palette.", "items": [
            {"term": "T-IMPL", "meaning": "la tool declara su impl (módulo:función) y existe."},
            {"term": "T-DUR", "meaning": "una tool outward (muta) declara approval_required."},
            {"term": "G-AGNOSTIC", "meaning": "la impl es PURA: no importa el runtime (agentspan/langgraph) — eso vive en los adapters."},
        ]},
        {"title": "Reglas de relación (las líneas) y port", "blurb": "lo que garantiza una arista al hacerle click.", "items": [
            {"term": "port resuelve en el registry", "meaning": "el port que un agente consumes: existe en el ConnectorKit (sdk/connectorkit/ports.py)."},
            {"term": "G-PORT", "meaning": "los datos externos entran SOLO por un port del ConnectorKit; nunca red directa en el esqueleto."},
        ]},
        {"title": "Reglas de caso (el catálogo \"Probar\")", "blurb": "lo que hace confiable un caso replayable.", "items": [
            {"term": "CASE-REF", "meaning": "todo $ref (seed/ports/golden) del caso resuelve a un archivo existente."},
            {"term": "CASE-TARGET", "meaning": "el target del caso (tool:/agent:/flow:<id>) existe en el catálogo."},
            {"term": "CASE-PORT", "meaning": "todo port que el caso inyecta tiene su fixture vendor (replayable sin red)."},
            {"term": "CASE-GOLDEN", "meaning": "el replay del caso == su golden (caza un golden desactualizado)."},
        ]},
        {"title": "Reglas transversales (el porqué)", "blurb": "propiedades de todo el sistema, no un check único.", "items": [
            {"term": "G-DET", "meaning": "el esqueleto del StateGraph es PURO; el LLM/IO va en nodos marcados. Es lo que hace replayable a un agente."},
            {"term": "sub-ejecución reconstruida", "meaning": "el I/O por-tool que muestra el modal de un nodo NO se persiste en el durable: se RECONSTRUYE replayeando el run() del nodo con tools trazadas sobre su input (sdk.tooltrace). Es fiel por G-DET; revela hasta los loops. Lo amarra la cert (conformance: tools declaradas == realmente invocadas)."},
            {"term": "G-CERT", "meaning": "un agente o tool < C2 no es mergeable ni componible."},
            {"term": "C0-SCHEMA", "meaning": "el manifest no parsea (YAML/schema inválido) — el piso de C0."},
        ]},
        {"title": "Cómo leer el grafo", "blurb": "el resto de las etiquetas y badges.", "items": [
            {"term": "extractor · analyzer · reporter · supervisor", "meaning": "archetype del agente: extrae datos · los procesa · los reporta · orquesta a otros."},
            {"term": "sequential · router · parallel", "meaning": "strategy de un supervisor: cadena en orden · rutea a UNO por $state.route · fan-out + join."},
            {"term": "handoff · swarm", "meaning": "strategy LLM-driven (la ruta la decide el LLM en runtime) — nativa de AgentSpan, aún no implementada."},
            {"term": "uses · agent · consumes", "meaning": "tipo de arista (línea): agente→tool · supervisor→agente · agente→port."},
            {"term": "pure · read · outward", "meaning": "side-effect de una tool: no toca afuera · lee externo · muta (pide approval)."},
            {"term": "demo · variante", "meaning": "estado de catálogo del nodo: ejemplo/proving-ground · variante de verificación (no es el pod de producción)."},
            {"term": "declara X", "meaning": "el agente DECLARA un nivel (certification:) distinto del que el TCK le computa — la verdad es el computado."},
        ]},
    ]}
