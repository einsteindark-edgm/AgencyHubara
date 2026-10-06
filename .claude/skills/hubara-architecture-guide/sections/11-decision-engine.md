# Sección 11 — El motor de decisiones (paquetes de decisión + Jev)

> **Cuándo leer esto:** la HU o la task cambia CÓMO DECIDE el bot (si el
> cliente confirmó la compra, pidió la baja, solo agradece; si un texto es para
> el cliente; la zona de envío; si escribirle de nuevo; la etiqueta de cierre…),
> o es un bug de producción del tipo «el bot decidió mal», o toca
> `chats/shared/decisions/bundles/`, `chats/agent/sales/decisions/` o
> `src/platform/decisions/`.
> **Pre-requisito:** ninguno. **Tamaño:** ~9 KB.
> **ADR:** `docs/adr/2026-10-02-decision-engine-in-agent-harness.md` (y
> `ADR-2026-10-01-decision-bundles.md` en la raíz).
> **Guía con diagramas para humanos:** `docs/motor-de-decisiones/index.html`.
> **Diseño completo:** `PAQUETES_DE_DECISION.md`. **Receta de agente:**
> `hubara-dev/skills/hubara-plugin-developer/references/06-decision-bundles.md`.

---

## §1. La regla de oro (no negociable)

**Un cambio o un bug de una decisión se resuelve en el paquete de decisión, no
en el lugar que decide.** Otra pregunta para Jev, otros criterios, otro umbral,
otra fila de la tabla o un ejemplo nuevo, en una **versión nueva** del paquete.
Nunca un `if`, un regex, una lista de palabras o una guarda nueva en el ingest,
una tool, el egreso, remarketing o un use case. Código solo si al motor le
falta algo genérico (un dato que la entrada no trae, un cálculo que CEL no
hace), y entonces en **UN builtin** con su prueba y su entrada en el catálogo.

Es exactamente lo que `main` hacía antes del motor (#371 tocó 9 archivos de
código para el cupo del cupón y la guarda de enumeración; #376, 6 archivos
entre código y `SOUL.md`). El operador lo prohibió explícitamente: el proyecto
evoluciona modificando prompting y reglas.

## §2. Las cuatro capas

| Capa | Qué es | Dónde | Cambia… |
|---|---|---|---|
| L4 · Dominio de la tienda | nombre, despedida, vocabulario del asesor | `bundles/<id>/domain.yaml` | seguido (dato) |
| L3 · Paquete de decisión | una capacidad por YAML (pregunta, umbrales, tabla `when/then` en CEL, regla de respaldo, piso, ejemplos) + `turn.yaml` (ráfaga ①, contrato asunto → tools, verificación ③, guía) | `bundles/<id>/capabilities/*.yaml`, `turn.yaml` | seguido (dato) — **aquí se arreglan los bugs** |
| L2 · Catálogo | builtins con firma, `capabilities:` que el código pide, `required_rules/floors`, vocabulario del turno, `places:` y `about:` | `bundles/builtins.yaml` | poco (contrato) |
| L1 · Motor | compila, evalúa CEL con tipos, pregunta a Jev, aplica pisos, registra | `src/platform/decisions/` (fachada `src.sdk.decisionkit`) + `decide()` | casi nunca (código) |

Paquetes de ventas: `hubara_agency/src/plugins/chats/shared/decisions/bundles/`
(los leen ventas y remarketing). Order Sentinel: su propio catálogo y paquete
`centinela` en `order_sentinel/agent/decisions/bundles/`.

## §3. El enchufe único

Todo lugar pide la decisión **por nombre** al resolutor:

```python
# tools/order_registration.py (desde una tool, vía guards)
from src.plugins.chats.agent.sales.decisions.guards import CiudadDeEnvio, capability, decide_for_session

zone = await decide_for_session(
    capability("zona_de_envio"),
    CiudadDeEnvio(ciudad=order_shipping.city),
    session_id=ctx.session_key,
    vault_dir=self._vault_dir,
)
```

- `registry.capability(nombre)` toma la capacidad del paquete activo
  (`SALES_DECISIONS_BUNDLE`, nace en Terraform `tenants.<t>.lab.decisions_bundle`).
  Si el paquete no la trae es error (DB003), nunca una clase de Python.
- `decide()` (`decisions/capabilities/__init__.py`): la regla de hoy → según el
  modo de la conversación (`bots.bot_for_session`) pregunta o no a Jev → la
  tabla → el piso → el `Verdict`. Nunca lanza por Jev: sin respuesta, con duda,
  con otro modelo o con el paquete roto, decide la regla (`by=respaldo`).
- El veredicto queda en `<vault>/<sesión>/evals/decisions.jsonl` si Jev
  participó, más métricas y la cola de desacuerdos regla ↔ Jev.

**Lugares que deciden hoy** (si tu task toca uno de estos archivos, piensa
primero si el cambio es de una decisión): `decisions/readings.py` (ingest:
compra, retoma, baja, cortesía, acuse, cupón, fuera de catálogo),
`activities/build_prompt_stage.py` (cantidad), `decisions/engine.py` +
`turn.yaml` (el turno), `tools/*.py` y `decisions/guards.py` (categoría, color,
ítem, datos, zona, selector, monto, persona, destinatario por oración),
`decisions/egress.py` y `activities/variant_enumeration_guard.py` (egreso),
`activities/turn_trace.py` (afirmación), `decisions/remarketing_context.py`,
`decisions/contact.py`, `decisions/cierre.py`. La lista viva, con lo que
resuelve cada una, es `builtins.yaml: places/about` (y Agents → Calidad LLM →
Motor de decisiones).

## §4. Modos (quién decide hoy)

`off` = la regla de hoy · `shadow` = decide la regla, Jev se pregunta y se mide
· `canary` = Jev en las conversaciones de prueba · `on` = Jev. Por capacidad,
guardado en el vault, dentro del techo de Terraform
(`SALES_CAPABILITIES_CEILING`). Subir exige la vara
(`decisions/capability_rollout.py`: 7+ días en sombra de 14, 100+ decisiones,
< 1 % de caídas, p95 < 1,5 s, 20+ desacuerdos calificados con Jev ganando);
bajar nunca se bloquea. **El modo es de la capacidad, no de la versión del
paquete**: si estaba encendida, una versión nueva actúa apenas entra.

## §5. Triage de un bug de decisión (por el veredicto, no por intuición)

| El veredicto dice | Qué falló | Dónde se arregla |
|---|---|---|
| `by=jev`, Jev seguro pero al revés | la pregunta o sus criterios | `text`/`criteria`/opciones (versión nueva); se prueba en laboratorio y sombra |
| `by=jev`, Jev bien pero bajo el umbral | umbral o fila | `thresholds`/`decide` + ejemplos en el borde |
| `reason=duda` muy seguido | pregunta que mezcla dos cosas | partir la pregunta, ajustar la tabla |
| `reason=bundle_error` | la tabla lee una respuesta que no llegó | fila `!('x' in p)` → `doubt` primero + ejemplo |
| `reason=no_question` / falta un dato | la entrada no trae lo que Jev necesita | builtin de estado/vista/opciones (código genérico, con prueba) |
| `by=reglas` (off o sombra) | decidió la regla de hoy | subir el modo con la vara, o corregir el builtin de la regla si sigue off |
| `by=piso` | el piso corrigió a Jev a propósito | casi nunca es bug; los obligatorios no cambian (DB012) |
| `timeout` / `http_*` / `model_changed` | Jev no contestó u otro modelo | infraestructura / perfil del oráculo |
| hace falta una decisión nueva | capacidad nueva | catálogo (`capabilities` + `about` con su `where`) + YAML + ejemplos + UN `capability("x")`; nace off → sombra |
| el bot redactó mal sin decisión fallida | no es decisión | `turn.yaml: guide`/`nudge`, `domain.yaml`; `workspace/SOUL.md` solo si es conducta general |
| un hecho mal (precio, cupo, stock, envío, etapa) | no es decisión | donde vive el dato, una sola vez (lo verificado manda) |

## §6. Procedimiento (TDD sobre datos)

1. **Rojo:** copia del paquete (`bundles/ventas-2/` → `bundles/ventas-3/`,
   `id` = carpeta, `version` + 1); en `capabilities/<x>.yaml` las `answers` del
   veredicto (anonimizadas) como `examples:` con el `expect` correcto →
   `decisions check` da DB010. Si lo que falló es que Jev entendió mal, el
   ejemplo no lo prueba (le da las respuestas a la tabla): la evidencia es la
   corrida del laboratorio y la sombra.
2. **Verde:** el cambio mínimo; `cd hubara_agency && uv run python -m src.sdk.cli decisions check`.
3. **Huella y prueba de versión:** la huella del paquete nuevo en
   `tests/plugins/test_decision_bundles_published.py` y una prueba de «es el
   anterior + este cambio y nada más» (patrón `test_decisions_ventas_2.py`). Un
   experimento de esta tienda va en `deletes` de `forge/manifest.yaml`.
4. **Medir:** laboratorio, «Paquete a comparar» → `B@ventas-N` contra el banco real.
5. **Promover:** el paquete ya en la imagen; `tenants.<t>.lab.decisions_bundle`
   en `infra/terraform/platform/tenants.auto.tfvars`; `terraform apply` de
   platform (operador); dispatch de Backend deploy; los procesos registran
   `decisions.bundle_ready`. Volver atrás = el id anterior.

## §7. Lo que no se hace (los revisores lo marcan)

- Lógica de decisión nueva en un lugar (`if`/regex/lista de palabras/umbral
  sobre el texto del cliente o del bot) → `DECISION-OUTSIDE-BUNDLE`.
- Editar una versión publicada del paquete (su huella sha256 está congelada) →
  `PUBLISHED-BUNDLE-EDITED`.
- Nombrar o importar una clase de capacidad (`Baja()`, `PERSONA`) →
  `CAPABILITY-CLASS-REFERENCED` (además, `test_decisions_registry.py`).
- Cargar paquetes en código de workflow (R-DET): solo ingest, tools y activities.
- Importar `src.platform.decisions` desde un plugin (P-28): `src.sdk.decisionkit`.
- Capacidad nueva sin `about`/`where` en el catálogo (DB003) o sin un ejemplo
  por fila y por borde (DB010).
- Escribir un piso en el YAML o cambiar la baja legal / el relevo (DB012).
- Un ejemplo de la tienda en el código de una tool: va en `domain.yaml: vocabulary`.
- `coverage: {}` en el turno pensando que «no se juzga».

## §8. Mejorar el motor mismo (cuando sí es código)

- **Builtin nuevo:** función + entrada en `builtins.yaml` + prueba
  (`test_the_catalog_and_the_code_declare_the_same_builtins`). Si arrastra
  Temporal, el ingest o el catálogo: `bundled_ingest.py` /
  `bundled_catalog.py` / `bundled_egress.py` + `LAZY_BUILTINS`.
- **Formato nuevo del paquete:** `src/platform/decisions/` + pruebas en
  `tests/platform/decisions/` + su código del certificador; si cambia el
  significado de un paquete, sube `engine_contract` (el viejo queda en
  `SUPPORTED_CONTRACTS`).
- **Migrar comportamiento que todavía es código:** primero la foto de
  paridad, después el YAML que la iguala.

---

**Fin sección 11.** Si esta sección contradice al código, gana el código
(`PAQUETES_DE_DECISION.md` y `06-decision-bundles.md` se mantienen al día).
