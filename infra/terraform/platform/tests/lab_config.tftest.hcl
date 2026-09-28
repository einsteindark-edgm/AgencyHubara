# Config del laboratorio por tenant (modules/lab-config + validaciones de
# variables.tf), con el provider SIMULADO: no toca AWS ni robotocore.
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

run "los_numeros_del_equipo_van_separados_por_coma" {
  command = plan
  module { source = "./modules/lab-config" }
  variables {
    tenant = "t1"
    config = {
      perception_mode_ceiling = "off"
      perception_profile      = "jev-v1"
      signal_inbound_meta     = false
      max_usd_per_run         = 120
      max_usd_per_month       = 300
      internal_numbers        = ["+573001234567", "+573009876543"]
    }
  }
  assert {
    condition     = aws_ssm_parameter.lab["LAB_INTERNAL_NUMBERS"].value == "+573001234567,+573009876543"
    error_message = "LAB_INTERNAL_NUMBERS debe ser la lista separada por comas (así la lee el exportador)."
  }
  assert {
    condition     = aws_ssm_parameter.lab["LAB_INTERNAL_NUMBERS"].name == "/hubara/t1/LAB_INTERNAL_NUMBERS" && aws_ssm_parameter.lab["LAB_INTERNAL_NUMBERS"].type == "String"
    error_message = "LAB_INTERNAL_NUMBERS va en /hubara/<tenant>/, tipo String (render-env-from-ssm.sh lo baja al .env)."
  }
}

run "sin_numeros_el_parametro_lleva_el_placeholder" {
  command = plan
  module { source = "./modules/lab-config" }
  variables {
    tenant = "t1"
    config = {
      perception_mode_ceiling = "off"
      perception_profile      = "jev-v1"
      signal_inbound_meta     = false
      max_usd_per_run         = 120
      max_usd_per_month       = 300
      internal_numbers        = []
    }
  }
  assert {
    condition     = aws_ssm_parameter.lab["LAB_INTERNAL_NUMBERS"].value == "PLACEHOLDER_set_out_of_band"
    error_message = "Lista vacía = placeholder: SSM no admite un valor vacío."
  }
}

run "un_numero_sin_formato_e164_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t = {
        api_url       = "https://x.example"
        callback_urls = ["https://x.example/callback"]
        logout_urls   = ["https://x.example/"]
        lab           = { internal_numbers = ["3001234567"] }
      }
    }
  }
  expect_failures = [var.tenants]
}

run "agregar_numeros_no_pisa_los_defaults_del_laboratorio" {
  command = plan
  variables {
    tenants = {
      t = {
        api_url       = "https://x.example"
        callback_urls = ["https://x.example/callback"]
        logout_urls   = ["https://x.example/"]
        lab           = { internal_numbers = ["+573001234567"] }
      }
    }
  }
  assert {
    condition     = var.tenants["t"].lab.max_usd_per_run == 120 && var.tenants["t"].lab.perception_mode_ceiling == "off"
    error_message = "Un bloque lab con solo internal_numbers conserva los demás defaults."
  }
}

# Motor de decisiones (MOTOR_DECISIONES_PLAN.md, F2 y F7): el techo de las
# capacidades (reglas → sombra → Jev) y el de la versión del workflow de
# ventas (V2). El control del dashboard nunca los supera; nacen apagados.
run "los_techos_del_motor_de_decisiones_nacen_apagados" {
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
    condition     = module.lab_config["t"].params["SALES_CAPABILITIES_CEILING"] == "off" && module.lab_config["t"].params["SALES_WORKFLOW_V2_CEILING"] == "off"
    error_message = "Sin bloque lab, los dos techos del motor de decisiones quedan en off."
  }
}

run "los_techos_del_motor_viajan_a_ssm" {
  command = plan
  module { source = "./modules/lab-config" }
  variables {
    tenant = "t1"
    config = {
      perception_mode_ceiling = "off"
      perception_profile      = "jev-v1"
      signal_inbound_meta     = false
      max_usd_per_run         = 120
      max_usd_per_month       = 300
      internal_numbers        = []
      capabilities_ceiling    = "shadow"
      workflow_v2_ceiling     = "canary"
    }
  }
  assert {
    condition     = aws_ssm_parameter.lab["SALES_CAPABILITIES_CEILING"].value == "shadow" && aws_ssm_parameter.lab["SALES_CAPABILITIES_CEILING"].name == "/hubara/t1/SALES_CAPABILITIES_CEILING"
    error_message = "SALES_CAPABILITIES_CEILING va en /hubara/<tenant>/ con el valor del tenant."
  }
  assert {
    condition     = aws_ssm_parameter.lab["SALES_WORKFLOW_V2_CEILING"].value == "canary"
    error_message = "SALES_WORKFLOW_V2_CEILING lleva el valor del tenant."
  }
}

run "un_techo_del_motor_raro_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t = {
        api_url       = "https://x.example"
        callback_urls = ["https://x.example/callback"]
        logout_urls   = ["https://x.example/"]
        lab           = { workflow_v2_ceiling = "shadow" }
      }
    }
  }
  expect_failures = [var.tenants]
}
