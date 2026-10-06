"""Puerto de percepción: el ORÁCULO del motor de decisiones.

Jev (TypeSafe), por la Decisions API de OpenRouter, responde preguntas
cerradas y tipadas sobre una conversación: `noul` (sí/no), `choice` (una
opción) o `score` (posición en una rúbrica), con su probabilidad. Nunca lanza.
100 % Jev desde el 2026-09-28 (el rival OpenAI del laboratorio se quitó).

Casi nunca cambia: cuestionarios, políticas y umbrales son del motor
(`src/plugins/chats/agent/sales/decisions/`). Los plugins lo importan de
`src.sdk.connectorkit`.
"""
