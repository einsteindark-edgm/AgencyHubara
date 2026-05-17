# Example — Plugin frontend + API sin worker (template B)

> **Plugin real del repo:** ninguno actualmente. Este ejemplo es
> **hipotético** — un plugin `reports` que muestra reportes financieros
> con un endpoint propio para CSV export.
>
> **Use cuándo:** tu plugin necesita CRUD propio o endpoints custom,
> pero no tiene workflows long-running.

---

## §1. Archivos típicos

```
frontend_dashboard/src/plugins/reports/
├── plugin.yaml
└── frontend/
    ├── index.ts
    ├── ReportsSection.tsx
    └── features/
        └── report-table/
            ├── index.ts
            └── ui/ReportTable.tsx

hubara_agency/src/plugins/reports/
├── __init__.py
└── api/
    ├── __init__.py                            # docstring solo
    └── routes.py                              # APIRouter con endpoints

hubara_agency/tests/plugins/reports/
└── api/
    └── test_routes.py
```

---

## §2. Manifest

```yaml
# frontend_dashboard/src/plugins/reports/plugin.yaml
id: reports
version: 0.1.0
display_name: Reports
description: Reportes financieros con export CSV.

depends_on: []

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: reports, label: Reports, order: 7, icon: file }
    sidebar:
      - { route: /reports, label: Reports, icon: file }

api:
  python_module: src.plugins.reports.api.routes
  prefix: /api/reports
  tags: [Reports]

wiring_intents:
  env_vars_required: []
```

**Notas:**

- `python_module: src.plugins.reports.api.routes` — apunta al módulo
  que expone `router = APIRouter()`.
- Sin `legacy_routers:` — solo 1 router unificado (no como `chats`).
- El loader registra automáticamente con `prefix: /api/reports` y
  `tags: [Reports]`.

---

## §3. Routes (`api/routes.py`)

```python
# canonical — hubara_agency/src/plugins/reports/api/routes.py
from io import StringIO
import csv
from datetime import date

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/list")
async def list_reports(
    month: str = Query(..., regex=r"^\d{4}-\d{2}$"),
) -> list[dict]:
    """Lista los reportes del mes (YYYY-MM)."""
    # Datos hipotéticos — en real query a DB
    return [
        {"id": "r1", "name": f"Sales {month}", "amount": 12345.67},
        {"id": "r2", "name": f"Refunds {month}", "amount": 432.10},
    ]


@router.get("/{report_id}/export.csv")
async def export_report_csv(report_id: str) -> StreamingResponse:
    """Exporta el reporte como CSV streaming."""
    def generate():
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["item", "amount"])
        # Datos hipotéticos
        for i in range(100):
            writer.writerow([f"item_{i}", i * 12.34])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={report_id}.csv"},
    )
```

---

## §4. Frontend — consumir el endpoint con TanStack

### §4.1 Entity nueva o existente?

**Decisión:** si `reports` data es consumido SOLO por el plugin reports,
NO va en `entities/`. Va dentro del plugin (en `frontend/features/`).

Si después otro plugin (e.g. `dashboard-summary`) lo consume, **promote
a entities/** en un PR explícito.

### §4.2 Local hook (dentro del plugin)

```typescript
// canonical — plugins/reports/frontend/features/report-table/api.ts
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/shared/api/client";

const reportSchema = z.object({
  id: z.string(),
  name: z.string(),
  amount: z.number(),
});
export type Report = z.infer<typeof reportSchema>;
const reportListSchema = z.array(reportSchema);

export function useReportsForMonth(month: string) {
  return useQuery({
    queryKey: ["reports", "list", month],
    queryFn: async () => reportListSchema.parse(
      await apiClient.get<unknown>(`/api/reports/list?month=${month}`)
    ),
    enabled: !!month,
  });
}

export function exportReportCsvUrl(reportId: string): string {
  return `/api/reports/${reportId}/export.csv`;
}
```

### §4.3 Componente

```typescript
// canonical — plugins/reports/frontend/features/report-table/ui/ReportTable.tsx
import { useReportsForMonth, exportReportCsvUrl } from "../api";

interface Props {
  month: string;
}

export function ReportTable({ month }: Props) {
  const { data, isLoading, error } = useReportsForMonth(month);

  if (isLoading) return <div>Loading…</div>;
  if (error) return <div>Error: {error.message}</div>;
  if (!data) return null;

  return (
    <table>
      <thead><tr><th>Name</th><th>Amount</th><th>Export</th></tr></thead>
      <tbody>
        {data.map(r => (
          <tr key={r.id}>
            <td>{r.name}</td>
            <td>{r.amount.toFixed(2)}</td>
            <td><a href={exportReportCsvUrl(r.id)} download>CSV</a></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

### §4.4 Section root

```typescript
// canonical — plugins/reports/frontend/ReportsSection.tsx
import { useState } from "react";
import { ReportTable } from "./features/report-table";

export interface ReportsSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
}

export function ReportsSection({ showSidebar, showInspector }: ReportsSectionProps) {
  const [month, setMonth] = useState<string>("2026-05");

  return (
    <>
      {showSidebar && (
        <aside className="sidebar">
          <input
            type="month"
            value={month}
            onChange={e => setMonth(e.target.value)}
          />
        </aside>
      )}
      <main>
        <ReportTable month={month} />
      </main>
    </>
  );
}

export default ReportsSection;
```

---

## §5. Tests Python (API)

```python
# canonical — hubara_agency/tests/plugins/reports/api/test_routes.py
import pytest
from httpx import AsyncClient

@pytest.mark.functional
async def test_list_reports_returns_array(api_client: AsyncClient):
    response = await api_client.get("/api/reports/list?month=2026-05")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert all("id" in r and "name" in r and "amount" in r for r in data)


@pytest.mark.functional
async def test_list_reports_validates_month_format(api_client: AsyncClient):
    response = await api_client.get("/api/reports/list?month=BAD")
    assert response.status_code == 422   # FastAPI validation error


@pytest.mark.functional
async def test_export_csv_returns_streaming_csv(api_client: AsyncClient):
    response = await api_client.get("/api/reports/r1/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert "item,amount" in body
```

**Nota:** `api_client` fixture vive en `tests/functional/conftest.py`
(httpx ASGI transport — sin puerto real).

---

## §6. Playwright E2E

```typescript
// frontend_dashboard/e2e/reports/table.spec.ts
import { expect, test } from "@playwright/test";

test.describe("reports", () => {
  test("operator sees reports for selected month", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: "Reports" }).click();
    await page.getByLabel("month").fill("2026-05");
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByText(/Sales 2026-05|Refunds 2026-05/)).toBeVisible();
  });

  test("operator can export CSV", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: "Reports" }).click();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "CSV" }).first().click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
  });
});
```

---

## §7. Verificación

```bash
# Backend
cd hubara_agency
ENABLED_PLUGINS=reports uv run python run_api.py
# El loader debería loguear:
# [loader] registered src.plugins.reports.api.routes → prefix='/api/reports' tags=['Reports']

curl http://localhost:8000/api/reports/list?month=2026-05
# → [{"id":"r1",...}, ...]

curl http://localhost:8000/api/reports/r1/export.csv -o /tmp/r1.csv
# → CSV file

# Tests
uv run pytest tests/plugins/reports/ -v
uv run pytest tests/functional/ -m functional -v

# Frontend
cd ../frontend_dashboard
npm run plugins:sync     # registry incluye reports
npm test -- reports
npm run dev              # visible en /reports tab
npx playwright test e2e/reports/
```

---

## §8. Lo que NO va en este plugin

- `agent:` block — no hay workers Temporal.
- `hubara_agency/src/plugins/reports/agent/` — no existe.
- `hubara_agency/src/plugins/reports/workers/` — no existe.
- K8s worker manifest — no aplica.

Si el plugin necesita workflow async (e.g. generación de reportes que
tarda minutos), promote a template C/D.

---

## §9. Pros y limitaciones del template B

| Pro | Limitación |
|---|---|
| Backend lógica propia | No workflows long-running |
| API endpoints propios | Si un endpoint tarda >30s, va a timeoutear |
| Setup moderado (~1-2 horas) | No tool-loop LLM |
| Tests functional Python + Vitest + Playwright | Sin retry sofisticado (FastAPI no es Temporal) |

---

**Fin example.**
