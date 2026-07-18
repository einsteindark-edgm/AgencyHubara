# Tenant {{company}} — config del provisioning WhatsApp/Meta (forge template).
# Copiar a tenants/{{slug}}.env y completar con los valores REALES del
# Business Manager de {{company}}. NUNCA commitear el .env real.
TENANT={{slug}}

# Business Manager del CLIENTE (recomendado propio, decisión D-6)
BUSINESS_ID=
APP_ID=
APP_SECRET=
WABA_ID=
SYSTEM_USER_TOKEN=
CATALOG_ID=

# Webhook (la EIP/dominio sale del primer deploy — NEXT_STEPS.md F7)
CALLBACK_URL={{api_url}}/api/chats/webhook
VERIFY_TOKEN=

# Línea nueva (no puede estar registrada en otra WABA)
NEW_NUMBER_CC={{phone_cc}}
NEW_NUMBER=
DISPLAY_NAME={{company}}
LANGUAGE=es_ES
