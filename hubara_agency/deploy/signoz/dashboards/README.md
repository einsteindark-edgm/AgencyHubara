# Dashboards de SigNoz (as-code)

Los `*.json` de este directorio son la **fuente de verdad** de los tableros de
SigNoz, versionados en git. **Pero SigNoz no los carga solo**: hay que crearlos en
la instancia corriendo vía su API (un JSON en el repo NO aparece en la UI hasta
importarlo). Si reseteás el stack de SigNoz (se borra el volume de metadata), los
tableros se pierden y hay que reponerlos.

## Importar / reponer todos (idempotente)

```bash
# El PAT se crea una vez en la UI: Settings → API Keys → New key.
SIGNOZ_API_KEY=<PAT> python deploy/signoz/dashboards/import_dashboards.py
# opcional: SIGNOZ_URL=http://localhost:8080 (default)
```

Saltea los que ya existen (por título) → se puede re-correr sin duplicar.

## Tableros

| Archivo | Tablero |
|---|---|
| `llm-costos.json` | Costos LLM · AgencyHubara |
| `02-trazabilidad.json` | Trazabilidad & Durable Execution · AgencyHubara |
| `03-latencia.json` | Latencia & Velocidad · AgencyHubara |
| `04-operacion-agente.json` | Operación del Agente · AgencyHubara |
| `05-calidad-llm.json` | Calidad del LLM — Asesor de Ventas (regenerable con `gen_calidad_llm.py`) |

> El de **Calidad del LLM** consume las métricas `gen_ai.eval.*` que emite el harness
> de evaluación — solo tienen datos después de la 1ª corrida del eval (worker
> `sales_eval` o un trigger manual). Si un panel se ve vacío, ampliá el rango de
> tiempo (arriba a la derecha) y verificá que el harness ya corrió al menos una vez.
