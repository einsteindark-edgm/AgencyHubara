# robotocore — réplica local de AWS para probar el Terraform

[robotocore](https://github.com/robotocore/robotocore) es un *digital twin* de AWS
(drop-in tipo LocalStack) que responde llamadas reales de la API de AWS en `:4566`,
sin nube, sin costo, sin registro. Lo usamos para correr el **mismo Terraform** que
va a producción, pero apuntado acá → *probar para no equivocarnos en la real*.

## Uso

```bash
docker compose -f docker-compose.robotocore.yml up -d        # emulador en :4566
curl -s http://localhost:4566/_robotocore/health | jq        # health
./test-local.sh                                              # plan+apply+asserts
docker compose -f docker-compose.robotocore.yml down         # apagar
```

`./test-local.sh --up` levanta y apaga robotocore solo.

## Cómo funciona el switch

Los providers de `../terraform/{platform,compute}` leen `var.aws_endpoint`:
- **vacío** → AWS real (creds reales, endpoints de AWS).
- **`http://localhost:4566`** → robotocore (creds dummy `test/test`, S3 path-style,
  todos los services al `:4566`).

`local.platform.tfvars` y `local.compute.tfvars` setean ese endpoint. El `init` usa
`-backend=false` (state local efímero — no necesitamos S3/lock para un test).

## Qué se valida y qué no

| Capa | robotocore | Notas |
|---|---|---|
| Grafo TF completo (`plan`) | ✅ | platform + compute — caza wiring/tipos/refs |
| S3, Cognito, SSM, IAM | ✅ apply + asserts | alta fidelidad (Moto nativo/fuerte) |
| EC2, SGs, EIP, instance profiles | ✅ apply | el grafo aplica; el cloud-init NO ejecuta (no hay VM) |
| CloudFront, ACM | ⚠️ best-effort | el `plan` valida; edge/validación-DNS solo en real |
| Temporal Cloud | ❌ | no es AWS — ver `../terraform/temporal-cloud` |

Los asserts del script son el gate: si S3/Cognito/SSM/IAM/EC2 quedan creados en el
emulador, el grafo es sano. Lo que robotocore no cubre (edge de CDN, ejecución de
cloud-init, DNS) se prueba recién en un primer apply real acotado.
