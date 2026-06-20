variable "region" {
  type    = string
  default = "us-east-1"
}

variable "aws_endpoint" {
  description = "VACÍO = AWS real. http://localhost:4566 = robotocore."
  type        = string
  default     = ""
}

variable "ami_id" {
  description = "AMI a usar. VACÍO = buscar la última Amazon Linux 2023 x86_64. En robotocore pasá un id dummy (ej. ami-12345678) porque el data source no resuelve."
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  description = "Clave pública SSH para el key pair de las cajas (la privada va al GH secret EC2_SSH_KEY). VACÍO = no crear key pair (deploy solo por SSM)."
  type        = string
  default     = ""
}

variable "ssh_ingress_cidrs" {
  description = "CIDRs que pueden entrar por SSH (22) y a la UI de SigNoz (8080). RESTRINGIR a tu IP. 0.0.0.0/0 confía solo en la auth por key — preferí SSM Session Manager."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "image_repo" {
  description = "Imagen del backend en GHCR (sin tag). El deploy le pasa el tag por SSH."
  type        = string
  default     = "ghcr.io/einsteindark-edgm/agencyhubara"
}

# ── Tenants (cajas de app) ──────────────────────────────────────────────────
# Hubara ~4GB → t3.medium ; Vincenzo ~8GB → t3.large (mapeo del doc §4).
variable "tenants" {
  description = "Por tenant: tipo de instancia + dominio público (para el Caddyfile) + plugins habilitados."
  type = map(object({
    instance_type   = string
    domain          = string # dominio público del FastAPI (Caddy auto-TLS)
    enabled_plugins = optional(string, "ads,agents_admin,catalog,chats,eta,orders,system_map")
    root_volume_gb  = optional(number, 30)
  }))
}

# ── Observabilidad (caja SigNoz compartida) ─────────────────────────────────
variable "observability" {
  description = "Caja única de SigNoz para todos los tenants (off-critical-path, doc §3.10)."
  type = object({
    instance_type  = optional(string, "t3.large") # ~8GB; subir a r-family si ClickHouse pide holgura
    root_volume_gb = optional(number, 60)         # disco de ClickHouse = el driver de costo
  })
  default = {}
}

# ── GraphAgents (caja compartida, full durable: postgres + agentspan + app) ──
# off-critical-path (analiza Meta Ads). AgentSpan es la columna vertebral (task
# graph durable) → corre el motor real, no LocalRuntime. self-host (OSS/MIT).
variable "graphagents" {
  description = "Caja única de GraphAgents. t3.large por la JVM de AgentSpan (heap 1536m) + postgres + app; t3.medium es el mínimo justo."
  type = object({
    instance_type  = optional(string, "t3.large")                              # 8GB cómodo; t3.medium (4GB) = budget, tight para la JVM
    root_volume_gb = optional(number, 40)                                      # datos de postgres (estado durable de Conductor)
    image_repo     = optional(string, "ghcr.io/einsteindark-edgm/graphagents") # imagen de la app (≠ la de hubara); agentspan/postgres son oficiales
  })
  default = {}
}
