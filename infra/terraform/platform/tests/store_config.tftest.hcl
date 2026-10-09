# Política comercial PÚBLICA de la tienda por tenant (modules/store-config):
# pago anticipado, envío, contra entrega, códigos del catálogo. Provider SIMULADO.
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

run "la_politica_comercial_va_a_ssm_con_los_nombres_del_codigo" {
  command = plan
  module { source = "./modules/store-config" }
  variables {
    tenant = "t1"
    config = {
      shipping_local_zone          = "Medellín y el área metropolitana"
      shipping_local_city          = "Medellín"
      shipping_rate_local_cop      = 9000
      shipping_rate_national_cop   = 18500
      cash_on_delivery_min_cop     = 60000
      payment_link_surcharge_local = "2%"
      payment_link_surcharge_other = "3,1%"
      sku_prefix                   = "ACM-"
      web_domain                   = "acme.example.com"
      catalog_collections          = ["vitrina", "nuevos"]
    }
  }
  assert {
    condition = (
      aws_ssm_parameter.store["SHIPPING_RATE_LOCAL_COP"].value == "9000"
      && aws_ssm_parameter.store["SHIPPING_RATE_NATIONAL_COP"].value == "18500"
      && aws_ssm_parameter.store["CASH_ON_DELIVERY_MIN_COP"].value == "60000"
      && aws_ssm_parameter.store["SHIPPING_LOCAL_ZONE"].value == "Medellín y el área metropolitana"
      && aws_ssm_parameter.store["SHIPPING_LOCAL_CITY"].value == "Medellín"
      && aws_ssm_parameter.store["PAYMENT_LINK_SURCHARGE_LOCAL"].value == "2%"
      && aws_ssm_parameter.store["PAYMENT_LINK_SURCHARGE_OTHER"].value == "3,1%"
      && aws_ssm_parameter.store["STORE_SKU_PREFIX"].value == "ACM-"
      && aws_ssm_parameter.store["STORE_WEB_DOMAIN"].value == "acme.example.com"
      && aws_ssm_parameter.store["CATALOG_COLLECTION_HANDLES"].value == "vitrina,nuevos"
    )
    error_message = "Cada campo de tenants.<t>.store es un String en SSM con el nombre que lee el código."
  }
  assert {
    condition     = length(aws_ssm_parameter.store) == 10
    error_message = "Sin payment_nequi_number no se crea ese parámetro; los demás sí."
  }
}

run "un_monto_que_no_es_entero_positivo_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { shipping_rate_local_cop = 0 }
      }
    }
  }
  expect_failures = [var.tenants]
}

run "un_recargo_sin_formato_porcentaje_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { payment_link_surcharge_local = "1.5" }
      }
    }
  }
  expect_failures = [var.tenants]
}

# `9.000` en HCL es el número 9: llegaría al bot como «$9» (premortem 2026-10-09)
run "un_monto_con_punto_de_miles_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { shipping_rate_local_cop = 9.000 }
      }
    }
  }
  expect_failures = [var.tenants]
}

# la ciudad se busca DENTRO de la del cliente: con «Bogotá D.C.», «Bogotá» cae en la nacional
run "una_ciudad_local_con_puntuacion_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { shipping_local_city = "Bogotá D.C." }
      }
    }
  }
  expect_failures = [var.tenants]
}

# render-env escribe KEY=valor: un `#` corta el valor y `${` tumba el .env entero
run "una_zona_con_caracteres_que_rompen_el_env_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { shipping_local_zone = "Zona #1 centro" }
      }
    }
  }
  expect_failures = [var.tenants]
}

# el código compara HANDLES de Medusa (minúsculas, sin espacios), no títulos
run "una_coleccion_que_no_es_handle_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { catalog_collections = ["Vitrina Principal"] }
      }
    }
  }
  expect_failures = [var.tenants]
}

# el dominio se compara en minúsculas
run "un_dominio_con_mayusculas_no_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store         = { web_domain = "CafeAurora.co" }
      }
    }
  }
  expect_failures = [var.tenants]
}

# …y la política de otra ciudad, con tildes y sus handles, sí pasa
run "la_politica_de_otra_ciudad_pasa_el_plan" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://t1.example.com"
        callback_urls = ["https://t1.example.com/callback"]
        logout_urls   = ["https://t1.example.com/"]
        store = {
          shipping_local_city        = "Medellín"
          shipping_local_zone        = "Medellín y el área metropolitana"
          shipping_rate_local_cop    = 9000
          shipping_rate_national_cop = 18500
          cash_on_delivery_min_cop   = 60000
          web_domain                 = "cafeaurora.co"
          catalog_collections        = ["vitrina", "home_new_arrivals"]
        }
      }
    }
  }
  assert {
    condition     = module.store_config["t1"].param_names != null
    error_message = "Una política válida de otra ciudad tiene que pasar las validaciones."
  }
}
