# Monitoreo de memoria de la caja de app (incidente 2026-09-25).
#
# La caja (t3.medium, 4 GB, sin swap) se quedó sin RAM y se congeló ~25 min:
# ni SSH ni SSM respondían y el reboot suave no pudo apagarla. AWS solo mide
# CPU/red/disco por defecto — no había NINGÚN registro de qué proceso llenó la
# memoria (sar solo guarda el total; los logs de docker se borran con cada
# deploy; la caja de observabilidad está apagada).
#
# Esto instala el CloudWatch Agent por SSM State Manager (NO por user_data: la
# instancia ignora cambios de user_data y tiene prevent_destroy — nada de esto
# la toca) y manda cada minuto:
#   · memoria de la caja (disponible + % usado) y disco (/ y vault)
#   · RSS de cada proceso del stack (procstat por patrón de cmdline)
# Queda guardado en AWS aunque la caja se congele o reinicie.
#
# Costo: 16 métricas custom (10 gratis permanentes + 6 × USD 0,30) ≈ USD 1,80/mes;
# 1 alarma (gratis, primeras 10). Al agregar un worker nuevo: sumar su patrón a
# `local.cw_processes` (+USD 0,30/mes).
#
# Gated en robotocore (`use_local`): el emulador no implementa estos servicios.

variable "alarm_email" {
  description = "Mail que recibe la alarma de memoria. VACÍO = alarma sin suscriptor (visible solo en la consola). AWS manda un mail de confirmación que hay que aceptar."
  type        = string
  default     = ""
}

variable "memory_alarm_threshold_mb" {
  description = "Memoria disponible (MB) bajo la cual avisa. Con 4 GB, la caja se congeló a los ~24 MB; 300 MB deja margen para reaccionar."
  type        = number
  default     = 300
}

locals {
  cw_namespace = "Hubara/App"

  # Nombre del proceso en CloudWatch → regex sobre la cmdline completa (procstat
  # `pattern`). Anclados con $ donde un nombre es prefijo de otro (sales vs
  # sales_eval). La API corre uvicorn en un hijo `multiprocessing.spawn`.
  cw_processes = {
    litellm                = "bin/litellm "
    api                    = "multiprocessing\\.spawn import spawn_main"
    chats-sales            = "chats\\.workers\\.sales$"
    chats-sales_eval       = "chats\\.workers\\.sales_eval$"
    chats-remarketing      = "chats\\.workers\\.remarketing$"
    chats-post_sale_return = "chats\\.workers\\.post_sale_return$"
    eta                    = "eta\\.workers\\.eta$"
    marketing-campaigns    = "marketing\\.workers\\.campaigns$"
    reengagement-cycle     = "reengagement\\.workers\\.cycle$"
    order_sentinel-cycle   = "order_sentinel\\.workers\\.cycle$"
    orders-reconcile       = "orders\\.workers\\.reconcile$"
    catalog-sync           = "catalog\\.workers\\.sync$"
  }

  cw_agent_config = jsonencode({
    agent = {
      metrics_collection_interval = 60
      run_as_user                 = "root" # procstat lee /proc de procesos de contenedores (root)
    }
    metrics = {
      namespace         = local.cw_namespace
      append_dimensions = { InstanceId = "$${aws:InstanceId}" }
      metrics_collected = {
        mem = { measurement = ["mem_available", "mem_used_percent"] }
        disk = {
          measurement                 = ["used_percent"]
          resources                   = ["/", "/mnt/hubara-vault"]
          drop_device                 = true
          ignore_file_system_types    = ["sysfs", "tmpfs", "overlay"]
          metrics_collection_interval = 300
        }
        procstat = [
          for name, pattern in local.cw_processes : {
            pattern     = pattern
            measurement = ["memory_rss"]
          }
        ]
      }
    }
  })
}

# El agente necesita PutMetricData + leer su config de SSM (params
# `AmazonCloudWatch-*`, cubiertos por esta managed policy).
resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_ssm_parameter" "cw_agent_config" {
  count = var.use_local ? 0 : 1
  # El prefijo `AmazonCloudWatch-` es el que CloudWatchAgentServerPolicy deja leer.
  name  = "AmazonCloudWatch-agencyhubara-${var.tenant}-app"
  type  = "String"
  value = local.cw_agent_config
  tags  = { Tenant = var.tenant }
}

# Instalar + configurar en ORDEN (dos associations sueltas corren en paralelo y
# el configure fallaría si el paquete todavía no está). Idempotente: re-correrlo
# reinstala la misma versión y reaplica la config.
resource "aws_ssm_document" "cw_agent_setup" {
  count           = var.use_local ? 0 : 1
  name            = "agencyhubara-${var.tenant}-cloudwatch-agent-setup"
  document_type   = "Command"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Instala el CloudWatch Agent y le aplica la config de SSM."
    mainSteps = [
      {
        action = "aws:runDocument"
        name   = "install"
        inputs = {
          documentType       = "SSMDocument"
          documentPath       = "AWS-ConfigureAWSPackage"
          documentParameters = { action = "Install", name = "AmazonCloudWatchAgent" }
        }
      },
      {
        action = "aws:runDocument"
        name   = "configure"
        inputs = {
          documentType = "SSMDocument"
          documentPath = "AmazonCloudWatch-ManageAgent"
          documentParameters = {
            action                        = "configure"
            mode                          = "ec2"
            optionalConfigurationSource   = "ssm"
            optionalConfigurationLocation = aws_ssm_parameter.cw_agent_config[0].name
            optionalRestart               = "yes"
          }
        }
      },
    ]
  })
}

resource "aws_ssm_association" "cw_agent" {
  count = var.use_local ? 0 : 1
  # El hash de la config en el nombre fuerza una association nueva (que corre
  # al crearse) cada vez que cambia la config: sin esto, editar los procesos
  # actualizaría el parámetro pero el agente seguiría con la config vieja.
  association_name = "agencyhubara-${var.tenant}-cw-agent-${substr(sha1(local.cw_agent_config), 0, 8)}"
  name             = aws_ssm_document.cw_agent_setup[0].name

  targets {
    key    = "InstanceIds"
    values = [aws_instance.app.id]
  }

  depends_on = [aws_iam_role_policy_attachment.cloudwatch_agent]
}

# ── Alarma: poca memoria libre (o la caja dejó de reportar) ─────────────────
resource "aws_sns_topic" "alarms" {
  count = var.use_local ? 0 : 1
  name  = "agencyhubara-${var.tenant}-app-alarms"
  tags  = { Tenant = var.tenant }
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = !var.use_local && var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "memory_low" {
  count             = var.use_local ? 0 : 1
  alarm_name        = "agencyhubara-${var.tenant}-app-memory-low"
  alarm_description = "Caja de app ${var.tenant}: menos de ${var.memory_alarm_threshold_mb} MB de RAM disponible 3 min seguidos, o dejó de reportar (congelada). Runbook: memoria del proyecto prod_box_memory_exhaustion."
  namespace         = local.cw_namespace
  metric_name       = "mem_available"
  dimensions        = { InstanceId = aws_instance.app.id }

  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "LessThanThreshold"
  threshold           = var.memory_alarm_threshold_mb * 1024 * 1024
  # Congelada = el agente deja de mandar datos: eso también es la emergencia.
  treat_missing_data = "breaching"

  alarm_actions = [aws_sns_topic.alarms[0].arn]
  ok_actions    = [aws_sns_topic.alarms[0].arn]
  tags          = { Tenant = var.tenant }
}

output "alarms_topic_arn" { value = var.use_local ? null : aws_sns_topic.alarms[0].arn }
