# Consumido por backend-deploy.yml (host por tenant) y observability-deploy.yml.

output "app_hosts" {
  description = "Por tenant: IP pública (EIP) + dominio. backend-deploy SSHea acá."
  value = {
    for t, m in module.app : t => {
      public_ip   = m.public_ip
      domain      = m.domain
      instance_id = m.instance_id
    }
  }
}

output "observability_host" {
  description = "IP pública de la caja SigNoz (UI por túnel/admin CIDR; OTLP solo desde las apps)."
  value = {
    public_ip   = module.observability.public_ip
    instance_id = module.observability.instance_id
    otlp_grpc   = "${module.observability.private_ip}:4317"
    otlp_http   = "${module.observability.private_ip}:4318"
  }
}
