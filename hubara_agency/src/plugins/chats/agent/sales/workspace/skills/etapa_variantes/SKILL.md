---
description: Guion de etapa - selección de variantes. Se inyecta automáticamente cuando hay producto elegido y falta aroma, color o cantidad. NO cargar con load_skill (el sistema la inyecta por etapa).
---

# Etapa: Variantes (aroma / color / cantidad)

El cliente ya eligió producto (está en los DATOS DEL PEDIDO del contexto). Objetivo: completar aroma, color y cantidad SIN loops ni re-preguntas.

## Guía turn-by-turn

1. Revisa los DATOS DEL PEDIDO: qué variante falta. Pide SOLO la siguiente pendiente (una pregunta por mensaje).
2. Para mostrar opciones → `present_variant_picker(variant_type=..., options=[...])`. El picker ES el mensaje completo (tu turno termina ahí); el ack corto ("Anotado, color *Lila* 🤍") va ANTES, junto a la tool call.
3. Cada elección del cliente → `set_order_slot(...)` INMEDIATO en ese turno. Solo con lo que el cliente escribió en SU último mensaje — nunca elijas por él.
4. Si pide recomendación ("¿cuál huele más rico?"): recomienda 2-3 con criterio sensorial (cálido/envolvente vs fresco/cítrico), di cuál destacarías y por qué, y cierra con UNA pregunta. No inventes aromas: solo los del envelope del producto. Para describir un aroma con sus notas reales (salida/corazón/base) → `load_skill("notas_olfativas")`.
5. Cantidad: pregunta simple ("¿Cuántas unidades deseas?"). NO la mezcles con "¿agregamos algo más?" en la misma burbuja — dos preguntas fabrican respuestas ambiguas.
6. Respuesta ambigua ("no solo ese") → clarifica en una línea ("¿O sea que dejamos solo esa? 🤍") antes de actuar.
7. Variantes completas → avanza a datos de envío: "Para coordinar tu envío necesito unos datos 🤍" + `request_shipping_details(order_total_cop, items_summary)`.

## Bordes

- El producto no maneja la opción pedida → dilo directo y ofrece lo disponible UNA vez; a la siguiente señal de avance, asume lo razonable y avanza.
- El cliente quiere agregar OTRO producto → muéstrale 2-3 opciones o el catálogo (`present_products`); nunca insistas con un producto que ya descartó.
- Un atributo no aplica al producto (sin colores) → `set_order_slot` con lo que sí aplica y sigue; no preguntes por variantes inexistentes.
