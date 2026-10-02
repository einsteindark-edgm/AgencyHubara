# Dominio de la tienda {{company}} del paquete `ventas`. PAQUETES_DE_DECISION.md F5.
#
# Lo forjó forge con ejemplos NEUTRALES: reemplázalos por los de la tienda
# (sus productos, sus variantes —talla, color, material…—, su forma de
# hablar). El agente los ve en sus herramientas y en el gancho de
# remarketing; las condiciones del paquete los leen como `dom.campo`. Los
# campos y sus tipos los declara `../builtins.yaml: domain` (DB014 si no cuadra).
# Certificar: cd hubara_agency && uv run python -m src.sdk.cli decisions check
store_name: "{{company}}"
farewell_order_registered: "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a {{company}}."
vocabulary:
  search_examples: "por nombre o categoría"
  product_example: "'el nombre del producto'"
  scent_example: "la opción del catálogo"
  notes_examples: "un detalle de personalización, 'es para regalo'"
  variant_label_examples: "la etiqueta de la variante: talla o color"
  caption_example: "'detalle breve del producto'"
  list_intro_example: "'Estos son nuestros productos:'"
  tax_note: "Impuestos en {{currency}} (suelen ser 0)."
  variant_question: "por la variante que prefiere"
  variant_dimensions: "variante"
  variant_kinds: "según el catálogo"
  variant_reply_examples: "la opción que quiera"
  variant_label_literal_examples: "'la variante'"
  picker_intro_examples: "'Tenemos estas opciones:' o 'Estas son las variantes disponibles:'"
  hook_topic_examples: "'el producto que vio', 'lo que viste'"
  hook_examples:
    - "¡Hola de nuevo! ✨ Quedó pendiente lo que viste — ¿lograste decidirte? 🤍"
    - "Te escribo para retomar lo que te gustó ✨ ¿Te ayudo a cerrar el pedido?"
