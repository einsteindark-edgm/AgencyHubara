# Implementation plan — 05 Integración y rollout

- **Refinement**: `.exoclaw/refinements/05-integration-and-rollout-tech.md`
- **Depends on**: HU-01..04 mergeadas.
- **Implementer**: exoclaw-implementer
- **Date**: 2026-05-07

## 1. PR sequence (each step keeps tests green)

### PR-1: env vars + cheatsheet
**Goal**: documentar y declarar las nuevas env vars.
**Files**:
- EDIT `.env` (DEV) — añadir `MEDUSA_BASE_URL`, `MEDUSA_ADMIN_TOKEN`, `CATALOG_SNAPSHOT_DIR`, `CATALOG_MAX_AGE_MINUTES`. **Cuidado**: NO commitear el `.env` real con `MEDUSA_ADMIN_TOKEN` real. Verificar `.gitignore`.
- CREATE `.env.example` (si no existe) o EDIT — mismas vars con placeholders.
- EDIT `cheatsheet_produccion.md` — añadir sección "Catálogo / catalog_sync".
**Verification**:
```bash
git diff --name-only | grep -E "(\.env\.example|cheatsheet_produccion\.md)$"
test -z "$(grep -E '^MEDUSA_ADMIN_TOKEN=sk_' .env || echo)" && echo ".env tiene placeholder"
```

### PR-2: Schedule script idempotente
**Goal**: helper de ops que crea o actualiza la Schedule de Temporal.
**Files**:
- CREATE `scripts/__init__.py`.
- CREATE `scripts/create_catalog_sync_schedule.py`.
- CREATE `tests/scripts/test_create_catalog_sync_schedule.py` (smoke).
**Verification**:
```bash
uv run pytest tests/scripts/ -x
# Manual:
uv run python scripts/create_catalog_sync_schedule.py --dry-run
```

> **Skill defer (recomendado)**: invocar `temporal:temporal-developer` antes de escribir el script para confirmar la API exacta de `client.create_schedule(...)` en la versión de `temporalio` que el repo usa. La firma puede haber cambiado entre versiones.

### PR-3: K8s manifests
**Goal**: nuevo deployment para `catalog-sync-worker` que monta el PVC compartido EFS.
**Files**:
- CREATE `k8s/aws-produccion/worker-catalog-sync.yaml`.
- EDIT `k8s/aws-produccion/worker-sales.yaml` — añadir env vars `CATALOG_SNAPSHOT_DIR` y `CATALOG_MAX_AGE_MINUTES`. Confirmar que ya monta `hubara-vault-efs` (sí, ver `worker-sales.yaml:48-51`).
- (Opcional) CREATE `k8s/aws-produccion/medusa-secret.yaml` si los secrets no se manejan via SealedSecrets / SOPS / external-secrets-operator.
**Verification**:
```bash
kubectl --context=staging apply --dry-run=client -f k8s/aws-produccion/worker-catalog-sync.yaml
kubectl --context=staging apply --dry-run=client -f k8s/aws-produccion/worker-sales.yaml
```

### PR-4: Test e2e + fixture preparation
**Goal**: integration test que sin Medusa real exhiba el ciclo completo.
**Files**:
- CREATE `tests/integration/__init__.py`.
- CREATE `tests/integration/test_catalog_e2e.py`.
**Verification**:
```bash
uv run pytest tests/integration/test_catalog_e2e.py -x
```

### PR-5: Deprecación blanda de la skill hardcoded
**Goal**: marcar la skill `hubara_catalog/SKILL.md` como `always: false` para que el LLM ya no la inyecte automáticamente, manteniendo el contenido como fallback cargable.
**Files**:
- EDIT `src/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md` — frontmatter de `metadata: {"exoclaw": {"always": true}}` a `metadata: {"exoclaw": {"always": false}}`.
- CREATE `tests/sales_whatsapp/workspace/test_skill_frontmatter.py`.
**Verification**:
```bash
uv run pytest tests/sales_whatsapp/workspace/test_skill_frontmatter.py -x
```

### PR-6: Activación staging + smoke completo
**Goal**: hacer rollout en el clúster de staging y validar end-to-end real.
**Files**: ninguno (es operacional).
**Steps**:
1. `kubectl apply` de los manifiestos PR-3.
2. Crear el Secret con `MEDUSA_*` (OOB del repo, según política).
3. `uv run python scripts/create_catalog_sync_schedule.py --env staging`.
4. Esperar 1 ciclo (5 min); validar `manifest.json` en el PVC.
5. Mandar mensaje de prueba a Sales (vía `simulate_whatsapp.py` o número real).
6. Verificar que el LLM cita `handle`s reales del snapshot.

## 2. File-by-file (canonical content)

### `.env.example` (NEW or EDIT)

Bloque a añadir:

```env
# Medusa Admin API (HU-01)
# Crear el Secret API Key en https://<medusa>/app → Settings → Developer → Secret API Keys
MEDUSA_BASE_URL=http://localhost:9000
MEDUSA_ADMIN_TOKEN=sk_local_PLACEHOLDER_REPLACE_ME

# Catalog snapshot (HU-02 + HU-03)
# DEV: default = <repo>/hubara_agency/catalog_workspace
# PROD: monta EFS PVC en /var/lib/hubara/catalog
CATALOG_SNAPSHOT_DIR=
CATALOG_MAX_AGE_MINUTES=30
```

### `.env` (EDIT — solo dev local)

Mismas vars con valores reales del Medusa de dev. **NO commitear** — confirmar `.gitignore` ya excluye `.env`.

### `cheatsheet_produccion.md` (EDIT — añadir sección al final)

```markdown
## Catálogo / catalog_sync

El agente `catalog_sync` sincroniza el catálogo de Medusa cada 5 min y deja un snapshot atómico que el agente Sales lee microsegundos vía filesystem (sin llamadas a la red por turno de chat).

### Activar la Schedule (1 vez por entorno)
```bash
uv run python scripts/create_catalog_sync_schedule.py --env <staging|prod>
```
Idempotente — si la Schedule ya existe, actualiza el spec sin duplicar.

### Forzar un sync ahora (debugging)
```bash
temporal schedule trigger --schedule-id catalog-sync-default
```

### Ver últimos syncs
```bash
temporal workflow list --query 'WorkflowType="CatalogSyncWorkflow"' --limit 20
```

### Ver logs del worker
```bash
kubectl logs -n default deploy/hubara-worker-catalog-sync --tail=200 -f
```

### Inspeccionar el snapshot vivo
```bash
kubectl exec -n default deploy/hubara-worker-catalog-sync -- ls -la /var/lib/hubara/catalog
kubectl exec -n default deploy/hubara-worker-catalog-sync -- cat /var/lib/hubara/catalog/manifest.json
```

### Rollback rápido
```bash
# Detener el sync agent (Sales sigue leyendo el último snapshot bueno):
kubectl scale deploy hubara-worker-catalog-sync --replicas=0

# Revertir las tools de catálogo en Sales (HU-04):
kubectl set image deploy/hubara-worker-sales worker=hubara-agency-prod:<sha-anterior>
```

### Marcar skill `hubara_catalog` como manual
Después de ≥1 semana sin alucinaciones, borrar el archivo:
```bash
git rm src/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md
```
(Mientras tanto vive con `always: false` — cargable manualmente con `load_skill` si el catálogo dinámico falla.)
```

### `scripts/create_catalog_sync_schedule.py` (NEW)

```python
"""Crea o actualiza la Temporal Schedule que dispara CatalogSyncWorkflow.

Idempotente: re-correrlo no duplica la Schedule. Útil para CI/CD post-deploy.

NOTA: la API exacta de Schedule en `temporalio` puede variar por versión.
Antes de mergear, validar con `temporal:temporal-developer` skill que la
firma de `client.create_schedule(...)` y los enums (ScheduleOverlapPolicy)
matcheen la versión instalada.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    ScheduleSpec,
    SchedulePolicy,
)

from src.catalog_sync.contracts import CatalogSyncInput
from src.catalog_sync.workflows import CatalogSyncWorkflow
from src.platform.catalog.paths import get_snapshot_dir
from src.platform.constants import CATALOG_SYNC_QUEUE
from src.platform.temporal.client import get_temporal_client

SCHEDULE_ID = "catalog-sync-default"


async def upsert_schedule(*, interval_minutes: int, dry_run: bool) -> None:
    client: Client = await get_temporal_client()
    snapshot_dir = str(get_snapshot_dir())

    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            CatalogSyncWorkflow.run,
            CatalogSyncInput(
                tenant_id="default",
                force_full_refresh=True,
                snapshot_dir=snapshot_dir,
            ),
            id=f"catalog-sync-{{ScheduledTime}}",  # Temporal substituye en runtime
            task_queue=CATALOG_SYNC_QUEUE,
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(minutes=interval_minutes))],
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )

    if dry_run:
        print(f"[dry-run] Would upsert schedule={SCHEDULE_ID} every={interval_minutes}min snapshot_dir={snapshot_dir}")
        return

    try:
        handle = await client.create_schedule(SCHEDULE_ID, schedule)
        print(f"Created schedule {SCHEDULE_ID}")
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(SCHEDULE_ID)
        await handle.update(lambda _input: schedule)
        print(f"Updated existing schedule {SCHEDULE_ID}")

    # Trigger inmediato así no esperamos al primer intervalo.
    await handle.trigger()
    print(f"Triggered immediate run of {SCHEDULE_ID}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env", default="dev", choices=["dev", "staging", "prod"])
    args = parser.parse_args()
    try:
        asyncio.run(upsert_schedule(
            interval_minutes=args.interval_minutes, dry_run=args.dry_run,
        ))
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### `tests/scripts/test_create_catalog_sync_schedule.py` (NEW)

```python
"""Smoke test del script. NO conecta a Temporal — solo importa y --dry-run."""
import subprocess, sys


def test_dry_run_does_not_crash():
    # subprocess porque el script tiene main() que llama sys.exit
    result = subprocess.run(
        [sys.executable, "scripts/create_catalog_sync_schedule.py", "--dry-run"],
        capture_output=True, text=True,
    )
    # Permitimos exit 0 (dry-run real) o exit 1 (Temporal no alcanzable en CI).
    # Lo crítico: NO debe haber ImportError ni SyntaxError.
    assert "Traceback" not in result.stderr or "ConnectionRefusedError" in result.stderr
```

### `k8s/aws-produccion/worker-catalog-sync.yaml` (NEW)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hubara-worker-catalog-sync
  namespace: default
spec:
  replicas: 1  # CRITICAL: single writer (R4 del refinement). NO escalar.
  strategy:
    type: Recreate  # Para evitar dos pods escribiendo simultáneamente durante un rollout.
  selector:
    matchLabels:
      app: hubara-worker-catalog-sync
  template:
    metadata:
      labels:
        app: hubara-worker-catalog-sync
    spec:
      containers:
        - name: worker
          image: hubara-agency-prod:latest
          imagePullPolicy: Always
          command: ["python", "-m", "hubara_agency.src.catalog_sync.worker"]
          env:
            - name: TEMPORAL_URL
              value: "temporal-frontend.temporal.svc.cluster.local:7233"
            - name: TEMPORAL_NAMESPACE
              value: "default"
            - name: TEMPORAL_TLS_CERT_PATH
              value: "/etc/temporal-certs/temporal.pem"
            - name: TEMPORAL_TLS_KEY_PATH
              value: "/etc/temporal-certs/temporal.key"
            - name: CATALOG_SNAPSHOT_DIR
              value: "/var/lib/hubara/catalog"
            - name: MEDUSA_BASE_URL
              valueFrom:
                secretKeyRef:
                  name: hubara-medusa-secret
                  key: MEDUSA_BASE_URL
            - name: MEDUSA_ADMIN_TOKEN
              valueFrom:
                secretKeyRef:
                  name: hubara-medusa-secret
                  key: MEDUSA_ADMIN_TOKEN
          volumeMounts:
            - name: hubara-vault
              mountPath: /var/lib/hubara/catalog
              subPath: catalog  # subdir dentro del PVC compartido (separado del vault de sesiones)
            - name: temporal-certs
              mountPath: "/etc/temporal-certs"
              readOnly: true
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
      volumes:
        - name: hubara-vault
          persistentVolumeClaim:
            claimName: hubara-vault-efs
        - name: temporal-certs
          secret:
            secretName: temporal-cloud-certs
```

### `k8s/aws-produccion/worker-sales.yaml` (EDIT — añadir env vars y volumeMount)

Añadir en `env:` (después de `WORKSPACE_VAULT_DIR`):

```yaml
            - name: CATALOG_SNAPSHOT_DIR
              value: "/var/lib/hubara/catalog"
            - name: CATALOG_MAX_AGE_MINUTES
              value: "30"
```

Añadir en `volumeMounts:` (después del existente `hubara-vault`):

```yaml
            - name: hubara-vault
              mountPath: /var/lib/hubara/catalog
              subPath: catalog
              readOnly: true  # Sales solo LEE el catálogo (R-DIP en runtime)
```

> **Nota EFS**: el PVC `hubara-vault-efs` (RWX, EFS, ver `efs-pv.yaml`) ya existe. Reusarlo con `subPath: catalog` evita crear un PVC nuevo. Sales lee con `readOnly: true` por defensa en profundidad (no debería escribir aunque pudiera). `catalog_sync` escribe sin `readOnly`.

### `k8s/aws-produccion/medusa-secret.yaml` (NEW — opcional, si no usan SealedSecrets)

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: hubara-medusa-secret
  namespace: default
type: Opaque
stringData:
  # PLACEHOLDERS — sobrescribir con valores reales OOB del repo.
  MEDUSA_BASE_URL: "https://medusa.hubara.example.com"
  MEDUSA_ADMIN_TOKEN: "sk_REPLACE_WITH_REAL_SECRET_FROM_MEDUSA_ADMIN_PANEL"
```

> **No commitear este archivo con valores reales**. Si el repo usa SealedSecrets / external-secrets-operator / SOPS, reemplazar este YAML por el manifest equivalente.

### `tests/integration/test_catalog_e2e.py` (NEW)

```python
"""Integration test — escribe snapshot manual + Sales tool lo consume.

NO requiere Medusa real. NO requiere Temporal. Verifica que el contrato
entre los DTOs de HU-02, el escritor de HU-03 (use case directo) y la
tool de HU-04 esté íntegro.
"""
import json, pytest
from pathlib import Path

from src.catalog_sync.contracts import WriteSnapshotInput
from src.catalog_sync.use_cases.write_snapshot import WriteSnapshotUseCase
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.sales_whatsapp.tools.catalog import SearchProductsTool, GetProductByHandleTool
from exoclaw.agent.tools import ToolContext


@pytest.mark.asyncio
async def test_e2e_write_then_read_then_tool(tmp_path: Path):
    # 1) Sync agent escribe snapshot.
    products = [
        {"id": "1", "handle": "luz-serena", "title": "Luz Serena", "status": "published",
         "variants": [{"id": "v1", "title": "u",
                       "prices": [{"amount": "23000", "currency_code": "cop"}]}]},
        {"id": "2", "handle": "vela-cruz", "title": "Cruz de Vida", "status": "published",
         "variants": [{"id": "v2", "title": "u",
                       "prices": [{"amount": "17000", "currency_code": "cop"}]}]},
    ]
    use_case = WriteSnapshotUseCase()
    await use_case.execute(WriteSnapshotInput(
        products_json=json.dumps(products),
        count=2,
        fetched_at="2099-05-07T12:00:00+00:00",  # future-dated, no stale
        snapshot_dir=str(tmp_path),
    ))

    # 2) LocalSnapshotCatalogClient lee.
    catalog = LocalSnapshotCatalogClient(tmp_path)

    # 3) Sales tool consume.
    search_tool = SearchProductsTool(workspace=tmp_path, catalog=catalog)
    out = await search_tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        q="luz",
    )
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["stale"] is False
    assert payload["results"][0]["handle"] == "luz-serena"
    assert payload["results"][0]["price"] == "23000"

    # 4) get_product_by_handle también funciona.
    get_tool = GetProductByHandleTool(workspace=tmp_path, catalog=catalog)
    out = await get_tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="vela-cruz",
    )
    payload = json.loads(out)
    assert payload["found"] is True
    assert payload["product"]["title"] == "Cruz de Vida"

    # 5) Handle inexistente → found:false (anti-alucinación).
    out = await get_tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="patata-frita",
    )
    payload = json.loads(out)
    assert payload["found"] is False
```

### `src/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md` (EDIT — frontmatter)

```diff
 ---
 description: Catálogo de velas Hubara, precios COP, envíos, políticas, métodos de pago, garantía.
-metadata: {"exoclaw": {"always": true}}
+metadata: {"exoclaw": {"always": false}}
 ---
```

> **Frontmatter rule**: single-line inline JSON. NO usar block scalar (`metadata: |`). Verificable con grep en §5.

### `tests/sales_whatsapp/workspace/test_skill_frontmatter.py` (NEW)

```python
"""Verifica que la skill hubara_catalog está deprecada (always: false)."""
import re
from pathlib import Path

SKILL = (
    Path(__file__).parents[2]
    / "src/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md"
)


def test_metadata_is_single_line_inline_json():
    text = SKILL.read_text(encoding="utf-8")
    # Frontmatter rule: single-line inline JSON, no block scalar
    assert "metadata: |" not in text, "block scalar form silently breaks the loader"
    assert re.search(r'^metadata:\s*\{', text, flags=re.MULTILINE), \
        "metadata must be inline JSON, not multiline"


def test_skill_marked_deprecated():
    text = SKILL.read_text(encoding="utf-8")
    assert '"always": false' in text, \
        "skill debe estar marcada always:false (HU-05 rollout)"
```

## 3. Tests to add

Ver §1. Resumen:
- Unit: `test_skill_frontmatter.py`.
- Integration: `test_catalog_e2e.py`.
- Smoke (subprocess): `test_create_catalog_sync_schedule.py`.

## 4. Replay fixture refresh

N/A para esta HU (no toca workflow signatures).

## 5. Verification commands (run between every PR)

```bash
# Tests
uv run pytest -x

# Frontmatter check (todas las skills, no solo hubara_catalog)
grep -rEn "^metadata: \|" src/sales_whatsapp/workspace/skills/*/SKILL.md \
  || echo "skill frontmatter ok (single-line JSON only)"

# K8s manifests dry-run (requiere kubectl + contexto válido)
for f in k8s/aws-produccion/*.yaml; do
  kubectl apply --dry-run=client -f "$f" || echo "VALIDATION FAILED: $f"
done

# .env real no commiteado
git ls-files | grep -E "^\.env$" && echo "ERROR: .env should not be tracked" || echo ".env not tracked ok"

# .env.example tiene placeholders, no secrets reales
grep -E "^MEDUSA_ADMIN_TOKEN=sk_(?!.*PLACEHOLDER)" .env.example \
  && echo "ERROR: .env.example contains real-looking secret" \
  || echo ".env.example has placeholder ok"
```

## 6. Smoke-test recipe (post-deploy en staging)

```bash
# 0) Pre-requisitos: HU-01..04 mergeadas + image pushed + secret creado.

# 1) Aplicar manifests al cluster staging
kubectl --context=staging apply -f k8s/aws-produccion/medusa-secret.yaml  # si aplica
kubectl --context=staging apply -f k8s/aws-produccion/worker-catalog-sync.yaml
kubectl --context=staging apply -f k8s/aws-produccion/worker-sales.yaml

# 2) Verificar pods
kubectl --context=staging get pods -l 'app in (hubara-worker-catalog-sync,hubara-worker-sales)'

# 3) Crear la Schedule
uv run python scripts/create_catalog_sync_schedule.py --env staging
# Esperado: "Created schedule catalog-sync-default" + "Triggered immediate run"

# 4) Esperar 30s a que el primer trigger termine
sleep 30
kubectl --context=staging exec deploy/hubara-worker-catalog-sync -- \
  cat /var/lib/hubara/catalog/manifest.json
# Esperado: {"version": "...", "fetched_at": "<reciente>", "product_count": <N>}

# 5) Verificar que Sales puede leer el snapshot
kubectl --context=staging exec deploy/hubara-worker-sales -- \
  ls /var/lib/hubara/catalog/by_handle | head

# 6) Mensaje de prueba
uv run python src/tests/simulate_whatsapp.py "qué velas tienen?"
# Inspeccionar logs de Sales y verificar:
#  a) `tool_definitions_json` incluye search_products + get_product_by_handle
#  b) Llamada al LLM produce un tool_use de search_products
#  c) Respuesta final cita un `handle` real del snapshot
#  d) NO cita precios diferentes a los del envelope

# 7) Test negativo (anti-alucinación)
uv run python src/tests/simulate_whatsapp.py "tienen la vela mágica de unicornio?"
# Esperado: el LLM hace search_products, ve count=0, dice "no manejamos ese producto".
# NO inventa una "vela mágica de unicornio".

# 8) Esperar 5+ min y verificar segundo ciclo
sleep 360
diff <(kubectl --context=staging exec deploy/hubara-worker-catalog-sync -- cat /var/lib/hubara/catalog/manifest.json) \
     <(echo "{}")
# Esperado: 2 versions distintas (uuids diferentes), ambos `fetched_at` recientes.
```

## 7. Rollback strategy

| Componente | Cómo revertir |
|---|---|
| K8s manifests | `kubectl rollout undo deploy/hubara-worker-catalog-sync` y `hubara-worker-sales`. |
| Schedule | `temporal schedule delete --schedule-id catalog-sync-default`. Sales seguirá leyendo el último snapshot bueno hasta que envejezca a `stale=True` (30 min) y degrade el tono. |
| Tools de Sales | Revert del PR-3 de HU-04 (las 2 líneas `register_tool_extension`). El `bootstrap_sales_session_activity` deja de incluirlas en `tool_definitions_json` para sesiones nuevas. |
| Skill `always: false` → `true` | Revert PR-5 de esta HU. El LLM vuelve a inyectar el catálogo hardcoded automáticamente en cada turno. |

**Orden recomendado de rollback** (de menos invasivo a más):
1. Skill flag back to `always: true` — restaura el catálogo hardcoded como red de seguridad inmediata.
2. Sales tools removidas del `worker.py` (deja de llamar `search_products`).
3. catalog-sync deployment scaled to 0 (deja de actualizar el snapshot, pero no rompe).
4. Si todavía hay regresión, revertir HU-01..04 completas a partir del último tag estable.

## 8. Coordination updates

ADRs:
- `ADR-2026-05-07-09: catalog_sync deployment con replicas=1 + strategy=Recreate`. Razón: single-writer enforce.
- `ADR-2026-05-07-10: PVC EFS compartido (subPath catalog) entre catalog_sync y sales`. Razón: RWX nativo evita complicaciones de S3/object storage.
- `ADR-2026-05-07-11: skill hubara_catalog deprecada blanda (always:false), borrado físico es follow-up`. Razón: red de seguridad mientras monitoreamos.

## 9. Risks I'm carrying forward from the refinement

- **R1 (RWX support)**: confirmado — el repo ya usa EFS RWX (`efs-pv.yaml`).
- **R2 (primer sync inmediato)**: el script hace `handle.trigger()` después de crear la Schedule.
- **R3 (cuándo borrar el SKILL.md)**: follow-up a 1-2 semanas post-deploy. Esta HU solo lo marca `always: false`.
- **R4 (observabilidad)**: logs estructurados de loguru ya existen. Métricas Prometheus quedan como follow-up.
- **R5 (alertas si Schedule falla)**: cron externo de monitoreo del manifest age — follow-up.
- **R6 (impacto en `dashboard/` agente)**: validar antes del rollout final — `grep -r "catalog\|hubara_catalog" src/dashboard/`. Si encuentra algo, decidir antes de mergear si toca migrar dashboard también.
- **Defer `temporal:temporal-developer`**: validar la API exacta del Schedule en PR-2.

---

**Status**: refinement validado, plan listo. **Stop point**: confirmar antes de PR-1.
