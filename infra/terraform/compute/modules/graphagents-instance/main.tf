# Caja GraphAgents (subsistema separado, off-critical-path — analiza Meta Ads).
# Corre los 3 contenedores: postgres (estado durable) + agentspan (runtime durable
# JVM, :6767) + la app graphagents (explorer :8900). Self-host (AgentSpan es OSS/MIT;
# no hay SaaS managed transparente → mismo criterio que SigNoz). 1 caja compartida.
#
# Puertos:
#   :6767 (agentspan) — abierto a las SGs de las cajas de app, para el PUENTE fase-B
#                       (el cast del plugin `ads` del monorepo llama por execution-id).
#   :8900 (explorer)  — solo admin CIDR / túnel (interno, como la UI de SigNoz).
#   :5432 (postgres)  — NUNCA expuesto (solo en la red interna del compose).

variable "region" { type = string }
variable "ami_id" { type = string }
variable "instance_type" { type = string }
variable "root_volume_gb" { type = number }
variable "key_name" {
  type    = string
  default = null
}
variable "ssh_ingress_cidrs" { type = list(string) }
variable "admin_ui_ingress_cidrs" {
  type    = list(string)
  default = []
}
variable "app_security_group_ids" { type = list(string) }
variable "image_repo" { type = string }
variable "use_local" {
  type    = bool
  default = false
}

# ── Economía pay-per-use (uso ocasional) ────────────────────────────────────
variable "use_eip" {
  description = "false = IP pública DINÁMICA (sin EIP). Ahorra el cargo de EIP idle (~$3.6/mo) cuando la caja está apagada. El deploy/control resuelven la IP por tag."
  type        = bool
  default     = false
}
variable "autostop_idle_minutes" {
  description = "La caja se auto-apaga tras N min de CPU idle (no podés olvidarla prendida). 0 = desactivar. Le da al rol el permiso de apagarse a sí misma."
  type        = number
  default     = 20
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "graphagents" {
  name        = "agencyhubara-graphagents"
  description = "AgencyHubara GraphAgents - agentspan/explorer internos; postgres nunca expuesto"
  vpc_id      = data.aws_vpc.default.id

  # AgentSpan (:6767) desde las cajas de app → puente fase-B (no público).
  ingress {
    description     = "AgentSpan runtime desde apps (puente fase-B)"
    from_port       = 6767
    to_port         = 6767
    protocol        = "tcp"
    security_groups = var.app_security_group_ids
  }
  # Explorer visual (:8900): NO público. Default cerrado (admin_ui_ingress_cidrs=[])
  # → NO se crea la regla (solo túnel). SEC-04: ya no cuelga de ssh_ingress_cidrs.
  dynamic "ingress" {
    for_each = length(var.admin_ui_ingress_cidrs) > 0 ? [1] : []
    content {
      description = "Explorer visual (restringir / tunelizar)"
      from_port   = 8900
      to_port     = 8900
      protocol    = "tcp"
      cidr_blocks = var.admin_ui_ingress_cidrs
    }
  }
  # AgentSpan admin (:6767): NO público. Default cerrado. (El :6767 del puente
  # fase-B viene de las SGs de app por source_security_group, no de este CIDR.)
  dynamic "ingress" {
    for_each = length(var.admin_ui_ingress_cidrs) > 0 ? [1] : []
    content {
      description = "AgentSpan UI/API desde admin"
      from_port   = 6767
      to_port     = 6767
      protocol    = "tcp"
      cidr_blocks = var.admin_ui_ingress_cidrs
    }
  }
  ingress {
    description = "SSH (deploy + ops)"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_ingress_cidrs
  }

  egress {
    description = "All outbound (Meta API, LLM, GHCR, Docker Hub, SSM)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "agencyhubara-graphagents" }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "graphagents" {
  name               = "agencyhubara-graphagents"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "ssm_read" {
  statement {
    sid     = "ReadGraphAgentsSecrets"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    # GetParametersByPath chequea el permiso contra el ARN del PATH (sin /*).
    resources = [
      "arn:aws:ssm:*:*:parameter/graphagents",
      "arn:aws:ssm:*:*:parameter/graphagents/*",
    ]
  }
  statement {
    sid       = "DecryptSecureString"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ssm_read" {
  name   = "ssm-read"
  role   = aws_iam_role.graphagents.id
  policy = data.aws_iam_policy_document.ssm_read.json
}

# Auto-apagado: la caja puede apagarse a SÍ MISMA (scoped al tag Role=graphagents).
data "aws_iam_policy_document" "self_stop" {
  statement {
    sid       = "SelfStop"
    actions   = ["ec2:StopInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Role"
      values   = ["graphagents"]
    }
  }
}

resource "aws_iam_role_policy" "self_stop" {
  count  = var.autostop_idle_minutes > 0 ? 1 : 0
  name   = "self-stop"
  role   = aws_iam_role.graphagents.id
  policy = data.aws_iam_policy_document.self_stop.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.graphagents.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "graphagents" {
  name = "agencyhubara-graphagents"
  role = aws_iam_role.graphagents.name
}

resource "aws_instance" "graphagents" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.graphagents.id]
  iam_instance_profile   = aws_iam_instance_profile.graphagents.name

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    region           = var.region
    image_repo       = var.image_repo
    autostop_minutes = var.autostop_idle_minutes
  })

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required"
  }

  # Incidente 2026-07-08: AMI drift (data source most_recent) convertía un apply
  # rutinario en replacement de la caja. El pin en tfvars + este ignore hacen
  # el upgrade de AMI un acto deliberado (`terraform apply -replace=...`).
  #
  # `user_data`: mismo racional que app-instance — la caja viva se patchea por
  # SSM (PR #200: autostop/quiet hours), NO re-lanzándola; user_data solo corre
  # al launch y modificarlo exige la caja PARADA (IncorrectInstanceState). Sin
  # este ignore, el drift del template contaminaba CUALQUIER plan de compute
  # (visto 2026-08-12 durante la migración del vault). Una caja nueva igual
  # toma el template vigente.
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  tags = {
    Name = "agencyhubara-graphagents"
    Role = "graphagents"
  }
}

# EIP solo si use_eip (default off → IP dinámica, sin cargo idle cuando está apagada).
resource "aws_eip" "graphagents" {
  count    = var.use_eip ? 1 : 0
  instance = aws_instance.graphagents.id
  domain   = "vpc"
  tags     = { Name = "agencyhubara-graphagents" }
}

output "security_group_id" { value = aws_security_group.graphagents.id }
output "instance_id" { value = aws_instance.graphagents.id }
# IP dinámica cuando no hay EIP — válida solo mientras la caja está prendida.
# El deploy/control la resuelven por tag en tiempo real (no del state).
output "public_ip" { value = var.use_eip ? aws_eip.graphagents[0].public_ip : aws_instance.graphagents.public_ip }
output "private_ip" { value = aws_instance.graphagents.private_ip }
