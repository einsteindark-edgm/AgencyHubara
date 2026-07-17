ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDuRdHjMDtKzrMPdh9+aYBVvyW1OHKxYqrRelQ+lD9u0 hubara-ops"

# AMI PINNEADO (incidente 2026-07-08): con ami_id vacío, el data source
# `most_recent` resuelve "el último AL2023 de HOY" — cada release de Amazon
# convertía el próximo apply en un `forces replacement` de las 3 cajas (y el
# vault vivía en el root EBS → pérdida total). Upgrade de AMI = cambiar este
# pin A PROPÓSITO, con snapshot previo. (robotocore lo pisa con su tfvars.)
ami_id = "ami-07ab13a91f7d7a8af" # AL2023 x86_64 — el que corren las cajas desde 2026-07-08

tenants = {
  hubara = {
    instance_type   = "t3.medium"
    domain          = "98-88-237-207.sslip.io" # ← poné tu dominio REAL si lo tenés; sino lo dejamos y arreglamos DNS al final
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,marketing,order_sentinel,orders,reengagement,system_map"
    root_volume_gb  = 30
  }
}

observability = {
  instance_type  = "t3.large"
  root_volume_gb = 60
}
