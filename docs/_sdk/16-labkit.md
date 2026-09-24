# 16 · LabKit — almacén y caja del laboratorio de conversaciones

> Fuente: `hubara_agency/src/sdk/labkit.py` → `src/platform/lab/` · Tests: `tests/platform/lab/` · Plan: `LABORATORIO_CONVERSACIONES_PLAN.md` §3.4 y §3.7

## Qué problema soluciona

El laboratorio simula el bot sobre las conversaciones reales en una **caja
aparte** (la de producción no tiene RAM para eso). Producción y caja no tienen
red entre sí: los datos viajan por un **S3 privado** y las órdenes por **SSM**.
El kit expone esas dos piezas a los plugins sin tocar `src.platform` (P-28).

## Qué expone

| Símbolo | Qué es |
|---|---|
| `LabStorePort` | contrato del almacén: `put_file` (streaming), `put_bytes`, `get_bytes` (None si no existe), `list_keys(prefix)`, `list_children(prefix)` (las "carpetas" inmediatas, sin recorrer cada objeto: en S3 con `Delimiter`). Claves relativas; nunca `..` ni `/` inicial |
| `S3LabStore` / `FilesystemLabStore` | adaptadores con la MISMA suite de contrato (el de disco es para tests y desarrollo) |
| `get_lab_store()` | `LAB_BUCKET` (Terraform, `/hubara/<tenant>/LAB_BUCKET`) → S3; `LAB_STORE_DIR` → disco; ninguno → `None` (laboratorio apagado: la API responde 503) |
| `LabBoxLauncher` / `Boto3LabLauncher` | prende la caja (tag `Role=lab`) y le da órdenes por SSM: `dispatch(run_id, imagen)` → `dispatched` \| `already_dispatched` (idempotente por id), `cancel(run_id)`. Reutiliza `Boto3Launcher` de GraphAgents |
| `RUN_ID_RE` / `IMAGE_RE` | la forma que aceptan los scripts de la caja |
| `installed_sandbox_ports(promotions_path=, catalog=)` | (caja, PR 11) pone en modo sandbox los puertos que hablan con Medusa: promociones del banco, pedido stub, verificación contra el snapshot; `SnapshotLiveMedusa` y `SandboxNoMedusaError` son sus piezas |
| `bench_catalog_client(dir)` | (caja, PR 13) `CatalogPort` sobre el snapshot del catálogo que viajó con el banco, sin tope de edad: el contexto del scorecard al calificar la corrida |

## Prefijos del bucket

| Prefijo | Escribe | Lee | Vida |
|---|---|---|---|
| `bench/<banco>/` | producción (exportador) | caja | 30 días |
| `orders/<corrida>.json` | producción (bots, repeticiones, banco, límite de gasto) | caja | 180 días |
| `runs/<corrida>/` | caja (`progress.json`, trazas, checks, resumen) | producción | 180 días |

## Quién lo usa

El lanzador de corridas de `chats` (`agent/sales_lab/launch/`, workflow
`LabLaunchWorkflow` en el worker `sales_eval`) y la API del botón
`/api/chats/lab/*`.
