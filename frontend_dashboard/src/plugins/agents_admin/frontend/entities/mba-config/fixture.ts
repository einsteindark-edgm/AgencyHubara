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
  excluded: [
    { source: "TOOLS.md", reason: "Mapa de tools del LLM: en MBA se modela como connector tools." },
  ],
  endpoints: [
    { section: "skills", method: "POST", path: "/{entity_id}/agent_config/skills" },
    { section: "business_info", method: "PUT", path: "/{entity_id}/agent_config/business_info" },
    { section: "faqs", method: "POST", path: "/{entity_id}/agent_config/faq" },
    { section: "settings", method: "PUT", path: "/{entity_id}/agent_config/settings" },
  ],
};
