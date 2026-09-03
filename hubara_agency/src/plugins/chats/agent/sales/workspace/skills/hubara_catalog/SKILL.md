---
description: Identidad de marca Hubara + políticas estables de envío/pago/garantía. NO contiene catálogo de productos (eso vive en las tools search_products / get_product_by_handle).
metadata: {"exoclaw": {"always": false}}
---

# Conocimiento Central de la Empresa

> ⚠️ **Esta skill NO contiene el catálogo de productos.** Para precios, nombres, descripciones e imágenes de productos usa **siempre** la tool `search_products` (búsqueda) o `get_product_by_handle` (detalle exacto). NO inventes precios ni nombres desde tu memoria — el catálogo es dinámico y se actualiza cada 5 minutos desde Medusa.

## IDENTIDAD DE MARCA

- **Ingredientes puros**: cera de palma 100% origen vegetal. Libres de parafinas y toxinas.
- **Proceso**: elaboradas y vertidas a mano en Colombia. Pequeñas variaciones de color/texturas son marcas de autenticidad y origen natural, **no defectos**.
- **Sinestesia**: cada vela cuenta con 3 capas de fragancia (Notas de Salida, Notas de Corazón y Notas de Fondo) que se despliegan orgánicamente en el entorno.

## ENVÍOS Y PAGOS (políticas estables)

- **Tarifas mínimas de envío** 🚚: Bogotá y municipios cercanos $7.900, Nivel Nacional $16.940. El valor definitivo se confirma al despachar según el tamaño y peso del paquete. Bogotá 1 a 2 días hábiles, resto del país 2 a 3 días hábiles.
- **Formas de pago** (infórmalas así, son las TRES únicas):
  - **Contra entrega**: solo compras totales **mayores a $45.000 COP**; el valor se calcula con la transportadora.
  - **Pago anticipado**: por Nequi o llave **3229041190**. Este número es el ÚNICO dato de pago que puedes escribir (sale de esta guía); los datos completos se los envía el sistema al registrar el pedido.
  - **Link de pago**: recargo adicional del **1,5%** sobre la venta pagando con Nequi o Bancolombia, **2,69%** con otros bancos. El link lo genera el equipo tras registrar el pedido — nunca inventes uno.

## POLÍTICAS ADICIONALES

- **Descuento de Bienvenida**: 5% automático para primeras compras a través de la web.
- **Descuento Testimonio**: cupón del 10% para la próxima compra a clientes recurrentes que manden foto/video contando su experiencia por nuestro chat.
- **Garantía**: 48 horas de cobertura desde la fecha de recepción para envíos rotos o defectuosos, siempre y cuando la vela nunca se haya encendido (debe conservar su empaque y tamaño de mecha).

## Cómo conseguir productos / precios

| Necesitas | Tool a usar |
|---|---|
| Listar productos por nombre/aroma | `search_products(q="lavanda")` |
| Filtrar por categoría (aunque venga con typo) | `search_products(q="", category="religosas")` |
| Saber qué categorías existen | `list_categories()` |
| Confirmar precio exacto de un producto que el cliente eligió | `get_product_by_handle(handle="<handle visto en search>")` |
| Sugerir 3 opciones al cliente | `search_products(q="vela", limit=3)` |

**Categorías**: nunca las niegues de memoria — resolvelas con `category=` o mirá `list_categories()`.

**Regla absoluta**: cualquier `handle`, `title` o `price` que menciones al cliente debe venir del último `tool_result`. Si la tool retorna `count: 0`, dile honestamente "no manejamos ese producto" — NO inventes uno que se parezca.
