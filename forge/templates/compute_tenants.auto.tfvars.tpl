# Compute de {{repo_name}} — generado por forge. UN solo tenant: {{slug}}.
# 1) ssh-keygen -t ed25519 -C "{{slug}}-ops" → pegar la pública acá y la
#    privada al secret EC2_SSH_KEY del repo (NEXT_STEPS.md F2).
# 2) domain: tras el primer apply, tomar la EIP y poner "<ip-con-guiones>.sslip.io"
#    (o el dominio propio) → segundo apply para que Caddy saque TLS.
ssh_public_key = "TODO-SSH-PUBLIC-KEY {{slug}}-ops"

# AMI de AL2023 x86_64 PINNEADA (lección incidente 2026-07-08 del proyecto
# madre: un data source most_recent reemplaza cajas y destruye el vault).
ami_id = "ami-07ab13a91f7d7a8af"

tenants = {
  {{slug}} = {
    instance_type   = "t3.large"
    domain          = ""
    enabled_plugins = "{{enabled_plugins}}"
    root_volume_gb  = 30
  }
}
