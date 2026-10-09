# Datos comerciales PÚBLICOS de la tienda por tenant (modules/store-config):
# hoy la llave Nequi/Bre-B del pago anticipado. Provider SIMULADO.
#
#   terraform -chdir=infra/terraform/platform init -backend=false
#   terraform -chdir=infra/terraform/platform test

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

run "la_llave_de_la_tienda_va_a_ssm_como_string" {
  command = plan
  module { source = "./modules/store-config" }
  variables {
    tenant = "t1"
    config = { payment_nequi_number = "3001234567" }
  }
  assert {
    condition     = aws_ssm_parameter.store["PAYMENT_NEQUI_NUMBER"].value == "3001234567" && aws_ssm_parameter.store["PAYMENT_NEQUI_NUMBER"].type == "String"
    error_message = "PAYMENT_NEQUI_NUMBER es dato público: String con el valor del tfvars."
  }
  assert {
    condition     = aws_ssm_parameter.store["PAYMENT_NEQUI_NUMBER"].name == "/hubara/t1/PAYMENT_NEQUI_NUMBER"
    error_message = "Va en /hubara/<tenant>/ (render-env-from-ssm.sh lo baja al .env)."
  }
}

run "sin_llave_no_se_crea_el_parametro" {
  command = plan
  module { source = "./modules/store-config" }
  variables {
    tenant = "t1"
    config = {}
  }
  assert {
    condition     = length(aws_ssm_parameter.store) == 0
    error_message = "Sin llave en el tfvars no hay parámetro: manda el default del código (en un clon, vacío = sin pago anticipado)."
  }
}

run "una_llave_que_no_son_digitos_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { payment_nequi_number = "+57 300 123" }
      }
    }
  }
  expect_failures = [var.tenants]
}
