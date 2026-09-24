"""Puerto de percepción: preguntas cerradas y tipadas sobre una conversación.

Plan del laboratorio de conversaciones, §4.2. Un clasificador (Jev de
TypeSafe, u OpenAI con logprobs, los dos por OpenRouter) responde preguntas
`noul` (sí/no), `choice` (una opción) o `score` (posición en una rúbrica) con
su probabilidad. Lo consume el turno de ventas (capas ① y ③, detrás del modo)
y el laboratorio. Los plugins lo importan de `src.sdk.connectorkit`.
"""
