# Test plans — bundles, estilo `.xctestplan`

El panel **Testing** de VS Code corre las mismas suites que
`/hubara-gates` y `/graphagents-gates` — compiler, certificación TCK,
arquitectura, golden replays, integration — con granularidad por-test vía
JUnit.

Un **test plan** (`test-plans/*.hubaraplan.yaml`) es un bundle: qué suites
corren juntas. Hay 5 de fábrica — Compilador, Certificación C2,
Arquitectura, Golden replays, Pre-PR completo — seleccionables desde la
status bar (`$(beaker) Plan: …`) o desde el picker nativo de profiles del
Test Explorer.

Agregar un plan propio es un YAML nuevo en `test-plans/` con `include`/
`exclude` por tag o por id exacto de suite.

[Elegir y correr un plan](command:hubara.selectPlan)
