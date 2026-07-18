# Tenants de {{repo_name}} — generado por forge. UN solo tenant: {{slug}}.
# api_url/callbacks: completar tras el primer apply de compute (EIP → sslip.io)
# o con el dominio propio. Ver NEXT_STEPS.md F7.
tenants = {
  {{slug}} = {
    api_url = "{{api_url}}"
    callback_urls = [
      "http://localhost:5173/auth/callback",
      "https://TODO-CLOUDFRONT.cloudfront.net/auth/callback",
    ]
    logout_urls = [
      "http://localhost:5173/",
      "https://TODO-CLOUDFRONT.cloudfront.net/",
    ]
    # explícito: el default de la variable puede quedar detrás del compose real
    enabled_plugins = "{{enabled_plugins}}"
  }
}
