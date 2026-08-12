# Plan: migrar el vault a un EBS dedicado (SEC-09-full)

> Estado: **PENDIENTE de ejecución.** Este doc es el plan — no toca código de infra.
> Contexto de seguridad completo: `SECURITY_AUDIT_fable.md` (SEC-09).
> Runbook operacional hermano: `infra/DEPLOY_RUNBOOK.md`.
> Revisado 2026-08-12 contra el código vivo (compose, módulo app-instance,
> backup.tf, backend-deploy.yml): paths, conteos y tags verificados.

## 1. Por qué

El vault (`hubara-vault`) es el único estado de negocio que no se puede
reconstruir de ninguna fuente externa: sesiones de WhatsApp, catálogo,
historial LLM por cliente. Hoy es un **volumen Docker nombrado sin
`driver_opts`**, que Docker crea en
`/var/lib/docker/volumes/hubara-prod_hubara-vault/_data` (el compose declara
`name: hubara-prod`; el prefijo es el nombre del proyecto) — físicamente el
**disco root**
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

gp3 en `us-east-1`: `$0.08/GB-mes`. Un volumen de **10 GB** cuesta
**~$0.80/mes** + snapshots incrementales del mismo tag `Backup=daily` que ya
usa el DLM existente (~$0.50/mes extra). Total realista: **~$1-1.5/mes**.

Empezar chico a propósito: un EBS **se agranda online en un comando**
(`aws ec2 modify-volume` + `resize2fs`, sin downtime) pero **no se puede
achicar nunca** — la asimetría dice arrancar en 10 GB, no en 20. Guard: si
el `du -sh` del volumen actual (Paso 3) muestra >5 GB, subir
`vault_volume_gb` antes del apply. El costo no es el motivo del delay — es
el riesgo operacional de migrar datos en vivo.

### 2.1 Qué ahorra y qué NO ahorra esta migración

- **NO libera plata del disco root.** Los 30 GB del root se pagan igual
  (~$2.40/mes) estén llenos o vacíos, y un EBS no se puede achicar — reducir
  el root exigiría un swap de root device (reconstruir la caja, contra
  `prevent_destroy`) para ahorrar <$2/mes. No vale el riesgo, y el root
  sigue necesitando espacio para SO + imágenes Docker + logs.
- **NO habilita bajar la instancia.** El tamaño de la caja (t3.medium) lo
  dictan RAM/CPU de los 13 containers (api + 10 workers + LiteLLM + Caddy),
  no dónde vive el vault. Bajar a t3.small (2 GB RAM) con esa flota es
  riesgo de OOM — si se quiere explorar, es un experimento aparte midiendo
  primero (`free -m`, `docker stats --no-stream`), sin relación con este
  plan.
- **SÍ ahorra (chico): dejar de snapshotear el root post-burn-in.** Una vez
  el vault esté fuera del root y el burn-in cerrado, el root ya no tiene
  dato irremplazable → quitarle el tag `Backup=daily`
  (`root_block_device.tags`, ver §4.1) y el DLM deja de snapshotearlo
  (~$0.50-1/mes menos, y el backup pasa a cubrir exactamente el dato que
  importa). Ver checklist §8.

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
  default = 10 # agrandar es online y trivial; achicar es imposible (§2)
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
patrón que `root_volume_gb`, `optional(number, 10)`) y desde `main.tf` (root
de compute) al módulo.

**Cambio OBLIGATORIO en el mismo PR — mover `volume_tags` a
`root_block_device.tags`.** `aws_instance.app` hoy taggea sus volúmenes vía
`volume_tags` (main.tf:225). El provider de AWS documenta explícitamente que
`volume_tags` NO se puede combinar con volúmenes atacheados que manejan sus
propios tags (`aws_ebs_volume` + `aws_volume_attachment`): `volume_tags`
aplica a TODOS los volúmenes atacheados, así que tras el attach cada plan
querría re-taggear el vault con los tags del root (`Name=...-app-root`,
pisando `Name` y `Backup`) y `aws_ebs_volume.vault` pelearía de vuelta en
cada apply — *resource cycling* perpetuo. El fix es mover los tags al bloque
del root, que es su único destinatario real:

```hcl
resource "aws_instance" "app" {
  # ...
  # volume_tags = { ... }   ← ELIMINAR (taggea TODOS los volúmenes
  #                            atacheados; pisaría los tags del vault)

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
    tags = {
      Name   = "agencyhubara-${var.tenant}-app-root"
      Tenant = var.tenant
      # Quitar post-burn-in (§2.1): migrado el vault, el root ya no tiene
      # dato irremplazable y el DLM deja de snapshotearlo.
      Backup = "daily"
    }
  }
  # ...
}
```

Es un cambio **in-place** (solo tags) — no dispara `prevent_destroy` ni
replacement.

**Gotcha AL2023 + Nitro (t3.medium ES Nitro):** el `device_name =
"/dev/xvdf"` que pedís en la API **no** es el nombre que ve el SO. En
instancias Nitro los EBS aparecen como NVMe — normalmente `/dev/nvme1n1`
(el root ya es `/dev/nvme0n1`). Confirmar con `lsblk` o `sudo nvme list` en
la caja después del `apply`, **no asumir el device_name de Terraform**.

**Impacto en el plan de Terraform:** 2 recursos `add` (volumen +
attachment) + **1 `change` in-place** sobre `aws_instance.app` (el move de
tags de arriba — solo tags, sin replacement). Si el plan muestra CUALQUIER
otra cosa (un `destroy`, un `replace`, un change que no sea de tags) —
**parar y revisar**.

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
Docker-managed original (`/var/lib/docker/volumes/hubara-prod_hubara-vault/_data`)
**nunca se toca** — ni se lee para escribir, ni se borra. Revertir el commit
del compose alcanza para volver exactamente al estado anterior.

### 4.3 Caja de reemplazo — cloud-init + etiqueta del filesystem

Hoy `cloud-init.yaml.tftpl` no sabe nada del vault. Si algún día la caja se
reemplaza (`-replace` explícito quitando el `prevent_destroy`), la instancia
nueva NO tendría `/mnt/hubara-vault` montado: Docker crearía el directorio
vacío en el root y los workers arrancarían con vault vacío **en silencio**
(el dato seguiría a salvo en el EBS, pero desmontado — la misma clase de bug
silencioso que ya nos mordió con el catálogo).

Cierre barato, en el mismo PR:

1. Formatear con etiqueta (`mkfs.ext4 -L hubara-vault`, ver Paso 2) y montar
   por `LABEL=` en fstab — la etiqueta es estable entre reboots,
   replacements y renombres de device NVMe.
2. Agregar al template de cloud-init el mount equivalente (mkdir + entrada
   fstab `LABEL=hubara-vault /mnt/hubara-vault ext4 defaults,nofail 0 2`).
   En la caja viva es inocuo — `ignore_changes = [user_data]` ya ignora el
   drift y user_data solo corre al launch; una caja nueva sí lo toma.
3. Nota en `infra/DEPLOY_RUNBOOK.md`: tras un replacement, verificar
   `findmnt /mnt/hubara-vault` ANTES de `docker compose up`.

**Riesgo residual conocido (aceptado):** `nofail` + docker en autostart
significa que si el mount falla en un boot, los containers escriben al
directorio vacío del root sin quejarse. El `findmnt` del runbook es la
guarda; blindarlo de verdad (unidad systemd con `RequiresMountsFor` sobre
docker.service) queda fuera de este plan.

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

# -L: etiqueta estable — fstab y cloud-init (§4.3) montan por LABEL, que
# sobrevive reboots, replacements y renombres de device NVMe
sudo mkfs.ext4 -L hubara-vault /dev/nvme1n1
sudo mkdir -p /mnt/hubara-vault
sudo mount LABEL=hubara-vault /mnt/hubara-vault
sudo chown ec2-user:ec2-user /mnt/hubara-vault

# Persistir en fstab por LABEL (no por device name — puede correrse si se
# agregan más volúmenes) + nofail (que un boot no cuelgue si el volumen no
# está disponible; el riesgo residual de nofail está en §4.3)
echo "LABEL=hubara-vault /mnt/hubara-vault ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount -a   # valida el fstab sin reiniciar
```

### Paso 3 — pasada 1: copia en caliente (app sigue corriendo, sin downtime)

```bash
# El volumen se llama hubara-prod_hubara-vault (prefijo = `name: hubara-prod`
# del compose) — derivar el path real en vez de hardcodearlo:
SRC=$(docker volume inspect hubara-prod_hubara-vault --format '{{.Mountpoint}}')
echo "$SRC"   # esperado: /var/lib/docker/volumes/hubara-prod_hubara-vault/_data

sudo rsync -aHAX --info=progress2 "$SRC/" /mnt/hubara-vault/
```

Esto puede tardar según el tamaño real del vault — la app sigue sirviendo
tráfico normal todo este tiempo, no hay corte.

### Paso 4 — verificación previa al corte

```bash
# Única diferencia esperada: `./lost+found` (lo crea mkfs en el destino; el
# rsync --delete de la pasada 2 lo borra — inofensivo)
diff <(cd "$SRC" && find . | sort) \
     <(cd /mnt/hubara-vault && find . | sort)
# Aproximados (metadata/bloques de ext4 difieren un poco): mismo orden de
# magnitud alcanza — el check real es el diff de arriba
du -sh "$SRC" /mnt/hubara-vault
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
sudo rsync -aHAX --delete "$SRC/" /mnt/hubara-vault/
```

**Cutover manual primero, merge después (default).** Con los workers
parados, aplicar el diff del §4.2 directamente sobre
`/opt/hubara/docker-compose.yml` en la caja y levantar:

```bash
docker compose up -d --remove-orphans
```

**Por qué NO cortar vía CI:** mergear el PR dispara `backend-deploy.yml`,
que primero hace el **build de la imagen** (varios minutos) antes de tocar
la caja — los workers quedarían parados todo ese build, no solo el delta
rsync. El merge del PR va DESPUÉS, con el servicio ya arriba, para que el
repo quede como fuente de verdad (ese deploy re-aplica el mismo compose:
no-op funcional). AMBAS copias del compose (repo y caja) tienen que terminar
iguales antes de cerrar la ventana.

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
`/var/lib/docker/volumes/hubara-prod_hubara-vault/_data` en este paso — queda como red
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

Como el volumen Docker `hubara-prod_hubara-vault` **nunca se tocó** (no se le hizo
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
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false
    # (post §4.1, los tags del root también viven acá — no en volume_tags)
  }
  # ...
}
```

`delete_on_termination = false` en el root deja de importar tanto una vez
migrado el vault (el root ya no tiene el dato irremplazable) — pero sigue
siendo gratis y correcto tenerlo. Se puede aplicar en un PR separado,
independiente de este plan, sin esperar a la ventana de migración.

## 8. Checklist de la sesión de ejecución

- [ ] PR de Terraform (§4.1: volumen + attachment + move `volume_tags`→`root_block_device.tags`; §4.3: cloud-init) + PR de compose (§4.2) — pueden ir en el mismo PR.
- [ ] `terraform plan` confirma 2 add / 1 change (in-place, SOLO tags de `aws_instance.app`) / 0 destroy antes de aplicar.
- [ ] Snapshot manual (§ Paso 0) antes de tocar nada.
- [ ] Pasada 1 (copia en caliente) completa y verificada (§ Paso 3-4).
- [ ] Ventana de corte comunicada/agendada (madrugada Bogotá, sin otros deploys en curso).
- [ ] Cutover manual en la caja (§ Paso 5) + verificación funcional (§ Paso 6) + merge del PR de compose DESPUÉS.
- [ ] Burn-in 24-48h antes de dar por cerrado SEC-09-full.
- [ ] Post-burn-in: quitar `Backup=daily` de `root_block_device.tags` — el DLM deja de snapshotear el root (§2.1); el vault queda como único volumen respaldado.
- [ ] Actualizar `SECURITY_AUDIT_fable.md` — mover SEC-09 de "guard" a "resuelto completo".
- [ ] Nota post-replacement (`findmnt /mnt/hubara-vault`) agregada a `infra/DEPLOY_RUNBOOK.md` (§4.3).
- [ ] (Opcional, fix aparte) §7 — `disable_api_termination` + `delete_on_termination=false`.
