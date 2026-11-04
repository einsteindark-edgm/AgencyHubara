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
  # El 2.º toque del remarketing (quien vio el catálogo y se calló) cuenta lo
  # práctico, y estas son las ÚNICAS cifras que puede citar: forge las llena al
  # forjar con `commerce` de client.yaml (no escribas las cifras aquí). Si la
  # tienda quiere contar sus tiempos de entrega, súmalos a `practical_facts`.
  practical_facts: >-
    Envío: tarifa mínima en {{shipping_local_zone}} {{shipping_rate_local_cop}} y a nivel nacional
    {{shipping_rate_national_cop}} (el valor definitivo se confirma al despachar según el tamaño y
    peso del paquete). Pago contra entrega: compras desde {{cash_on_delivery_min_cop}} en productos.
    También pago anticipado por Nequi o link de pago, dentro de este chat.
  hook_examples_practical:
    - "Por si te sirve: el envío en {{shipping_local_zone}} arranca en {{shipping_rate_local_cop}} y a nivel nacional en {{shipping_rate_national_cop}} (tarifa mínima). Si tu compra es desde {{cash_on_delivery_min_cop}} puedes pagar contra entrega 🤍 ¿A qué ciudad lo enviaríamos?"
  # Cómo corrige el bot un precio que dijo mal o una suma que el cliente hizo
  # mal: con calidez y sin regaño. Ajusta el tono y los emojis a la tienda; los
  # ejemplos van con huecos, sin cifras (el bot copia el ejemplo).
  correction_tone: >-
    Corrígelo con calidez y sin culpar nunca al cliente: un tono suave, un emoji amable
    (🙈, 🤍 o 😊) y nada que suene a regaño, como «ojo», «te corrijo» o «aclaro». Si el error
    fue tuyo, asúmelo.
  correction_examples:
    - "¡Ay, qué pena contigo! 🙈 Me equivoqué con el precio: [el producto] está en [el precio del catálogo], así que [la cantidad] queda en [el total] 🤍"
  sum_correction_examples:
    - "¡Casi! 😊 Te cuento cómo da la cuenta: [un producto] a [su precio] y [el otro] a [su precio] suman [el total] 🤍"
