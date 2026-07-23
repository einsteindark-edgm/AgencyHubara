# Caja SigNoz compartida (off-critical-path, doc §3.10). NUNCA expone ClickHouse
# (:9000/:8123) ni Zookeeper. El collector (:4317/:4318) solo acepta tráfico de
# las SGs de las cajas de app. La UI (:8080) NO es pública (admin CIDR / túnel).

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
variable "use_local" {
  description = "true en robotocore: saltea el attach de la managed policy de AWS."
  type        = bool
  default     = false
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "signoz" {
  name        = "agencyhubara-observability"
  description = "AgencyHubara SigNoz - solo OTLP desde apps; UI no publica"
  vpc_id      = data.aws_vpc.default.id

  # OTLP gRPC/HTTP SOLO desde las cajas de app (source security group, no CIDR).
  ingress {
    description     = "OTLP gRPC desde apps"
    from_port       = 4317
    to_port         = 4317
    protocol        = "tcp"
    security_groups = var.app_security_group_ids
  }
  ingress {
    description     = "OTLP HTTP desde apps"
    from_port       = 4318
    to_port         = 4318
    protocol        = "tcp"
    security_groups = var.app_security_group_ids
  }
  # UI de SigNoz: NO pública. Default cerrado (admin_ui_ingress_cidrs=[]) → NO se
  # crea la regla (solo túnel SSM/SSH). SEC-04: no cuelga más de ssh_ingress_cidrs
  # (amplio por CI). El dynamic evita una regla con cidr vacío (inválida en AWS).
  dynamic "ingress" {
    for_each = length(var.admin_ui_ingress_cidrs) > 0 ? [1] : []
    content {
      description = "SigNoz UI (restringir / tunelizar)"
      from_port   = 8080
      to_port     = 8080
      protocol    = "tcp"
      cidr_blocks = var.admin_ui_ingress_cidrs
    }
  }
  ingress {
    description = "SSH (deploy SigNoz + ops)"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_ingress_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "agencyhubara-observability" }
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

resource "aws_iam_role" "signoz" {
  name               = "agencyhubara-observability"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.signoz.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "signoz" {
  name = "agencyhubara-observability"
  role = aws_iam_role.signoz.name
}

resource "aws_instance" "signoz" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.signoz.id]
  iam_instance_profile   = aws_iam_instance_profile.signoz.name

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    region = var.region
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
  lifecycle {
    ignore_changes = [ami]
  }

  tags = {
    Name = "agencyhubara-observability"
    Role = "observability"
  }
}

resource "aws_eip" "signoz" {
  instance = aws_instance.signoz.id
  domain   = "vpc"
  tags     = { Name = "agencyhubara-observability" }
}

output "security_group_id" { value = aws_security_group.signoz.id }
output "instance_id" { value = aws_instance.signoz.id }
output "public_ip" { value = aws_eip.signoz.public_ip }
output "private_ip" { value = aws_instance.signoz.private_ip }
