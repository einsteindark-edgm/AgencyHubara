# Outputs consumidos por: los workflows de CI (frontend-deploy necesita el bucket
# + el distribution id), el build del frontend (Cognito ids → VITE_*), y el root
# ../compute (instance profiles leen los paths SSM).

output "frontend" {
  description = "Por tenant: bucket S3 + CloudFront (id para invalidar, domain para apuntar DNS)."
  value = {
    for t, m in module.frontend : t => {
      bucket_name                = m.bucket_name
      cloudfront_distribution_id = m.distribution_id
      cloudfront_domain_name     = m.distribution_domain_name
    }
  }
}

output "auth" {
  description = "Por tenant: ids de Cognito para el build del frontend (VITE_COGNITO_*) y la validación de JWT en FastAPI."
  value = {
    for t, m in module.auth : t => {
      user_pool_id     = m.user_pool_id
      app_client_id    = m.app_client_id
      user_pool_domain = m.user_pool_domain
      jwks_uri         = m.jwks_uri
      issuer           = m.issuer
    }
  }
}

output "build_config" {
  description = "Fuente única para frontend-deploy.yml: VITE_* del build + targets de deploy, por tenant."
  value = {
    for t in keys(var.tenants) : t => {
      api_url         = var.tenants[t].api_url
      region          = var.region
      user_pool_id    = module.auth[t].user_pool_id
      app_client_id   = module.auth[t].app_client_id
      cognito_domain  = module.auth[t].user_pool_domain
      bucket          = module.frontend[t].bucket_name
      distribution_id = module.frontend[t].distribution_id
    }
  }
}

output "ssm_prefixes" {
  description = "Por tenant: el prefijo SSM que el instance profile de ../compute debe poder leer."
  value       = { for t, m in module.secrets : t => m.ssm_prefix }
}

output "github_deploy_role_arn" {
  description = "ARN del rol angosto (frontend/backend deploy). Copiar a la GH variable AWS_DEPLOY_ROLE_ARN."
  value       = module.github_oidc.deploy_role_arn
}

output "github_terraform_role_arn" {
  description = "ARN del rol amplio (terraform plan/apply). Copiar a la GH variable AWS_TERRAFORM_ROLE_ARN."
  value       = module.github_oidc.terraform_role_arn
}
