# Cajas de app por tenant. `domain` = dominio público del FastAPI (Caddy le saca
# auto-TLS). Ajustá los dominios a los reales.
#
# Sizing del doc §4: Hubara ~4GB (t3.medium) · Vincenzo ~8GB (t3.large).

tenants = {
  hubara = {
    instance_type   = "t3.medium"
    domain          = "api.hubara.example"
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"
    root_volume_gb  = 30
  }

  vincenzo = {
    instance_type   = "t3.large"
    domain          = "api.vincenzo.example"
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"
    root_volume_gb  = 30
  }
}

# Caja única de SigNoz (todos los tenants). El disco grande es para ClickHouse.
observability = {
  instance_type  = "t3.large"
  root_volume_gb = 60
}

# RESTRINGÍ esto a tu IP. 0.0.0.0/0 deja SSH/UI-SigNoz accesibles (confiando en key).
# ssh_ingress_cidrs = ["TU.IP.PUBLICA/32"]
