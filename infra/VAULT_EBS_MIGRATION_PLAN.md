# Plan: migrar el vault a un EBS dedicado (SEC-09-full)

> Estado: **PENDIENTE de ejecución.** Este doc es el plan — no toca código de infra.
> Contexto de seguridad completo: `SECURITY_AUDIT_fable.md` (SEC-09).
> Runbook operacional hermano: `infra/DEPLOY_RUNBOOK.md`.

## 1. Por qué

El vault (`hubara-vault`) es el único estado de negocio que no se puede
reconstruir de ninguna fuente externa: sesiones de WhatsApp, catálogo,
historial LLM por cliente. Hoy es un **volumen Docker nombrado sin
`driver_opts`**, que Docker crea en
`/var/lib/docker/volumes/hubara-vault/_data` — físicamente el **disco root**
de la instancia EC2 (`infra/compose/docker-compose.prod.yml:137-138`, el
propio comentario dice *"Candidato a EBS dedicado/backup"*).

Eso significa que el ciclo de vida de los datos está pegado al ciclo de vida
de la máquina. Es exactamente lo que causó el incidente 2026-07-08: un
`apply` reemplazó la caja → se fue el disco root → cero copias.

**Ya mitigado (SEC-09, PR #204, mergeado):**
- `prevent_destroy` en `aws_instance.app` — Terraform ya se niega a
  destruir/reemplazar la caja.
- `ignore_changes=[ami, user_data]` — el drift que disparó el incidente ya
  no aparece en el plan.
- DLM snapshot diario del disco root, retención 7 días.

**Lo que ninguna de esas mitigaciones tapa** (confirmado leyendo el código —
ver `infra/terraform/compute/modules/app-instance/main.tf`):
- `delete_on_termination` no está seteado → default AWS = `true`. Si la
  instancia se termina por **cualquier vía que no sea `terraform apply`**
  (consola, `aws ec2 terminate-instances`, credencial comprometida), el
  disco root se borra al instante — `prevent_destroy` no interviene porque
  nunca pasó por el plan de Terraform.
- `disable_api_termination` (Termination Protection nativo de AWS) tampoco
  está seteado.

Estos dos son un fix barato aparte (ver §7) que reduce la probabilidad del
disparo, pero **no elimina el acoplamiento de fondo**. La única forma de
cerrarlo del todo es que los datos vivan en un volumen cuyo ciclo de vida
sea independiente de la instancia — eso es este plan.

## 2. Costo

gp3 en `us-east-1`: `$0.08/GB-mes`. Un volumen de 20 GB (holgado para el
vault real) cuesta **~$1.60/mes** + snapshots incrementales del mismo tag
`Backup=daily` que ya usa el DLM existente (~$0.50-1/mes extra). Total
realista: **~$2-3/mes**. El costo no es el motivo del delay — es el riesgo
operacional de migrar datos en vivo.

## 3. Qué NO cambia

- El resto de la infra (Cognito, CloudFront, roles OIDC, rate limiting,
  etc.) — cero relación.
- La caja `hubara` sigue siendo `t3.medium`, mismo `root_volume_gb=30` (SO +
  Docker images + logs se quedan ahí; solo el vault se muda).
- Las cajas de `observability` y `graphagents` — no tienen este problema
  (su estado, si lo pierden, es reconstruible o no es de negocio del
  cliente).

## 4. Diseño

### 4.1 Terraform — nuevo volumen + attachment

En `infra/terraform/compute/modules/app-instance/main.tf`, agregar junto al
`aws_instance.app` existente:

```hcl
variable "vault_volume_gb" {
  type    = number
  default = 20
}

resource "aws_ebs_volume" "vault" {
  availability_zone = aws_instance.app.availability_zone
  size              = var.vault_volume_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name   = "agencyhubara-${var.tenant}-vault"
    Tenant = var.tenant
    # Mismo tag que ya usa el DLM existente (backup.tf) — el snapshot diario
    # lo recoge automáticamente, sin tocar backup.tf.
    Backup = "daily"
  }

  # Esto SÍ es el dato real ahora — más justificado que el guard del root.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "vault" {
  device_name = "/dev/xvdf"
  volume_id   = aws_ebs_volume.vault.id
  instance_id = aws_instance.app.id
}
```

Propagar `vault_volume_gb` desde `variables.tf` → `tenants` map (mismo
patrón que `root_volume_gb`) y desde `main.tf` (root de compute) al módulo.

**Gotcha AL2023 + Nitro (t3.medium ES Nitro):** el `device_name =
"/dev/xvdf"` que pedís en la API **no** es el nombre que ve el SO. En
instancias Nitro los EBS aparecen como NVMe — normalmente `/dev/nvme1n1`
(el root ya es `/dev/nvme0n1`). Confirmar con `lsblk` o `sudo nvme list` en
la caja después del `apply`, **no asumir el device_name de Terraform**.

**Impacto en el plan de Terraform:** solo 2 recursos `add` (volumen +
attachment). **Cero cambios a `aws_instance.app`** — no dispara
`prevent_destroy`, no hay riesgo de replacement.

### 4.2 Docker Compose — bind mount, no `driver_opts`

Dos formas de re-apuntar Docker al volumen nuevo. Elegir **bind mount
directo por servicio**, no `driver_opts` en el volumen nombrado — el motivo
es el rollback (ver §6).

En `infra/compose/docker-compose.prod.yml`, cambiar en los **11 servicios**
que hoy montan `hubara-vault:/app/hubara_vault` (api + 10 workers: ver
`grep -n "hubara-vault:/app/hubara_vault" infra/compose/docker-compose.prod.yml`):

```diff
     volumes:
-      - hubara-vault:/app/hubara_vault
+      - /mnt/hubara-vault:/app/hubara_vault
```

Y quitar `hubara-vault:` del bloque `volumes:` al final del archivo (ya no
es un volumen Docker-managed).

**Por qué bind mount y no `driver_opts`:** con `driver_opts` el volumen
`hubara-vault` sigue siendo un objeto Docker — un rollback requeriría
`docker volume rm` (que borra los datos del path viejo) antes de poder
recrearlo apuntando de nuevo al root. Con bind mount directo, el volumen
Docker-managed original (`/var/lib/docker/volumes/hubara-vault/_data`)
**nunca se toca** — ni se lee para escribir, ni se borra. Revertir el commit
del compose alcanza para volver exactamente al estado anterior.

## 5. Runbook de ejecución (dos pasadas — minimiza downtime real)

Ventana recomendada: **madrugada Bogotá** (mismo horario que ya usa el DLM,
02:00 Bogotá / 07:00 UTC — fuera de horario de conversaciones, ver
`backup.tf`). Confirmar que no hay un `backend-deploy` en curso ni agendado
para esa ventana (el workflow corre en cada push a `main` que toque
`hubara_agency/**`/`infra/compose/**` — evitar mergear algo más grande ese
día).

### Paso 0 — snapshot manual explícito (además del DLM automático)

```bash
aws ec2 create-snapshot --volume-id <root-volume-id> \
  --description "pre-vault-ebs-migration $(date -u +%F)" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Purpose,Value=pre-migration-manual}]'
```

No depender solo del cron de las 07:00 UTC — tomar uno fresco justo antes
de empezar.

### Paso 1 — Terraform apply (crea + attach, NO toca la instancia)

```bash
cd infra/terraform/compute
terraform plan   # confirmar: 2 to add (aws_ebs_volume.vault, aws_volume_attachment.vault), 0 to change, 0 to destroy
terraform apply
```

Si el plan muestra algo tocando `aws_instance.app` — **parar y revisar**,
no debería pasar (el volumen y el attachment son recursos independientes).

### Paso 2 — formatear + montar (por SSH, host)

```bash
# Encontrar el device real (NO asumir /dev/xvdf — ver gotcha §4.1)
lsblk
# Verificar que está VACÍO antes de formatear (debe decir "data", no un fs conocido)
sudo file -s /dev/nvme1n1

sudo mkfs.ext4 /dev/nvme1n1
sudo mkdir -p /mnt/hubara-vault
sudo mount /dev/nvme1n1 /mnt/hubara-vault
sudo chown ec2-user:ec2-user /mnt/hubara-vault

# Persistir en fstab por UUID (no por device name — puede correrse si se
# agregan más volúmenes) + nofail (que un boot no cuelgue si el volumen no
# está disponible)
UUID=$(sudo blkid -s UUID -o value /dev/nvme1n1)
echo "UUID=$UUID /mnt/hubara-vault ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount -a   # valida el fstab sin reiniciar
```

### Paso 3 — pasada 1: copia en caliente (app sigue corriendo, sin downtime)

```bash
sudo rsync -aHAX --info=progress2 \
  /var/lib/docker/volumes/hubara-vault/_data/ /mnt/hubara-vault/
```

Esto puede tardar según el tamaño real del vault — la app sigue sirviendo
tráfico normal todo este tiempo, no hay corte.

### Paso 4 — verificación previa al corte

```bash
diff <(cd /var/lib/docker/volumes/hubara-vault/_data && find . | sort) \
     <(cd /mnt/hubara-vault && find . | sort)
du -sh /var/lib/docker/volumes/hubara-vault/_data /mnt/hubara-vault  # deben coincidir
```

### Paso 5 — cutover (downtime real: minutos, solo esto)

```bash
cd /opt/hubara
docker compose stop api worker-catalog-sync worker-chats-sales \
  worker-chats-remarketing worker-reengagement-cycle \
  worker-order-sentinel-cycle worker-chats-sales_eval \
  worker-chats-post_sale_return worker-eta-eta \
  worker-orders-reconcile worker-marketing-campaigns

# Pasada 2: delta rsync (solo lo escrito entre el paso 3 y ahora)
sudo rsync -aHAX --delete /var/lib/docker/volumes/hubara-vault/_data/ /mnt/hubara-vault/
```

Mientras los workers están parados: mergear el PR con el diff del §4.2
(`docker-compose.prod.yml`) a `main` — dispara `backend-deploy.yml`, que
hace `scp` del compose nuevo + `docker compose up -d --remove-orphans`. Ese
mismo deploy hace el cutover (los containers arrancan ya con el bind mount
nuevo).

Si se prefiere no depender del CI para el timing exacto, aplicar el cambio
a mano primero (`docker compose up -d --remove-orphans` con el compose ya
editado localmente en la caja) y mergear el PR después para que quede como
fuente de verdad — pero AMBAS copias del compose (repo y caja) tienen que
terminar iguales antes de cerrar la ventana.

### Paso 6 — verificación funcional

```bash
docker compose ps                          # todo Up, sin restarts en loop
docker compose logs --tail 50 api           # sin errores de arranque
curl -sf http://localhost:8000/health       # o el endpoint de health real
```

Funcional (no solo que el container prenda):
- Mandar un mensaje de WhatsApp de prueba (o revisar que una conversación
  existente avance) → confirma que sales/remarketing leen y escriben al
  vault nuevo.
- Dashboard → catálogo visible → confirma que `worker-catalog-sync` lee
  bien del mount nuevo.
- `docker compose logs worker-order-sentinel-cycle worker-reengagement-cycle`
  sin excepciones en el próximo ciclo.

### Paso 7 — burn-in antes de considerar la copia vieja descartable

Dejar corriendo 24-48h bajo tráfico real. **No borrar**
`/var/lib/docker/volumes/hubara-vault/_data` en este paso — queda como red
de seguridad extra sin costo (es el mismo disco root que ya pagás). Limpieza
es un paso aparte, opcional, más adelante — no forma parte de este plan.

## 6. Rollback

Mientras no se haya borrado la copia original (nunca, en este plan), el
rollback es:

```bash
# revertir el commit del §4.2 (compose vuelve a `hubara-vault:/app/hubara_vault`)
git revert <sha-del-cambio-de-compose>
git push origin main   # dispara backend-deploy, redeploya
```

Como el volumen Docker `hubara-vault` **nunca se tocó** (no se le hizo
`docker volume rm`, nunca se escribió en él durante la migración — solo se
leyó vía rsync), Docker lo reconoce como ya existente al hacer
`docker compose up -d` y los containers vuelven a leer exactamente los
datos de antes del corte. Sin pérdida, sin pasos manuales de restauración.

Si el rollback ocurre **después** del burn-in y ya hubo escritura real en
el disco nuevo (`/mnt/hubara-vault`) que el volumen viejo no tiene, hay que
rsync-ear de vuelta esos deltas antes de revertir — por eso el burn-in del
§5 paso 7 es la señal para recién ahí considerar "no hay vuelta atrás
barata".

## 7. Fix aparte, barato, hacer antes o junto con esto

No depende de la migración — cierra el hueco de "termination fuera de
Terraform" descrito en §1 sin tocar datos:

```hcl
resource "aws_instance" "app" {
  # ...
  disable_api_termination = true

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type            = "gp3"
    encrypted              = true
    delete_on_termination  = false
  }
  # ...
}
```

`delete_on_termination = false` en el root deja de importar tanto una vez
migrado el vault (el root ya no tiene el dato irremplazable) — pero sigue
siendo gratis y correcto tenerlo. Se puede aplicar en un PR separado,
independiente de este plan, sin esperar a la ventana de migración.

## 8. Checklist de la sesión de ejecución

- [ ] PR de Terraform (§4.1) + PR de compose (§4.2) — pueden ir en el mismo PR.
- [ ] `terraform plan` confirma 2 add / 0 change / 0 destroy antes de aplicar.
- [ ] Snapshot manual (§ Paso 0) antes de tocar nada.
- [ ] Pasada 1 (copia en caliente) completa y verificada (§ Paso 3-4).
- [ ] Ventana de corte comunicada/agendada (madrugada Bogotá, sin otros deploys en curso).
- [ ] Cutover (§ Paso 5) + verificación funcional (§ Paso 6).
- [ ] Burn-in 24-48h antes de dar por cerrado SEC-09-full.
- [ ] Actualizar `SECURITY_AUDIT_fable.md` — mover SEC-09 de "guard" a "resuelto completo".
- [ ] (Opcional, fix aparte) §7 — `disable_api_termination` + `delete_on_termination=false`.
