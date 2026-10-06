# Caja del laboratorio de conversaciones (LABORATORIO_CONVERSACIONES_PLAN.md §3,
# PR 6): EC2 aparte bajo demanda, con autoapagado, más su S3 privado y los
# permisos de la app de producción para lanzar corridas.
#
# Por qué una caja aparte: la de producción (t3.medium, sin swap, ~420 MB libres)
# no aguanta un simulador sin arriesgar que el sistema mate un proceso del bot.
#
# Candados (plan §3.5):
#   1. Físico: el disco del vault solo está en la caja de producción.
#   2. IAM: el rol de la caja lee SOLO /hubara-lab/* (nunca /hubara/<tenant>/*) y
#      solo su bucket. OJO: AmazonSSMManagedInstanceCore (la necesita el agente
#      SSM) trae ssm:GetParameter/GetParameters sobre "*", y los permisos se
#      suman: el candado es el Deny explícito de abajo, no el Allow acotado.
#      La app de producción solo escribe bench/ y orders/ y lee runs/; su
#      política `launch-lab` solo prende la instancia Role=lab y le da órdenes
#      con AWS-RunShellScript (la política `wake_graphagents` de app-instance,
#      anterior al laboratorio, ya da ssm:SendCommand sobre "*": ver issue aparte).
#   3. Código: el worker sales_lab no arranca con llaves ni rutas de producción.
#   4. CI: test de fugas (PR 11) y tests/infra/test_lab_box_iam.py.
#
# Sin puertos de entrada (ni SSH): todo por SSM. Stateless: reemplazarla no
# pierde nada (los resultados viven en S3), por eso user_data SÍ reemplaza la
# caja al cambiar (a diferencia de app-instance, que protege el vault).

variable "region" { type = string }
variable "ami_id" { type = string }
variable "instance_type" { type = string }
variable "root_volume_gb" { type = number }
variable "autostop_idle_minutes" { type = number }
variable "image_repo" {
  description = "Imagen del backend en GHCR (sin tag): la misma que producción. El tag llega con cada orden."
  type        = string
}
variable "app_role_names" {
  description = "Roles de las cajas de app que pueden lanzar corridas (bench/ + prender + ordenar por SSM)."
  type        = list(string)
}
variable "tenants" {
  description = "Tenants cuyo /hubara/<tenant>/LAB_BUCKET se publica (lo lee el lanzador de corridas)."
  type        = list(string)
}
variable "max_run_hours" {
  description = "Tope de una corrida: el autoapagado detiene un runner que lleva más que esto, y un apagado de respaldo corta la caja una hora después."
  type        = number
}
variable "use_local" {
  type    = bool
  default = false
}

data "aws_caller_identity" "current" {}
data "aws_vpc" "default" {
  default = true
}

locals {
  bucket     = "agencyhubara-lab-${data.aws_caller_identity.current.account_id}"
  ghcr_owner = split("/", var.image_repo)[1]
  lab_dir    = "${path.module}/../../../../compose/lab"
  # Secretos del laboratorio: placeholder; el operador los carga fuera de banda.
  # La llave de OpenRouter es OTRA (con su propio límite de crédito), no la de prod.
  secret_keys = ["DEEPSEEK_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GHCR_PULL_TOKEN"]
}

# ── S3 privado: bench/ (el banco, 30 días), orders/ (la orden de cada corrida:
# bots, repeticiones y banco, 180 días) y runs/ (resultados, 180 días) ─────────
resource "aws_s3_bucket" "lab" {
  bucket = local.bucket
  tags   = { Name = local.bucket, Role = "lab" }
}

resource "aws_s3_bucket_public_access_block" "lab" {
  bucket                  = aws_s3_bucket.lab.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lab" {
  bucket = aws_s3_bucket.lab.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lab" {
  bucket = aws_s3_bucket.lab.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lab" {
  bucket = aws_s3_bucket.lab.id
  rule {
    id     = "bench-30d"
    status = "Enabled"
    filter {
      prefix = "bench/"
    }
    expiration {
      days = 30
    }
  }
  rule {
    id     = "orders-180d"
    status = "Enabled"
    filter {
      prefix = "orders/"
    }
    expiration {
      days = 180
    }
  }
  rule {
    id     = "runs-180d"
    status = "Enabled"
    filter {
      prefix = "runs/"
    }
    expiration {
      days = 180
    }
  }
  # Una subida cortada (caja apagada a mitad) no deja partes cobrando para siempre.
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }
}

# Solo TLS: nada entra ni sale del bucket en claro.
data "aws_iam_policy_document" "bucket_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [aws_s3_bucket.lab.arn, "${aws_s3_bucket.lab.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lab" {
  bucket     = aws_s3_bucket.lab.id
  policy     = data.aws_iam_policy_document.bucket_tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.lab]
}

# ── Secretos del laboratorio en /hubara-lab/* (fuera del árbol de los tenants) ─
resource "aws_ssm_parameter" "secret" {
  for_each = toset(local.secret_keys)

  name        = "/hubara-lab/${each.key}"
  type        = "SecureString"
  value       = "PLACEHOLDER_set_out_of_band"
  description = "Laboratorio de conversaciones — ${each.key} (valor fuera de banda; NUNCA la de producción)"

  lifecycle {
    ignore_changes = [value]
  }
}

# El lanzador de corridas (API de producción) encuentra el bucket acá.
resource "aws_ssm_parameter" "bucket_name" {
  for_each = toset(var.tenants)

  name        = "/hubara/${each.key}/LAB_BUCKET"
  type        = "String"
  value       = local.bucket
  description = "Laboratorio de conversaciones — bucket privado de bancos y corridas (fuente: Terraform compute)"
}

# ── Rol de la caja ──────────────────────────────────────────────────────────
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lab" {
  name               = "agencyhubara-lab"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "lab_box" {
  statement {
    sid     = "ReadLabSecretsOnly"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "arn:aws:ssm:*:*:parameter/hubara-lab",
      "arn:aws:ssm:*:*:parameter/hubara-lab/*",
    ]
  }
  # El candado real: AmazonSSMManagedInstanceCore permite GetParameter(s) sobre
  # "*" y un Deny explícito le gana. Sin esto, desde la caja se leerían
  # /hubara/<tenant>/WHATSAPP_ACCESS_TOKEN, TEMPORAL_API_KEY, MEDUSA_*, etc.
  statement {
    sid    = "DenyEveryParameterOutsideTheLab"
    effect = "Deny"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
      "ssm:GetParameterHistory",
    ]
    not_resources = [
      "arn:aws:ssm:*:*:parameter/hubara-lab",
      "arn:aws:ssm:*:*:parameter/hubara-lab/*",
    ]
  }
  statement {
    sid       = "DecryptLabSecureStringsViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:PARAMETER_ARN"
      values   = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/hubara-lab/*"]
    }
  }
  # Sin condición de prefijo: S3 contesta 404 (no 403) a una clave que no existe
  # solo si el rol puede listar el bucket, y la caja lee progress.json antes de
  # crearlo. El bucket es del laboratorio: listarlo no expone nada más.
  statement {
    sid       = "ListOwnBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lab.arn]
  }
  statement {
    sid       = "ReadBenchAndOrders"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lab.arn}/bench/*", "${aws_s3_bucket.lab.arn}/orders/*"]
  }
  statement {
    sid       = "WriteRuns"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.lab.arn}/runs/*"]
  }
  statement {
    sid       = "SelfStop"
    actions   = ["ec2:StopInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Role"
      values   = ["lab"]
    }
  }
}

resource "aws_iam_role_policy" "lab_box" {
  name   = "lab-box"
  role   = aws_iam_role.lab.id
  policy = data.aws_iam_policy_document.lab_box.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.lab.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "lab" {
  name = "agencyhubara-lab"
  role = aws_iam_role.lab.name
}

# ── Permisos de la app de producción para lanzar corridas ───────────────────
# Estrecho a propósito: NO se copia `wake_graphagents` (que da SendCommand sobre *).
# Solo va a los roles de los tenants del laboratorio (var.app_role_names).
data "aws_iam_policy_document" "app_launch_lab" {
  statement {
    sid       = "WriteBenchAndOrders"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lab.arn}/bench/*", "${aws_s3_bucket.lab.arn}/orders/*"]
  }
  statement {
    sid       = "ReadRuns"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lab.arn}/runs/*", "${aws_s3_bucket.lab.arn}/bench/*", "${aws_s3_bucket.lab.arn}/orders/*"]
  }
  # Sin condición de prefijo, por la misma razón que en la caja (404 y no 403).
  statement {
    sid       = "ListBenchAndRuns"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lab.arn]
  }
  statement {
    sid       = "StartLabBox"
    actions   = ["ec2:StartInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Role"
      values   = ["lab"]
    }
  }
  # SendCommand se autoriza contra la INSTANCIA y contra el DOCUMENTO: la
  # instancia debe tener Role=lab y el documento solo puede ser AWS-RunShellScript.
  statement {
    sid       = "SendCommandToLabBoxOnly"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:*:*:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Role"
      values   = ["lab"]
    }
  }
  statement {
    sid       = "SendCommandShellDocumentOnly"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:*::document/AWS-RunShellScript"]
  }
  # Sin resource-scope en AWS: lectura del estado de la orden, del agente SSM y
  # de la instancia por tag.
  statement {
    sid       = "ReadDispatchState"
    actions   = ["ssm:GetCommandInvocation", "ssm:DescribeInstanceInformation", "ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "app_launch_lab" {
  for_each = toset(var.app_role_names)

  name   = "launch-lab"
  role   = each.key
  policy = data.aws_iam_policy_document.app_launch_lab.json
}

# ── La caja ─────────────────────────────────────────────────────────────────
resource "aws_security_group" "lab" {
  name        = "agencyhubara-lab"
  description = "AgencyHubara laboratorio - sin entrada (solo SSM); salida a LLM, GHCR, S3 y SSM"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound (LLM, GHCR, Docker Hub, S3, SSM)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "agencyhubara-lab" }
}

resource "aws_instance" "lab" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.lab.id]
  iam_instance_profile   = aws_iam_instance_profile.lab.name

  # gzip: el cloud-init lleva el compose y los scripts (el límite de user_data
  # es 16 KB; cloud-init descomprime solo). La configuración del proxy de LLM
  # NO viaja acá: dispatch.sh la saca de la imagen de cada corrida (la misma
  # de producción), así un cambio de ids de modelo no deja la caja con los viejos
  # ni obliga a reemplazarla.
  user_data_base64 = base64gzip(templatefile("${path.module}/cloud-init.yaml.tftpl", {
    region           = var.region
    bucket           = local.bucket
    ghcr_owner       = local.ghcr_owner
    autostop_minutes = var.autostop_idle_minutes
    max_run_hours    = var.max_run_hours
    backstop_minutes = (var.max_run_hours + 1) * 60
    compose_b64      = filebase64("${local.lab_dir}/docker-compose.lab.yml")
    dispatch_b64     = filebase64("${local.lab_dir}/dispatch.sh")
    cancel_b64       = filebase64("${local.lab_dir}/cancel.sh")
    autostop_b64     = filebase64("${local.lab_dir}/autostop.sh")
  }))
  # Stateless: un cambio del cloud-init (compose, scripts) crea la caja de nuevo
  # en vez de pedirla apagada para editar user_data.
  user_data_replace_on_change = true

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required"
  }

  lifecycle {
    ignore_changes = [ami]
  }

  tags = {
    Name = "agencyhubara-lab"
    Role = "lab"
  }
}

output "instance_id" { value = aws_instance.lab.id }
output "bucket" { value = aws_s3_bucket.lab.bucket }
output "security_group_id" { value = aws_security_group.lab.id }
