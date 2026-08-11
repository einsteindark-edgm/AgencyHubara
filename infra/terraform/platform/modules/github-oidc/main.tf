# OIDC de GitHub Actions → AWS, SIN llaves estáticas (nada de aws_access_key_id en
# secrets). Los workflows asumen estos roles vía `aws-actions/configure-aws-credentials`.
# Coincide con INFRASTRUCTURE.md §7 (Terraform) + el patrón de deploy de los workflows.
#
# Dos roles, principio de menor privilegio:
#   • terraform_role — gestiona TODA la infra. Trust: solo main + environment=production.
#                      Lo usa terraform-apply (gateado por el environment de GitHub).
#   • deploy_role    — angosto: subir el frontend a S3 + invalidar CloudFront + resolver
#                      el host EC2. Lo usan frontend-deploy / backend-deploy.
#
# Las PRs NO asumen ningún rol: se validan contra robotocore (local-aws-test.yml),
# así que un fork no puede tocar AWS real. Ese es el punto de robotocore.

variable "github_repo" { type = string } # owner/repo
variable "github_branches" { type = list(string) }
variable "tenants" { type = list(string) }
variable "region" { type = string }

# El OIDC provider de GitHub es ÚNICO POR CUENTA AWS. El primer proyecto de la
# cuenta lo crea (true, el default — este repo madre). Un proyecto clonado por
# forge en la MISMA cuenta lo referencia como data source (false, vía
# project.auto.tfvars) — crearlo de nuevo falla con EntityAlreadyExists.
variable "create_provider" {
  type    = bool
  default = true
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fce",
  ]
}

# Agregar `count` cambió el address del recurso en el state del repo madre
# (github → github[0]): sin este moved, el plan querría DESTRUIR y recrear el
# provider vivo (misma clase de incidente que el vault 2026-07-08).
moved {
  from = aws_iam_openid_connect_provider.github
  to   = aws_iam_openid_connect_provider.github[0]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  oidc_provider_arn = var.create_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

locals {
  # SEC-03: el rol WRITE (iam:*, ec2:*, ...) SOLO se asume desde el environment
  # `production` (con required reviewers). Ya NO desde un push a una branch — un
  # commit a main o una action comprometida no puede escalar a admin. El plan y
  # los deploys (que solo hacen `terraform output`) usan el rol READ-ONLY.
  tf_subs     = ["repo:${var.github_repo}:environment:production"]
  deploy_subs = [for b in var.github_branches : "repo:${var.github_repo}:ref:refs/heads/${b}"]
}

# ── Rol de Terraform (amplio, gateado) ──────────────────────────────────────
data "aws_iam_policy_document" "tf_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.tf_subs
    }
  }
}

resource "aws_iam_role" "terraform" {
  name               = "agencyhubara-gha-terraform"
  assume_role_policy = data.aws_iam_policy_document.tf_trust.json
}

# Amplio pero acotado a los servicios que la infra usa. Es PODEROSO (gestiona IAM):
# por eso solo lo asume el environment `production` con required reviewers.
data "aws_iam_policy_document" "tf_perms" {
  statement {
    sid = "ManageInfra"
    actions = [
      "s3:*", "cloudfront:*", "cognito-idp:*", "ssm:*",
      "iam:*", "ec2:*", "acm:*", "dynamodb:*", "logs:*",
      # dlm: la política de backup del vault (compute/backup.tf) — sin esto el
      # apply de compute falla al leer/gestionar el DLM Lifecycle Policy.
      "dlm:*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "terraform" {
  name   = "manage-infra"
  role   = aws_iam_role.terraform.id
  policy = data.aws_iam_policy_document.tf_perms.json
}

# ── Rol READ-ONLY (SEC-03) ──────────────────────────────────────────────────
# Lo asumen terraform-plan + los deploys (backend/frontend/observability, que
# solo hacen `terraform output`). Trust: branch refs (push a main) — pero SIN
# capacidad de escribir/escalar. Reemplaza el uso del rol amplio en esos flujos.
data "aws_iam_policy_document" "tf_readonly_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.deploy_subs
    }
  }
}

resource "aws_iam_role" "terraform_readonly" {
  name               = "agencyhubara-gha-terraform-readonly"
  assume_role_policy = data.aws_iam_policy_document.tf_readonly_trust.json
}

# Read comprensivo (refresca cualquier recurso en el plan) vía la policy managed
# de AWS — cubre Describe/Get/List de todos los servicios que la infra toca.
resource "aws_iam_role_policy_attachment" "readonly_managed" {
  role       = aws_iam_role.terraform_readonly.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# Lo que ReadOnlyAccess NO da y terraform necesita: el lock de DynamoDB
# (init/plan acquieren lock) + kms:Decrypt (refrescar los SSM SecureString).
data "aws_iam_policy_document" "tf_readonly_extra" {
  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = ["*"]
  }
  statement {
    sid       = "DecryptSecureStrings"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "terraform_readonly" {
  name   = "readonly-extra"
  role   = aws_iam_role.terraform_readonly.id
  policy = data.aws_iam_policy_document.tf_readonly_extra.json
}

# ── Rol de deploy (angosto) ─────────────────────────────────────────────────
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.deploy_subs
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "agencyhubara-gha-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust.json
}

data "aws_iam_policy_document" "deploy_perms" {
  # Frontend: subir el build a los buckets de cada tenant.
  statement {
    sid     = "FrontendSync"
    actions = ["s3:PutObject", "s3:DeleteObject", "s3:GetObject", "s3:ListBucket"]
    resources = concat(
      [for t in var.tenants : "arn:aws:s3:::agencyhubara-${t}-frontend"],
      [for t in var.tenants : "arn:aws:s3:::agencyhubara-${t}-frontend/*"],
    )
  }
  # Frontend: invalidar el cache de CloudFront (no se puede acotar por recurso).
  statement {
    sid       = "CloudFrontInvalidate"
    actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
    resources = ["*"]
  }
  # Backend: resolver la IP del host EC2 por tag (read-only).
  statement {
    sid       = "ResolveHost"
    actions   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus"]
    resources = ["*"]
  }
  # GraphAgents pay-per-use: prender/apagar la caja ocasional (scoped por tag).
  statement {
    sid       = "GraphAgentsPower"
    actions   = ["ec2:StartInstances", "ec2:StopInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Role"
      values   = ["graphagents"]
    }
  }
  # Read de config no-secreta si el deploy la necesita (los SECRETOS los lee el box).
  statement {
    sid       = "SsmRead"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:*:*:parameter/hubara/*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_perms.json
}

output "deploy_role_arn" { value = aws_iam_role.deploy.arn }
output "terraform_role_arn" { value = aws_iam_role.terraform.arn }
output "terraform_readonly_role_arn" { value = aws_iam_role.terraform_readonly.arn }
output "oidc_provider_arn" { value = local.oidc_provider_arn }
