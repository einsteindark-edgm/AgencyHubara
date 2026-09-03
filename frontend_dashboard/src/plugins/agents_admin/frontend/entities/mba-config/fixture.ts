/**
 * Fixture con la forma REAL de `GET /api/agents/{id}/mba-config` (recortada).
 * Solo para tests: espeja el DTO `MbaConfigDTO` del backend.
 */
export const MBA_CONFIG_FIXTURE = {
  agent_id: "sales",
  channel: "whatsapp",
  business_info: {
    business_description: "Nombre: Hubara\nIndustria: marca premium colombiana de velas artesanales",
    payment_method: "Contra entrega: solo compras mayores a $45.000 COP.\nPago anticipado: por Nequi o llave 3229041190.",
    delivery_and_shipping: "Envíos a Bogotá: $12.000 a $15.000 aprox. 1 a 2 días hábiles.",
    return_policy: "Garantía: 48 horas de cobertura para envíos rotos o defectuosos.",
    purchase_info: "",
    contact_info: { email: null, hours_of_operation: "America/Bogota", address: null },
    sources: ["USER.md", "skills/hubara_catalog/SKILL.md"],
  },
  settings: {
    rollout_enabled: false,
    ai_audience: "ALLOWLISTED_ONLY",
    handoff: {
      enabled: true,
      message: "Un colega del equipo te responde en este mismo chat 🤍",
      message_selection: "CUSTOM",
    },
    followup: { enabled: false, followup_interval_in_seconds: 900, message: null },
    never_say_phrases: [
      { phrase: "vos", source: "IDENTITY.md" },
      { phrase: "voy a averiguar", source: "AGENTS.md" },
    ],
  },
  skills: [
    {
      title: "persona-y-tono",
      description: "Aplicar en toda conversación: quién es el asesor.",
      skill: "# Eres el Asesor\n\nEres un experto de ventas.",
      char_count: 18204,
      char_limit: 20000,
      over_limit: false,
      sources: ["IDENTITY.md", "SOUL.md"],
    },
    {
      title: "reglas-operativas",
      description: "Aplicar en cada turno.",
      skill: "# Agent rules",
      char_count: 3942,
      char_limit: 20000,
      over_limit: false,
      sources: ["AGENTS.md"],
    },
    {
      title: "guion-sales-script",
      description: "Núcleo del guion conversacional.",
      skill: "# Guion",
      char_count: 20544,
      char_limit: 20000,
      over_limit: true,
      sources: ["skills/sales_script/SKILL.md", "skills/etapa_cierre/SKILL.md"],
    },
  ],
  faqs: [
    {
      question: "¿Cuánto demora el envío?",
      answer: "Bogotá 1 a 2 días hábiles. Resto del país 2 a 3 días hábiles.",
      source: "skills/sales_script/SKILL.md",
    },
  ],
  connector: {
    name: "hubara-commerce",
    description: "API de Hubara: catálogo, pedidos y estado de envío.",
    base_url: "https://<host-publico>/api/mba",
    auth_type: "API_KEY",
    auth_header: "X-API-Key",
    requires_certificate: false,
    tools: [
      {
        name: "check_order_status",
        description: "Cliente pregunta por su pedido (etapa o pago)",
        method: "GET",
        path: "/tools/check_order_status",
        query_parameters: [],
        body_parameters: [],
        bindings: ["WHATSAPP_PHONE_NUMBER"],
        write: false,
        notes: "Lectura: responde desde la fuente de verdad (Medusa / vault).",
        source: "TOOLS.md",
      },
      {
        name: "register_order",
        description: "Cliente tocó '✅ Confirmar' + datos completos",
        method: "POST",
        path: "/tools/register_order",
        query_parameters: [],
        body_parameters: [],
        bindings: ["WHATSAPP_PHONE_NUMBER"],
        write: true,
        notes: "Escritura: el endpoint debe ser idempotente (fingerprint + pre-check).",
        source: "TOOLS.md",
      },
    ],
  },
  ui_skills: [
    {
      title: "request-shipping-details",
      component_type: "flow",
      status: "enabled",
      instruction: "Variantes completas → pedir datos de envío",
      from_tool: "request_shipping_details",
      source: "TOOLS.md",
    },
  ],
  tool_treatments: [
    { llm_tool: "check_order_status", when: "Cliente pregunta por su pedido", treatment: "connector_tool", detail: "GET https://<host-publico>/api/mba/tools/check_order_status" },
    { llm_tool: "request_shipping_details", when: "Variantes completas", treatment: "ui_skill", detail: "UI skill nativa `flow`." },
    { llm_tool: "escalate_to_human", when: "Tabla de triggers", treatment: "native_handoff", detail: "MBA escala solo y usa handoff.message." },
    { llm_tool: "set_order_slot", when: "CADA dato confirmado", treatment: "internal", detail: "Estado interno de Hubara." },
  ],
  excluded: [
    { source: "TOOLS.md#tool:set_order_slot", reason: "Tool interna de Hubara; sin equivalente en MBA." },
  ],
  endpoints: [
    { section: "skills", method: "POST", path: "/{entity_id}/agent_config/skills" },
    { section: "business_info", method: "PUT", path: "/{entity_id}/agent_config/business_info" },
    { section: "faqs", method: "POST", path: "/{entity_id}/agent_config/faq" },
    { section: "settings", method: "PUT", path: "/{entity_id}/agent_config/settings" },
    { section: "connector", method: "POST", path: "/{entity_id}/agent_connectors" },
    { section: "connector_tools", method: "POST", path: "/{entity_id}/agent_connectors/{connector_id}/tools" },
    { section: "ui_skills", method: "POST", path: "/{entity_id}/agent-ui-skills" },
  ],
};
