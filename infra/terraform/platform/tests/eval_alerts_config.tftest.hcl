# Alertas del scorecard de ventas por tenant (modules/eval-alerts-config +
# validación de variables.tf), con el provider SIMULADO: no toca AWS ni
# robotocore. Ciclo Colossus F4 (2026-11-03).
#
#   terraform -chdir=infra/terraform/platform init -backend=false
#   terraform -chdir=infra/terraform/platform test
#
# En CI lo corre infra/robotocore/test-local.sh (workflow local-aws-test).

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

run "el_repo_del_ciclo_viaja_a_ssm" {
  command = plan
  module { source = "./modules/eval-alerts-config" }
  variables {
    tenant = "t1"
    config = { repo = "acme/ciclo" }
  }
  assert {
    condition     = aws_ssm_parameter.eval_alerts["SCORECARD_ALERTS_REPO"].value == "acme/ciclo"
    error_message = "SCORECARD_ALERTS_REPO lleva el repo del tenant."
  }
  assert {
    condition     = aws_ssm_parameter.eval_alerts["SCORECARD_ALERTS_REPO"].name == "/hubara/t1/SCORECARD_ALERTS_REPO" && aws_ssm_parameter.eval_alerts["SCORECARD_ALERTS_REPO"].type == "String"
    error_message = "SCORECARD_ALERTS_REPO va en /hubara/<tenant>/, tipo String (render-env-from-ssm.sh lo baja al .env)."
  }
}

run "un_tenant_nuevo_nace_sin_alertas" {
  command = plan
  variables {
    tenants = {
      t = {
        api_url       = "https://x.example"
        callback_urls = ["https://x.example/callback"]
        logout_urls   = ["https://x.example/"]
      }
    }
  }
  assert {
    condition     = module.eval_alerts_config["t"].params["SCORECARD_ALERTS_REPO"] == "PLACEHOLDER_set_out_of_band"
    error_message = "Sin bloque eval_alerts el repo es el placeholder: el código no abre alertas."
  }
}

run "un_repo_sin_forma_owner_repo_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t = {
        api_url       = "https://x.example"
        callback_urls = ["https://x.example/callback"]
        logout_urls   = ["https://x.example/"]
        eval_alerts   = { repo = "https://github.com/acme/ciclo" }
      }
    }
  }
  expect_failures = [var.tenants]
}
