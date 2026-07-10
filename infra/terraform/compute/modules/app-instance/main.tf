# Caja de app de un tenant: FastAPI + LiteLLM + 6 workers + Caddy (en la prod
# compose). Lee sus secretos de SSM vía el instance profile. Coincide con
# INFRASTRUCTURE.md §3.3-3.8.

variable "tenant" { type = string }
variable "region" { type = string }
variable "ami_id" { type = string }
variable "instance_type" { type = string }
variable "domain" { type = string }
variable "enabled_plugins" { type = string }
variable "root_volume_gb" { type = number }
variable "key_name" {
  type    = string
  default = null
}
variable "ssh_ingress_cidrs" { type = list(string) }
variable "image_repo" { type = string }
variable "use_local" {
  description = "true en robotocore: saltea el attach de la managed policy de AWS (el emulador no las siembra)."
  type        = bool
  default     = false
}

data "aws_vpc" "default" {
  default = true
}

# ── Security group ──────────────────────────────────────────────────────────
# 80/443 público (webhooks WhatsApp/Medusa + dashboard). 22 solo desde admin CIDR.
resource "aws_security_group" "app" {
  name        = "agencyhubara-${var.tenant}-app"
  description = "AgencyHubara app box - ${var.tenant}"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (Caddy ACME + redirect)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "HTTPS (webhooks + dashboard API)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "SSH (deploy + ops) - restringir a admin CIDR"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_ingress_cidrs
  }

  egress {
    description = "All outbound (Temporal Cloud, Meta, DeepSeek, Medusa, GHCR, SSM, OTLP)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "agencyhubara-${var.tenant}-app" }
}

# ── IAM: instance profile que lee /hubara/<tenant>/* de SSM ──────────────────
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "agencyhubara-${var.tenant}-app"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "ssm_read" {
  statement {
    sid     = "ReadTenantSecrets"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    # GetParametersByPath chequea el permiso contra el ARN del PATH (sin /*); el /*
    # cubre los parámetros. Hacen falta los DOS, sino el get-by-path da AccessDenied.
    resources = [
      "arn:aws:ssm:*:*:parameter/hubara/${var.tenant}",
      "arn:aws:ssm:*:*:parameter/hubara/${var.tenant}/*",
    ]
  }
  statement {
    sid       = "DecryptSecureString"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
  }
  # El plugin `ads` guarda el token OAuth de Meta EN RUNTIME como SecureString en
  # /hubara/<tenant>/meta/oauth (MetaTokenStorePort → SsmTokenStore). El instance
  # profile por default SOLO lee SSM; estas statements le dan WRITE scopeado a
  # `meta/*` (no a los demás secretos del tenant) + el Encrypt del SecureString.
  statement {
    sid       = "WriteMetaToken"
    actions   = ["ssm:PutParameter", "ssm:DeleteParameter"]
    resources = ["arn:aws:ssm:*:*:parameter/hubara/${var.tenant}/meta/*"]
  }
  statement {
    sid       = "EncryptMetaToken"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ssm_read" {
  name   = "ssm-read"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.ssm_read.json
}

# Despertar+despachar a la caja graphagents (pay-per-use, autostop). El bouncer
# del buzón (`src.plugins.graphagents.runs.boto3_launcher`) corriendo en ESTA caja
# arranca la box dormida y le manda el run vía SSM. Espeja el `self_stop` de
# graphagents-instance (scoped al tag Role=graphagents) pero para Start, no Stop.
data "aws_iam_policy_document" "wake_graphagents" {
  statement {
    sid       = "StartGraphAgentsBox"
    actions   = ["ec2:StartInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Role"
      values   = ["graphagents"]
    }
  }
  statement {
    # ec2:DescribeInstances NO soporta condiciones de tag a nivel recurso (AWS lo
    # rechaza); se deja resources=["*"] sin condición — es read-only (resolver la
    # IP/estado de la box por tag en tiempo real).
    sid       = "DescribeForTagResolve"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  statement {
    # SSM SendCommand/GetCommandInvocation: despachar el run a la box y leer el
    # resultado de la invocación. DescribeInstanceInformation: esperar a que el
    # AGENTE SSM de la box esté Online tras el arranque frío del autostop —
    # EC2 `running` no alcanza y el send_command prematuro muere con
    # InvalidInstanceId (PR #141). Es read-only y NO soporta resource-scope.
    sid       = "DispatchViaSSM"
    actions   = ["ssm:SendCommand", "ssm:GetCommandInvocation", "ssm:DescribeInstanceInformation"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "wake_graphagents" {
  name   = "wake-graphagents"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.wake_graphagents.json
}

# Session Manager (deploy/ops sin abrir SSH si se prefiere). robotocore no siembra
# las managed policies de AWS → se saltea en local (es real-only, como el cloud-init).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "app" {
  name = "agencyhubara-${var.tenant}-app"
  role = aws_iam_role.app.name
}

# ── Instancia + EIP ─────────────────────────────────────────────────────────
resource "aws_instance" "app" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    region          = var.region
    tenant          = var.tenant
    domain          = var.domain
    enabled_plugins = var.enabled_plugins
    image_repo      = var.image_repo
  })

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 obligatorio
  }

  # Incidente 2026-07-08: un AMI nuevo en el data source `most_recent` hizo que
  # un apply rutinario REEMPLAZARA la caja — y el vault (root EBS) se destruyó
  # con ella. Cinturón además del pin en tfvars: aunque el AMI resuelto cambie,
  # Terraform NO propone replacement. Upgrade de AMI = `-replace` explícito.
  lifecycle {
    ignore_changes = [ami]
  }

  # Volume tags: el DLM (backup.tf del root) snapshotea diario por este tag —
  # acá vive el vault (sesiones WhatsApp + catálogo), el único estado de
  # negocio que no se puede reconstruir de una fuente externa.
  volume_tags = {
    Name   = "agencyhubara-${var.tenant}-app-root"
    Tenant = var.tenant
    Backup = "daily"
  }

  tags = {
    Name   = "agencyhubara-${var.tenant}-app"
    Tenant = var.tenant
    Role   = "app"
  }
}

resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"
  tags     = { Name = "agencyhubara-${var.tenant}-app" }
}

output "security_group_id" { value = aws_security_group.app.id }
output "instance_id" { value = aws_instance.app.id }
output "public_ip" { value = aws_eip.app.public_ip }
output "domain" { value = var.domain }
