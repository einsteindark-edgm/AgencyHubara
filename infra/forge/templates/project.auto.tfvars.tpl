# Proyecto {{repo_name}} — generado por forge.
# El OIDC provider de GitHub es 1 POR CUENTA AWS y ya lo creó el proyecto madre:
# este clon lo referencia como data source (create = false) para no chocar con
# EntityAlreadyExists. Los roles gha-* sí son propios ({{prefix}}-gha-*).
create_github_oidc_provider = false
github_repo                 = "{{repo}}"
