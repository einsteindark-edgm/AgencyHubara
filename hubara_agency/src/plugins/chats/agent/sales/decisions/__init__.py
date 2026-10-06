"""Motor de decisiones con Jev (diseño v2, aprobado el 2026-09-28).

Tres capas, cada una cambia a su ritmo:

* Oráculo (`src/platform/perception`, casi nunca cambia): Jev por la Decisions
  API responde preguntas cerradas con probabilidades. Nunca lanza.
* Motor (este paquete, cambia seguido): cuestionarios versionados como datos
  (`questionnaires/`), políticas puras y versionadas (`policies/`), perfiles
  que los juntan (`profiles.yaml`) y el núcleo (`engine.py`).
* Consumidores (casi nunca cambian): el workflow de ventas (solo importa
  `contracts` y `facade`), las tools y el laboratorio. Solo leen decisiones:
  no conocen ni una pregunta de Jev ni un umbral.

Reglas: Jev percibe, el código decide y el LLM redacta. Si Jev falla o tarda,
decide la regla de hoy. Las reglas viven en activities y viajan grabadas en
su resultado: el workflow solo aplica (replay-safe). El contrato solo crece
con campos opcionales.
"""
