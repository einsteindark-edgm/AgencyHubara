# AMI Amazon Linux 2023 (salvo override). En robotocore el data source no resuelve
# → pasá var.ami_id dummy.
data "aws_ami" "al2023" {
  count       = var.ami_id == "" ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# Key pair opcional (la privada va al GH secret EC2_SSH_KEY). Sin clave → deploy
# solo por SSM Session Manager.
resource "aws_key_pair" "ops" {
  count      = var.ssh_public_key != "" ? 1 : 0
  key_name   = "agencyhubara-ops"
  public_key = var.ssh_public_key
}

locals {
  ami_id   = var.ami_id != "" ? var.ami_id : try(data.aws_ami.al2023[0].id, "")
  key_name = var.ssh_public_key != "" ? aws_key_pair.ops[0].key_name : null
}

# ── Caja de app por tenant ──────────────────────────────────────────────────
module "app" {
  source   = "./modules/app-instance"
  for_each = var.tenants

  tenant            = each.key
  region            = var.region
  ami_id            = local.ami_id
  instance_type     = each.value.instance_type
  domain            = each.value.domain
  enabled_plugins   = each.value.enabled_plugins
  root_volume_gb    = each.value.root_volume_gb
  key_name          = local.key_name
  ssh_ingress_cidrs = var.ssh_ingress_cidrs
  image_repo        = var.image_repo
  use_local         = local.use_local
}

# ── Caja de observabilidad (SigNoz, compartida) ─────────────────────────────
# Solo acepta OTLP (:4317/:4318) desde las SGs de las cajas de app — nunca público.
module "observability" {
  source = "./modules/observability-instance"

  region                 = var.region
  ami_id                 = local.ami_id
  instance_type          = var.observability.instance_type
  root_volume_gb         = var.observability.root_volume_gb
  key_name               = local.key_name
  ssh_ingress_cidrs      = var.ssh_ingress_cidrs
  app_security_group_ids = [for m in module.app : m.security_group_id]
  use_local              = local.use_local
}

# ── Caja de GraphAgents (full durable, compartida) ──────────────────────────
# postgres + agentspan + app. :6767 abierto a las SGs de app (puente fase-B);
# :8900/:6767 admin; postgres nunca expuesto.
module "graphagents" {
  source = "./modules/graphagents-instance"

  region                 = var.region
  ami_id                 = local.ami_id
  instance_type          = var.graphagents.instance_type
  root_volume_gb         = var.graphagents.root_volume_gb
  key_name               = local.key_name
  ssh_ingress_cidrs      = var.ssh_ingress_cidrs
  app_security_group_ids = [for m in module.app : m.security_group_id]
  image_repo             = var.graphagents.image_repo
  use_local              = local.use_local
  # 10 min de idle (no el default 20): el ciclo de reengagement despierta la
  # caja cada 45 min y el agente trabaja segundos-minutos — la cola idle era
  # ~80% del costo (ahorro 2026-08-04). OJO: esto viaja por user_data →
  # cloud-init SOLO corre en el primer boot: la caja VIVA se ajusta editando
  # IDLE_MIN en /opt/graphagents/autostop.sh vía SSM (ya hecho). El próximo
  # apply querrá sincronizar user_data (stop/start in-place, NO replacement)
  # — hacerlo con la caja apagada.
  autostop_idle_minutes = 10
}
