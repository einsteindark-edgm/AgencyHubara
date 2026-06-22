ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDuRdHjMDtKzrMPdh9+aYBVvyW1OHKxYqrRelQ+lD9u0 hubara-ops"

tenants = {
  hubara = {
    instance_type   = "t3.medium"
    domain          = "api.hubara.example"   # ← poné tu dominio REAL si lo tenés; sino lo dejamos y arreglamos DNS al final
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"
    root_volume_gb  = 30
  }
}

observability = {
  instance_type  = "t3.large"
  root_volume_gb = 60
}
