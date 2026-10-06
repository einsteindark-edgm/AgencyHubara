# ADR-2026-10-01 — Paquetes de decisión: YAML tipado + CEL + certificador

- **Estado:** aceptado (F1 en implementación)
- **Contexto completo:** `PAQUETES_DE_DECISION.md`

## Contexto

Las 22 capacidades del motor de decisiones son clases de Python con la
pregunta a Jev, los umbrales y la tabla de decisión adentro. Cambiar una
pregunta exige desplegar código y llevar el motor a otra tienda exige editar
código de dominio (velas, Colombia). Queremos que la "inteligencia" del motor
sea un artefacto versionado, certificado antes de desplegar e intercambiable
por tienda, y que los agentes que lo escriban no puedan introducir llaves o
tipos inválidos sin que algo lo rechace.

## Decisión

1. Cada capacidad se describe en YAML, validado por modelos Pydantic v2
   estrictos (`extra="forbid"`), con esquema JSON para el editor.
2. Las condiciones de decisión se escriben en **CEL** y se compilan con
   **`cel-expr-python`** (implementación oficial de Google sobre cel-cpp)
   contra un entorno tipado (`p`, `choice`, `conf`, `th`, `rule`).
3. Lo que no cabe en una fila de decisión es un **builtin** del motor,
   catalogado en `builtins.yaml` con su firma.
4. Un **certificador** (`src.sdk.cli decisions check`) rechaza el paquete por
   estructura, referencias, tipos, filas inalcanzables y ejemplos que fallan.
5. Todo corre **en el mismo proceso** de Python. Las condiciones pasan por
   un puerto (`ExpressionPort`) para poder cambiar de implementación.

## Alternativas descartadas

- Go o Rust como servicio aparte: sin ventaja de verificación (los bindings
  de Python envuelven los mismos núcleos), 10–100× más latencia por
  decisión, un proceso más en una caja de 4 GB, y versiones desalineadas.
- Rego/regorus, Cedar, GoRules ZEN, CUE, Pkl, KCL, DMN, JSON Logic, Starlark,
  Jsonnet: ver `PAQUETES_DE_DECISION.md` §4.

## Consecuencias

- Dependencia nueva: `cel-expr-python` (Apache-2.0, 0.1.x, ruedas para
  Python 3.11–3.14 en Linux/macOS x86 y ARM). Riesgo mitigado por el puerto.
- Los pisos de seguridad siguen en código y el motor los aplica aunque el
  paquete no los pida.
- Migración capacidad por capacidad, con paridad contra la clase actual como
  compuerta.
