# Diagnóstico: `create_agent` (LangChain 1.x) y `deepagents` vs GraphAgents

> 2026-07-07. Pregunta del operador: ¿estas abstracciones nos agregan
> fiabilidad o mejoran el desarrollo del workflow de agents? Restricción:
> todo queda DENTRO de nuestra arquitectura (manifests YAML + G-rules +
> AgentSpan/Conductor). Investigado contra docs oficiales y repos (links al
> final).

## Qué son

- **`create_agent`** (langchain 1.x): el loop de agente prebuilt —
  `model + tools + middleware` → devuelve un **grafo LangGraph compilado**.
  Lo nuevo es el **middleware stack** (hooks `before_model` /
  `modify_model_request` / `after_model`) con ~19 built-ins: Summarization,
  Human-in-the-loop (`interrupt_on`), ModelRetry/ToolRetry (backoff
  exponencial), ModelFallback, ModelCallLimit/ToolCallLimit, PII detection,
  To-do list, LLM tool selector, ContextEditing, Filesystem, Subagent, etc.
- **`deepagents`** (v0.6.12, pre-1.0): harness opinionado SOBRE ese loop:
  planning (`write_todos`), filesystem virtual con backends y permisos,
  subagents efímeros (`task` tool), skills (`SKILL.md`, progressive
  disclosure), memoria (`AGENTS.md`), HITL. Pensado para tareas
  **long-horizon** (research multi-paso), no para pipelines deterministas.

## Dónde SÍ nos aportan

1. **Resiliencia del (futuro) nodo LLM.** Hoy casi no tenemos nodos LLM
   (solo el narrate de `ctwa_report`, best-effort L-26). Cuando lleguen
   (redacción del copy de reactivación, priorización fina), `create_agent`
   COMO BUILDING BLOCK DENTRO de un nodo aislado da gratis lo que no
   tenemos formalizado: ModelRetry + ModelFallback + call limits +
   summarization. Eso ES fiabilidad real para la parte no-determinista.
2. **Vocabulario middleware ≈ nuestros guardrails, versión librería.**
   `model_call_limit` ≈ tope de presupuesto del nodo `plan`;
   `interrupt_on` ≈ nuestro `@human_task` → HUMAN task de Conductor;
   `tool_retry` ≈ retry policies de activities. Para futuros agentes CON
   loop, el mapeo 1:1 existe — no habría que inventar conceptos.
3. **Patrones robados sin adoptar la lib**: el spawn de subagents con
   contexto aislado y el "offload de tool results grandes al filesystem"
   son patrones que nuestro supervisor podría adoptar como técnica, sin la
   dependencia.

## Dónde CHOCAN con nuestra arquitectura

1. **Contra G-DET (el choque de fondo).** Nuestra fiabilidad viene del
   golden-replay: esqueleto puro, fixture → output EXACTO, certificable.
   `create_agent`/`deepagents` son loops **model-driven**: el "plan" lo
   decide el LLM en runtime (`write_todos` = plan LLM; nuestro nodo `plan`
   = política determinista testeada con guardrail de presupuesto).
   Sustituir el Window Strategist (o cualquier analyzer/extractor) por un
   deep agent **REDUCE** la fiabilidad: no hay golden posible, solo evals.
2. **Contra L-15 (Conductor).** El loop interno de `create_agent` usa
   conditional edges (model↔tools) — exactamente lo que documentamos que
   CUELGA en Conductor/AgentSpan. Un agente así NO corre como grafo
   multi-nodo en nuestra caja durable; solo (a) LocalRuntime, o (b)
   envuelto ENTERO como un nodo passthrough (pierde durabilidad por paso).
3. **Contra G-PORT.** El FilesystemMiddleware/shell de deepagents le da IO
   al LLM; nuestra ley es data-por-payload y cero IO desde el grafo. Útil
   para research agents, tóxico para pipelines con seams.
4. **Churn.** langchain 1.x completo + deepagents 0.6.x (API moviéndose)
   vs nuestro footprint mínimo (langgraph pelado). Cada middleware es
   superficie nueva que el TCK no cubre.

## Conexión con nuestros YAMLs — SÍ es viable, y barata

- `capability: graphs.<x>:build` ya admite que `build()` devuelva
  CUALQUIER grafo LangGraph compilado — **incluido el de `create_agent`**.
  El seam existe; cero cambio de runtime local.
- Las tools del catálogo ya scaffoldean `adapters/langgraph.py` — el
  adaptador natural para la lista `tools=[...]` de `create_agent`.
  G-AGNOSTIC se mantiene (la impl pura no sabe de langchain).
- El mapeo declarativo sería un `ext` NUEVO del manifest:

  ```yaml
  archetype: researcher          # arquetipo NUEVO, perfil P-29 propio
  agent_loop:
    model: sales-agent           # alias litellm
    middleware:
      - model_retry: {max_retries: 3}
      - model_fallback: [gemini-multimodal]
      - model_call_limit: {run_limit: 20}
      - summarization: {trigger: 0.8}
    interrupt_on: {spend_tools: approval}   # ≈ G-DUR
  ```

  con su check (regla de oro: campo nuevo ⇒ check nuevo) y un loader
  `sdk/agent_loop.py` que arma el `create_agent` desde el manifest.
- **El costo real no es el YAML — es la certificación**: el TCK actual
  certifica golden-replay; un loop no puede pasarlo. Habría que estrenar el
  nivel **C3 (conducta)** que hoy está reservado: certificación por evals
  (rubric grading), no por replay.

## Veredicto

| Caso | Recomendación |
|---|---|
| Window Strategist / analyzers / extractors / funnel | **NO adoptar.** El golden-replay + guardrails + autoridad hubara-side ya dan más fiabilidad que un loop LLM. Nada que ganar, determinismo que perder. |
| Nodo LLM aislado (copy de reactivación, narrate) | **SÍ, cuando llegue el caso**: `create_agent` DENTRO del nodo, con ModelRetry+Fallback+limits; golden con fixture de respuesta (L-26 se mantiene). |
| Futuro agente de research long-horizon (análisis exploratorio de ads/mercado) | **deepagents es el candidato**, en LocalRuntime o caja aparte (no Conductor, por L-15), con archetype `researcher` + cert C3 por evals. |
| Puente YAML | Factible y alineado (build() + adapters existentes + ext `agent_loop`). **No construirlo hasta tener el primer caso de uso real** — sería harness sin consumidor. |

Una línea: *estas libs hacen más fiable lo NO-determinista; nuestra
arquitectura hace fiable lo determinista. No compiten — se tocan solo en el
nodo LLM, y ahí sí conviene usarlas.*

## Fuentes

- https://docs.langchain.com/oss/python/langchain/middleware/built-in
- https://www.langchain.com/blog/agent-middleware
- https://docs.langchain.com/oss/python/deepagents/overview
- https://github.com/langchain-ai/deepagents (v0.6.12)
- https://www.langchain.com/deep-agents
