# Alertas de SigNoz — calidad del agente

Reglas de alerta versionadas (igual que `../dashboards/`). Fuente de verdad en git;
SigNoz no las toma solas → se aplican vía API con `import_alerts.py`.

## Aplicar

```bash
SIGNOZ_API_KEY=<PAT> [SIGNOZ_URL=http://localhost:8080] \
    python deploy/signoz/alerts/import_alerts.py
```

(El PAT se crea una vez en la UI: **Settings → API Keys → New key**.)

## Las alertas

### `eval-calidad-baja.json` — "Calidad del agente de ventas por debajo de 0.7"

Dispara cuando el **score promedio de evaluación** de las **conversaciones reales del
día** (`eval.suite=online`) cae **por debajo de 0.7**.

- **Métrica**: `gen_ai.eval.conversation` (histograma; el promedio = `.sum / .count`,
  igual que el widget *"Score promedio por conversación"* del dashboard Calidad LLM).
- **Filtro**: `eval.suite = online` — solo el eval diario sobre tráfico real, **no** el
  golden (que puede tener scores bajos legítimos en escenarios difíciles).
- **Umbral**: `< 0.7` (`op:"2"` = below, `matchType:"1"` = al menos una vez en la ventana).
- **Ventana/frecuencia**: 6h / 1h — el eval online emite 1×/día, así que un chequeo
  horario con ventana de 6h captura el punto del día.

## Si el POST falla (schema cambió de versión)

El schema de `/api/v1/rules` es sensible a la versión de SigNoz (acá v0.126). La query
de la regla replica una que **ya funciona** en el dashboard, pero si la API la rechaza,
creala desde la **UI en 3 pasos** (es la misma definición):

1. **Alerts → New Alert → Metric based**.
2. Query Builder:
   - `A` = métrica `gen_ai.eval.conversation.sum`, agg `latest`, space-agg `sum`,
     filtro `eval.suite = online`, **disabled**.
   - `B` = `gen_ai.eval.conversation.count`, agg `latest`, space-agg `sum`,
     filtro `eval.suite = online`, **disabled**.
   - Fórmula `F1 = A/B` (este es el score promedio).
3. **Alert condition**: `F1` **below** `0.7`, *at least once*, ventana 6h.
   Severity `warning`. Notification channel: el que uses (Slack/email/etc.).

> Para alertar por **una métrica puntual** (ej. solo `script_adherence < 0.7`) en vez
> del promedio: misma idea con `gen_ai.eval.score.sum/.count` + filtro
> `metric.name = script_adherence` y `eval.suite = online`.
