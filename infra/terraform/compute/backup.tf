# Backups del estado de negocio (incidente 2026-07-08: un apply con AMI drift
# reemplazó las cajas y destruyó el vault — sesiones WhatsApp + catálogo — sin
# que existiera NINGUNA copia). DLM snapshotea DIARIO todo volumen con tag
# `Backup=daily` (hoy: el root EBS de cada caja de app, ver volume_tags del
# módulo app-instance) y retiene 7 días.
#
# Restore tras un replacement: crear volumen desde el último snapshot →
# attach a la caja nueva → copiar /var/lib/docker/volumes/hubara-prod_hubara-vault.
#
# Gated en robotocore (`use_local`): el emulador no implementa DLM.

data "aws_iam_policy_document" "dlm_assume" {
  count = local.use_local ? 0 : 1

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  count              = local.use_local ? 0 : 1
  name               = "agencyhubara-dlm-backup"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume[0].json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  count      = local.use_local ? 0 : 1
  role       = aws_iam_role.dlm[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "daily_backup" {
  count              = local.use_local ? 0 : 1
  description        = "AgencyHubara - snapshot diario de volumenes con tag Backup daily - vault"
  execution_role_arn = aws_iam_role.dlm[0].arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Backup = "daily" }

    schedule {
      name = "daily-7d"

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        # 07:00 UTC = 02:00 Bogotá — fuera del horario de conversaciones.
        times = ["07:00"]
      }

      retain_rule {
        count = 7
      }

      tags_to_add = { SnapshotCreator = "dlm-agencyhubara" }
      copy_tags   = true
    }
  }
}
