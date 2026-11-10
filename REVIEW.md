# REVIEW.md — cómo se revisa un PR

Lo usan el revisor con contexto limpio que lanza el Taller del Ciclo Colossus (`hubara-dev:hubara-gate-reviewer`) y cualquier persona que revise un PR de este repo. Quien escribió el cambio no lo aprueba.

Se revisa contra dos cosas: el **contrato** de la tarjeta (en el buzón privado `hubara-ciclo`, enlazado en el PR) y estas reglas. Cada hallazgo dice archivo y línea, qué falla, por qué importa y qué arreglo propone.

## 1. ¿Hace lo que dice el contrato, y nada más?

- La meta del contrato se puede comprobar con lo que trae el PR (prueba, `decisions check`, golden, laboratorio). Si falta una medición que solo puede correr el operador, el PR lo dice.
- Lo que el contrato dice que no puede empeorar tiene su prueba o su cifra.
- No hay cambios fuera del alcance: nada de refactors «de paso» ni archivos que la tarjeta no pide.

## 2. El test rojo

- Existe una prueba que falló antes del arreglo por la razón correcta (no por un import) y pasa después. La evidencia muestra las dos salidas.
- El arreglo no tocó esa prueba. Si la prueba cambió después de su commit, es un hallazgo bloqueante.
- La prueba asierta comportamiento observable, no implementación.

## 3. El lugar del arreglo (gotchas 14 y 16 de `CLAUDE.md`)

- Una decisión del bot se arregla en el paquete de decisión, con versión nueva, su prueba y su huella. Un `if`, un regex o una guarda en el ingest, una tool o el egreso es un hallazgo, salvo que al motor le falte algo genérico (un builtin con su prueba).
- Lo que el bot promete hacer va en el detector de promesas, con su caso en `test_promised_actions.py`.
- Una guarda nueva que rechace una tool trae un test con el caso legítimo que no debe frenar.
- Los datos de un pedido se leen de `OrderFacts` (gotcha 13), nunca de copias del vault.

## 4. Durabilidad y arquitectura

- Reglas DEHA (R-DET, R-JSON, R-STATELESS, R-HEARTBEAT, R-DIP). Un cambio en la secuencia de comandos de un workflow va detrás de `workflow.patched(...)` con su prueba de replay.
- Los plugins importan `src.sdk`, nunca `src.platform` ni vendors. FSD estricto en el frontend.
- Nada de archivos `protected: true` de `hubara_agency/.hubara/spinal-files.yaml` sin ADR.
- Los gates de §8 de la skill `hubara-plugin-developer` corren en verde para lo que se tocó.

## 5. Datos y seguridad (este repo es público)

- Sin teléfonos, nombres, textos de clientes, ids de pedido reales ni cifras internas del negocio en código, pruebas, fixtures, commits o el cuerpo del PR. Las pruebas usan datos sintéticos.
- Sin secretos ni llaves. La configuración vive en Terraform y paquetes (YAML), no quemada en el código.
- Nada que cambie el bot en producción por fuera del comando del operador (`infra/scripts/bot_control.sh`), ni botones nuevos que lo cambien desde el dashboard (gotcha 15).

## 6. La evidencia

- Es la salida literal de los comandos (pruebas, gates, `decisions check`, golden), no afirmaciones.
- El CI del PR está en verde, o el PR explica por qué un check rojo no viene de este cambio.

## Veredicto

- **Aprobar para el operador**: todo lo anterior se cumple. El merge y la promoción los hace siempre el operador.
- **Pedir cambios**: hay hallazgos. Se listan de más grave a menos grave.
