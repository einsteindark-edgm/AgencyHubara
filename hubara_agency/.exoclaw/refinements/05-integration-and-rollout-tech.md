# Tech refinement — 05 Integración y rollout (sync schedule + K8s + observabilidad + deprecación de skill hardcoded)

- **HU id**: catalog-05
- **Source**: dependencias de HU-01..04 + decisiones de operación
- **Target agent**: cross-agent operativo (`platform/`, `catalog_sync/`, `sales_whatsapp/`, K8s, env)
- **Refiner**: exoclaw-tech-refiner
- **Date**: 2026-05-07

## 1. Scope

**Summary**: Cerrar el loop end-to-end: activar la **Temporal Schedule** que dispara `CatalogSyncWorkflow` cada 5 min en producción, configurar el K8s deployment del worker `catalog_sync` con volumen compartido (PVC) entre `catalog_sync` y `sales_whatsapp`, completar `.env.example` y secrets K8s con las variables Medusa, añadir observabilidad básica (logs estructurados + manifest age check), correr el smoke-test e2e, y **deprecar la skill `hubara_catalog/SKILL.md` hardcoded** una vez que las tools dinámicas (HU-04) demuestren cobertura.

**Acceptance criteria**:
- Given los manifiestos K8s aplicados, When `kubectl get pods -n hubara`, Then aparece `deploy/catalog-sync-worker` con `replicas: 1` corriendo y `deploy/sales-worker` montando el mismo PVC `catalog-snapshot-pvc` en `/var/lib/hubara/catalog`.
- Given la Schedule creada (`temporal schedule create --schedule-id catalog-sync-default --interval 5m ...`), When pasan 5 min, Then `temporal workflow list` muestra ejecuciones con prefijo `catalog-sync-` exitosas.
- Given un sync exitoso, When un cliente manda un mensaje a Sales pidiendo "qué velas tienen", Then el LLM invoca `search_products`, recibe envelope con productos reales del catálogo de Medusa, y responde citando `handle`s del envelope.
- Given un mensaje preguntando "tienen la vela de patata frita" (un producto que NO existe), Then el LLM invoca `search_products(q="patata frita")` → `count: 0` → responde "no manejamos ese producto" (NO alucina).
- Given el manifest tiene `fetched_at` > 30 min atrás (la Schedule cae), When Sales hace `search_products`, Then el envelope lleva `stale: true` y el LLM degrada el tono ("voy a confirmar disponibilidad").
- `cheatsheet_produccion.md` actualizado con la sección "Catálogo / catalog_sync" (cómo arrancar, dónde miran logs, cómo forzar resync manual).
- `.env.example` y `k8s/secret.yaml` (o equivalente) tienen `MEDUSA_BASE_URL`, `MEDUSA_ADMIN_TOKEN`, `CATALOG_SNAPSHOT_DIR`, `CATALOG_MAX_AGE_MINUTES` documentados.
- La skill `workspace/skills/hubara_catalog/SKILL.md` se cambia de `metadata: {"exoclaw": {"always": true}}` a `metadata: {"exoclaw": {"always": false}}` (deprecación blanda — sigue cargable manualmente). Borrado total queda como follow-up cuando 100% del tráfico pase por las tools.

**Out of scope**:
- Implementación de HU-01..04 (este HU asume que ya están mergeadas).
- Migración de catálogo desde Medusa hacia otro backend (cambio de fuente).
- Meilisearch / motor de búsqueda externo (futuro).
- Tools de stock en vivo (futuro).
- Conditional GET / etag-based delta sync (futuro).

## 2. Workflow mode

**Decision**: N/A para esta HU como tal. **Pero**: aquí se decide y registra el `Schedule` que invoca el `CatalogSyncWorkflow` (turn-based, definido en HU-03).

**Defer to `temporal:temporal-developer`** para los detalles del Schedule API en Python. Por defecto recomiendo:

```python
# pseudo (verificar API exacta con temporal:temporal-developer)
await client.create_schedule(
    "catalog-sync-default",
    Schedule(
        action=ScheduleActionStartWorkflow(
            CatalogSyncWorkflow.run,
            CatalogSyncInput(tenant_id="default", force_full_refresh=True),
            id_template="catalog-sync-{{.ScheduledTime}}",
            task_queue=CATALOG_SYNC_QUEUE,
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(minutes=5))]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    ),
)
```

`overlap=SKIP` evita que dos sync corran si el anterior se demora.

**File**: helper script `scripts/create_catalog_sync_schedule.py` (no es código de runtime — herramienta de ops idempotente que se corre 1 vez por entorno).

## 3. Boundary DTOs (R-JSON)

Sin DTOs nuevos.

## 4. Activities

Sin activities nuevas.

## 5. Tools

Sin tools nuevas.

## 6. Use cases

Sin use cases nuevos.

## 7. State adapters

Sin state adapters nuevos. Pero **sí** decisiones operativas sobre el storage:

| Aspecto | Decisión |
|---|---|
| `CATALOG_SNAPSHOT_DIR` en prod | `/var/lib/hubara/catalog` montado desde `catalog-snapshot-pvc` (RWX). |
| PVC backend | Si el clúster soporta `ReadWriteMany` nativo (NFS / EFS / Filestore / Longhorn): usarlo. Si solo RWO: deploy `catalog_sync` y `sales_whatsapp` en el **mismo node** vía `nodeSelector` o sidecar pattern (catalog_sync como sidecar de sales). **Recomendado**: investigar capabilities del clúster actual en `k8s/`. |
| Tamaño inicial | `1Gi` (catálogo Hubara actual << 50MB). |
| Backup | No requerido — el snapshot es regenerable desde Medusa en 1 sync. |

## 8. Prompts / workspace changes

- `workspace/skills/hubara_catalog/SKILL.md` — **CAMBIAR** frontmatter de `metadata: {"exoclaw": {"always": true}}` a `metadata: {"exoclaw": {"always": false}}`. Cuerpo se conserva como fallback. **Frontmatter rule**: single-line inline JSON.
- `cheatsheet_produccion.md` (en `hubara_agency/`) — añadir sección:

  ```markdown
  ## Catálogo / catalog_sync

  ### Activar la Schedule (1 vez por entorno)
  uv run python scripts/create_catalog_sync_schedule.py

  ### Forzar un sync ahora
  temporal schedule trigger --schedule-id catalog-sync-default

  ### Ver últimos syncs
  temporal workflow list --query 'WorkflowType="CatalogSyncWorkflow"' --limit 20

  ### Ver logs del worker
  kubectl logs -n hubara deploy/catalog-sync-worker --tail=100 -f

  ### Inspeccionar snapshot
  kubectl exec -n hubara deploy/catalog-sync-worker -- ls -la /var/lib/hubara/catalog
  kubectl exec -n hubara deploy/catalog-sync-worker -- cat /var/lib/hubara/catalog/manifest.json
  ```
- `workspace/TOOLS.md` (Sales) — sin cambios adicionales (HU-04 ya lo dejó listo).

## 9. Composition wiring

Sin cambios — HU-01..04 ya cablearon todo.

## 10. Worker registration (`worker.py`)

Sin cambios — HU-03 creó `src/catalog_sync/worker.py` y HU-04 actualizó `src/sales_whatsapp/worker.py`.

## 11. Hard rules check

- **R-DET / R-JSON / R-STATELESS / R-HEARTBEAT / R-DIP**: **N/A en esta HU**. Las reglas se validan en HU-01..04 cuando el código se escribe. Esta HU es operacional (configuración + manifests + observabilidad + deprecación de un archivo markdown).

## 12. Tests

| Test file | Type | Asserts |
|---|---|---|
| `tests/integration/test_catalog_e2e.py` | Integration (con Temporal local + tmp PVC simulado por `tmp_path`) | 1) Lanzar `CatalogSyncWorkflow` con un `MedusaClient` fake → snapshot escrito. 2) Arrancar `SearchProductsTool` apuntando al mismo `tmp_path` → recibe productos. |
| `scripts/create_catalog_sync_schedule.py` | Idempotency | El script puede correrse N veces sin duplicar la Schedule (usa `client.get_schedule_handle(...)` + try/except). |
| `tests/sales_whatsapp/workspace/test_skill_frontmatter.py` | Unit | `hubara_catalog/SKILL.md` parsea frontmatter sin warnings (single-line JSON). `metadata.exoclaw.always` es `false`. |
| Manual (post-deploy smoke) | Manual checklist en `cheatsheet_produccion.md` | (a) `temporal schedule list` lo muestra. (b) PVC montado en ambos pods. (c) Mensaje real de WhatsApp de prueba que dispara `search_products` y recibe envelope no-vacío. |

Replay: si HU-03 generó `tests/catalog_sync/test_replay.py`, esta HU **no** cambia signature de workflow → fixture permanece.

## 13. Risks / open questions

- **R1 (operacional)**: ¿El clúster K8s actual soporta RWX? **Acción**: revisar `k8s/` y describir StorageClass disponibles. Si no hay RWX:
  - Opción A: sidecar pattern (`catalog_sync` como container en el mismo Pod que `sales_whatsapp`, ambos comparten un `emptyDir` o RWO PVC). Trade-off: acopla deploys.
  - Opción B: object storage (S3/R2) como medio — `catalog_sync` sube `snapshot.json`, `sales_whatsapp` baja con polling mtime via S3 metadata. Más cambios; queda como follow-up si A no aplica.
- **R2**: La Schedule corre 1 vez antes del primer mensaje real para "primear" el snapshot. **Acción**: el script `create_catalog_sync_schedule.py` ejecuta `client.get_schedule_handle("catalog-sync-default").trigger()` después de crearla, así el primer sync no espera 5 min.
- **R3**: ¿Cuándo borrar la skill `hubara_catalog/SKILL.md` físicamente? **Recomendación**: 2 semanas después de HU-04 mergeada, después de monitorear logs de tool calls y confirmar que ≥99% de turnos que mencionan productos llaman `search_products`/`get_product_by_handle`. **Out of scope HU-05** (es una decisión post-rollout); HU-05 solo cambia `always: true` → `always: false`.
- **R4**: Observabilidad mínima v1 — logs estructurados desde `catalog_sync/use_cases/*.py` con `structlog` o `loguru` (`loguru` ya se usa en `worker.py`). Métricas (`prometheus_client`) quedan como follow-up.
- **R5**: ¿Alertas si la Schedule deja de correr? **Recomendado v1**: un cron externo (n8n / GitHub Actions / k8s CronJob) que cada 30 min hace `cat manifest.json` y alerta si `fetched_at` > 30 min atrás. Out-of-band a Temporal. Out of scope HU-05; documentar follow-up.
- **R6**: Migración del agente `dashboard/` (existente en el repo) — ¿usa el catálogo de alguna forma? `grep -r "catalog\|hubara_catalog" src/dashboard/` antes de mergear HU-05.
- **R7**: Rollback strategy. Si HU-04 introduce regresión, secuencia de revert:
  1. Revertir el commit que hizo `register_tool_extension` para `search_products`/`get_product_by_handle` en `worker.py` (las tools dejan de existir; LLM cae en la skill `always: false` → no las ve, vuelve a usar el conocimiento general). Edge: el LLM puede haber "memorizado" tools entre turnos del mismo workflow — `continue_as_new` lo cura.
  2. Si insuficiente, restaurar `metadata: always: true` en la skill `hubara_catalog`.
  3. `kubectl rollout undo` para los workers.
- **Defer to `temporal:temporal-developer`**: la API exacta de `client.create_schedule(...)` y la semántica de `overlap=SKIP` vs `BUFFER_ALL`. Recomiendo invocar el skill en el primer PR de implementación de esta HU.
- **Defer to `claude-api`**: ninguno.

## 14. Implementation order (suggested)

1. **Documentación primero** — actualizar `cheatsheet_produccion.md` con la sección Catálogo (sin manifests aún, solo intent).
2. **Schedule script** — crear `scripts/create_catalog_sync_schedule.py` idempotente (consultar `temporal:temporal-developer` para el API correcto).
3. **`.env.example` y secrets** — añadir `MEDUSA_BASE_URL`, `MEDUSA_ADMIN_TOKEN`, `CATALOG_SNAPSHOT_DIR`, `CATALOG_MAX_AGE_MINUTES`.
4. **K8s manifests** — crear `k8s/catalog-snapshot-pvc.yaml`, `k8s/catalog-sync-deployment.yaml`. Modificar `k8s/sales-deployment.yaml` para montar el mismo PVC.
5. **Activación en staging** — aplicar manifests, correr el schedule script, verificar 2 ciclos de sync.
6. **Test e2e** — `tests/integration/test_catalog_e2e.py` con Temporal local.
7. **Smoke manual desde WhatsApp** — mensaje de prueba real, verificar que el LLM cita un `handle` real.
8. **Deprecación skill** — cambiar `metadata: always: true` → `false` en `hubara_catalog/SKILL.md`. Test del frontmatter.
9. **Rollout a prod** — repetir 4-5 contra prod cluster.
10. **Monitorear 1 semana** antes de borrar la skill (follow-up post-HU).

(Esta HU depende de HU-01..04 mergeadas. Es la última en la cadena.)

---

**Next step**:

```
/exoclaw-implementer .exoclaw/refinements/05-integration-and-rollout-tech.md
```
