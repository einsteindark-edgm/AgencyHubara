# ADR 2026-10-02 — Los agentes de desarrollo, arquitectura y revisión conocen el motor de decisiones

- **Status**: Accepted
- **Date**: 2026-10-02
- **Context surfacing**: pedido del operador al cerrar el motor de decisiones
  (PR #372): «que no se vuelva a la forma anterior que tenemos en producción
  main de empezar a meter código en muchos lados; la idea es que solo
  modificando prompting y motor de reglas podamos evolucionar el proyecto».
- **Protected files touched**:
  `.claude/skills/hubara-architecture-guide/SKILL.md`,
  `.claude/skills/hubara-architecture-guide/sections/10-cookbook.md`,
  `.claude/skills/hubara-architecture-guide/sections/11-decision-engine.md` (nuevo),
  `.claude/skills/hubara-implementer-archon/SKILL.md`,
  `.claude/skills/hubara-tech-refiner-archon/SKILL.md`.

## Contexto

Desde #372 las decisiones del bot (¿confirmó la compra?, ¿pidió la baja?, ¿el
texto es para el cliente?, ¿qué zona de envío?…) son capacidades de un paquete
de decisión: YAML + CEL, versionado, certificado (`decisions check`) y elegido
por Terraform. Los lugares del código las piden por nombre
(`capability("baja")`) y el veredicto dice quién decidió, por qué y con qué
paquete. Guía con diagramas: `docs/motor-de-decisiones/index.html`.

Pero los agentes que programan, planifican y revisan no lo sabían:

- el guide de arquitectura (que leen el refinador, los planificadores y el
  implementador del pipeline) no tenía una sección del motor;
- el refinador no distinguía una decisión (paquete) de un hecho (dato) o de una
  redacción (turno/dominio), así que una HU «el bot confirma compras que no
  son» terminaba planificada como código en el ingest;
- los revisores del pipeline y el `hubara-gate-reviewer` no buscaban lógica de
  decisión nueva en los lugares (un `if`, un regex o una lista de palabras
  sobre el texto del cliente), ni versiones publicadas editadas;
- el panel de compuertas de `hubara-dev` no corría `decisions check`.

Con eso, el primer bug de decisión en producción volvía a arreglarse como en
`main` (#371 tocó 9 archivos de código; #376, 6 entre código y prompt).

## Decisión

1. **Guide de arquitectura**: sección nueva `sections/11-decision-engine.md`
   (las capas, el enchufe único, la regla de oro, el triage por veredicto, el
   procedimiento de versión nueva y promoción, lo que no se hace). Fila en la
   tabla de `SKILL.md` y receta en `10-cookbook.md`.
2. **Refinador** (`hubara-tech-refiner-archon`): carga la sección 11 cuando la
   HU habla de cómo decide el bot, y el `hu-refinada.md` clasifica cada cambio
   de comportamiento como decisión (paquete), hecho (dato) o redacción (turno o
   dominio) antes de listar archivos de código.
3. **Implementador** (`hubara-implementer-archon`): carga la sección 11 si la
   task toca un lugar que decide o `decisions/bundles/`.
4. **Revisores del pipeline** (`.archon/commands/`, no protegidos): DEHA suma la
   categoría «motor de decisiones» (`DECISION-OUTSIDE-BUNDLE`,
   `PUBLISHED-BUNDLE-EDITED`, `CAPABILITY-CLASS-REFERENCED`); test-coverage
   exige ejemplos en los bordes y la prueba de versión; plugin-system revisa
   catálogo, huella y forge; el premortem suma hipótesis del motor; la rúbrica
   del evaluador corre `decisions check` y castiga la lógica de decisión fuera
   del paquete como regla mayor.
5. **`hubara-dev`** (no protegido): el skill desarrollador, sus tres subagentes,
   el panel `/hubara-gates` (suma `decisions check`), la inyección de reglas al
   iniciar sesión y un aviso del `tdd-guard` al editar un lugar que decide.

No se agrega un gate automático que prohíba `if`/regex en los lugares: no hay
una forma determinista de distinguir lógica de decisión de lógica legítima del
lugar sin falsos positivos. La defensa es la de los revisores (pipeline y
`hubara-gate-reviewer`), el aviso del hook y el triage del veredicto.

## Consecuencias

- Un bug de decisión se arregla en una versión nueva del paquete (rojo con un
  `examples:` → DB010, verde, huella, laboratorio `B@paquete`, Terraform).
- El código solo crece en el motor cuando falta algo genérico (un builtin con
  su prueba y su entrada en el catálogo), nunca en el lugar que decide.
- Los cambios a `.claude/skills/hubara-*` de este ADR necesitan el label
  `architecture-change` en el PR (lo pone un humano con review).
