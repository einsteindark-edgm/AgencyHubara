# Laboratorio de conversaciones: cómo se corre

El laboratorio toma turnos reales de producción (el banco), los vuelve a
jugar con uno o más bots (brazos) y los califica con el mismo scorecard de
Calidad LLM. Sirve para medir el bot nuevo (V2 + Jev) o una versión nueva de
un paquete de decisión antes de encenderlos en Chats.

- Diseño completo: `LABORATORIO_CONVERSACIONES_PLAN.md` (raíz).
- Almacén y caja (SDK): `docs/_sdk/16-labkit.md`.
- Probar un paquete de decisión (`B@ventas-N`): `PAQUETES_DE_DECISION.md` §10 (F6).

Brazos: `A1` (el bot de hoy, re-simulado: el control), `B0` (V2 con reglas,
solo para probar paridad con A1), `B` (V2 con Jev en cada capacidad) y
`B@<paquete>` (el bot B con otra versión del paquete de decisión).

Regla del operador: el bot nuevo se prueba aquí, nunca encendiéndolo en
Chats del stack local.

## Dónde corre (2026-10-05)

**En local.** La caja de AWS del laboratorio existe en el código
(`infra/terraform/compute/modules/lab-instance`, `infra/compose/lab/`) pero
está apagada: `lab.enabled = false` en `infra/terraform/compute/tenants.auto.tfvars`
(decisión del operador: gastaba recursos sin necesidad). Por eso en
producción no hay `LAB_BUCKET` y la sección Laboratorio responde que está
apagado (503) en vez de lanzar corridas.

## Antes de la próxima corrida: llaves propias

El laboratorio usa sus propias llaves de LLM, con su propio tope de gasto.
La `OPENROUTER_API_KEY` del `.env` local era la misma de producción
(verificado el 2026-10-05): una corrida gastaba del tope de producción.

1. Genera tres llaves **nuevas**, cada una con límite de gasto:
   - OpenRouter (Jev);
   - DeepSeek (el LLM del bot);
   - Gemini (fotos, notas de voz y el juez).
2. Ponlas en el entorno del laboratorio con su nombre propio:

   ```
   OPENROUTER_API_KEY_LAB=
   DEEPSEEK_API_KEY_LAB=
   GEMINI_API_KEY_LAB=
   ```

3. El worker `sales_lab` no arranca sin las tres (su guard,
   `chats/agent/sales_lab/guard.py`, lo dice con el nombre que falta) y se las
   pasa a la app con el nombre que lee (`OPENROUTER_API_KEY`, …). Una llave
   con el nombre de producción que se cuele nunca llega a la app.
4. El proxy LiteLLM del laboratorio las recibe con el nombre que lee su
   config (`DEEPSEEK_API_KEY`, `GEMINI_API_KEY`), sin la de OpenRouter: Jev va
   directo a la Decisions API desde el worker. En la caja lo hace
   `dispatch.sh`; en local, quien arme el entorno del proxy.

## Las piezas de una corrida

| Pieza | Qué hace | Dónde está |
|---|---|---|
| Orden y banco | La API (`POST /api/chats/lab/runs`, botón «Nueva corrida») arma el banco desde el vault y guarda la orden en el almacén | `chats/api/lab.py`; almacén: `get_lab_store()` → `LAB_BUCKET` (S3) o `LAB_STORE_DIR` (disco) |
| Temporal | Temporal de desarrollo, namespace `hubara-lab` | servicio `temporal` de `infra/compose/lab/docker-compose.lab.yml` |
| Proxy LLM | LiteLLM con el `litellm_config.yaml` de la imagen de la corrida | servicio `litellm` del mismo compose |
| Worker | La misma imagen de producción: `python -m src.plugins.chats.workers.sales_lab` con `LAB_RUN_ID=<corrida>` | servicio `sales_lab` del mismo compose |
| Resultados | El worker los escribe en el almacén; la sección Laboratorio los lee de ahí | el mismo almacén |

El guard del worker exige, además de las llaves `*_LAB`:
- ninguna llave de producción (`WHATSAPP_*`, `MEDUSA_*`, `META_*`,
  `COGNITO_*`, `TEMPORAL_API_KEY`, `HUBARA_SERVICE_TOKEN`…);
- `TEMPORAL_URL=temporal:7233`, `TEMPORAL_NAMESPACE=hubara-lab` y
  `HUBARA_ENV=lab`;
- todas las carpetas que escribe la app dentro de `LAB_ROOT`.

## Correr en local hoy: lo que falta

**No hay todavía un script local en el repo.** La caja usa
`infra/compose/lab/dispatch.sh` con `docker-compose.lab.yml`, que tienen las
rutas de la caja (`/lab`, `/opt/lab`) y leen las llaves de SSM
(`/hubara-lab/*_LAB`).

La corrida local del 2026-09-29 se armó a mano:
- la API local con `LAB_STORE_DIR` (almacén en disco) para la orden y para
  ver los resultados en la sección Laboratorio;
- el worker `sales_lab` con el compose del laboratorio, su Temporal de
  desarrollo y solo llaves de LLM.

En local, «Nueva corrida» no lanza la corrida: el lanzador es
`Boto3LabLauncher`, que prende la caja por SSM. Aquel procedimiento quedó en
un scratchpad que ya no existe.

**Pendiente:** `infra/compose/lab/local.sh`, el equivalente local de
`dispatch.sh`:
- rutas locales;
- almacén en disco (`LAB_STORE_DIR`);
- las llaves `*_LAB` del entorno local;
- el proxy con los nombres que lee su config.

Se arma y se prueba en la próxima corrida, porque la prueba de verdad gasta
llamadas reales.

## Costos

Topes en Terraform (`tenants.<t>.lab`): `max_usd_per_run` = 120 y
`max_usd_per_month` = 300, que llegan como `LAB_MAX_USD_PER_RUN` y
`LAB_MAX_USD_PER_MONTH`. Los lee la API al crear la corrida: si el costo
estimado los pasa, responde 422. En local valen los mismos defaults (120 y
300) si no se fijan. El último tope es el límite de cada llave `*_LAB`.
