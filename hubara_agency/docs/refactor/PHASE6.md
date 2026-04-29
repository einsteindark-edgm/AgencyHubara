---
title: Fase 6 - Cerrar deuda residual post-refactor
last_updated: 2026-04-28
status: Pendiente
---

# Fase 6 - Cerrar deuda residual post-refactor

Esta fase aborda los hallazgos diferidos detectados durante la revision integral (R7) del refactor DEHA. Las Fases 1-5 cerraron 20/20 hallazgos del AUDIT original; durante la revision aparecieron 3 hallazgos nuevos (N-1, N-2, N-3) que se difirieron por requerir drain operativo o por ser deuda consciente documentada en ADRs. Fase 6 los cierra y consolida el cumplimiento R-DET al 100%.

## Objetivo

1. Cerrar las 3 deudas R-DET blandas (N-1, N-2, N-3).
2. Establecer barrera CI contra regresiones futuras de shape de history (replay tests).
3. Eliminar residuos: shim deprecated `integrations.py`, codigo muerto menor.
4. Validacion runtime de la suite de tests (N-5).

## Resumen de fixes

| ID | Titulo | Origen | Riesgo | Cambia history? |
|----|--------|--------|--------|-----------------|
| F6.1 | `bootstrap_session_activity` para Remarketing | N-1 | Medio | Si |
| F6.2 | Replay tests con history fixtures | N-2 / ADR-005 | Bajo | No |
| F6.3 | `load_remarketing_brain_activity` | N-3 / ADR-006 | Medio | Si |
| F6.4 | Eliminar shim `integrations.py` | ADR-008 | Bajo | No |
| F6.5 | Limpieza menor (`metadata_content` muerto, etc.) | revision | Bajo | No |
| F6.6 | Validacion runtime de tests | N-5 | Bajo | No |
| F6.7 | CI gate: replay test obligatorio en PRs | F6.2 | Bajo | No |

**Total**: 7 fixes. 2 cambian shape de history (drain previo).

---

## F6.1 - `bootstrap_session_activity` para Remarketing (cierra N-1)

### Contexto
`src/domains/remarketing_whatsapp/workflows/remarketing.py:73-75` (dentro de `@workflow.run`) llama `build_workspace_config(session_id)` que ejecuta `Path.mkdir(parents=True, exist_ok=True)`. Esto es **I/O dentro del workflow** -> R-DET blando. Hoy funciona porque `mkdir(exist_ok=True)` es idempotente, pero si el filesystem cambia entre invocaciones (replay, multi-worker, EFS lag), el replay puede divergir.

Sales ya recibe el `SessionInput` armado desde el caller; Remarketing deberia hacer lo mismo.

### Tareas
1. Crear activity `bootstrap_remarketing_session_activity(session_id: str, motivo: str) -> SessionInput` en `src/domains/remarketing_whatsapp/activities.py`. Internamente:
   - Llama `build_workspace_config(session_id)` (esta vez OK, esta dentro de activity).
   - Llama `get_base_tools_registry(...)` y serializa tool_definitions_json.
   - Construye y retorna `SessionInput` JSON-safe.
2. Modificar `RemarketingSessionWorkflow.run`:
   - Recibe `RemarketingSessionInput(session_id, motivo)` (ya creado en F3.6).
   - **Primer paso**: `await workflow.execute_activity(bootstrap_remarketing_session_activity, args=[input.session_id, input.motivo], ...)`.
   - Recibe `SessionInput` listo y opera con el.
3. Eliminar imports de `build_workspace_config` y `get_base_tools_registry` del modulo del workflow (deberian estar solo dentro del bloque `imports_passed_through` y solo usarse por `_load_remarketing_brain` mientras N-3 no este resuelto).
4. Actualizar `schedule_remarketing_workflow_activity` para que NO construya `SessionInput`; el caller solo pasa `RemarketingSessionInput`.
5. Test:
   - `tests/test_bootstrap_remarketing_activity.py` - usa `ActivityEnvironment`, verifica que la activity retorna un `SessionInput` valido y que el filesystem se setupea.

### Done si
- `grep -n "build_workspace_config\|get_base_tools_registry" src/domains/remarketing_whatsapp/workflows/remarketing.py` solo aparece dentro del bloque `imports_passed_through()` (no en el cuerpo del `run`).
- El workflow Remarketing solo hace `await workflow.execute_activity(...)` antes de cualquier referencia a `Path` o `mkdir`.
- Test pasa: `pytest tests/test_bootstrap_remarketing_activity.py -v`.

### Riesgos
- **Cambia shape de history del workflow Remarketing**: aparece una activity execution event nueva al inicio. Workflows en vuelo lanzaran `NonDeterminismError`. **Mitigacion**: drain operativo previo al deploy (ADR-009).
- Si Sales en el futuro tambien lo necesita, replicar el patron alli (no en Fase 6, anotar como F7 si surge).

---

## F6.2 - Replay tests con history fixtures (cierra N-2 / cumple ADR-005 al 100%)

### Contexto
Hoy hay 8 archivos de tests pero ningun replay test. Cualquier cambio futuro a la shape de history puede pasar a produccion sin barrera CI. ADR-005 exige replay tests desde Fase 3.

### Tareas
1. Capturar history JSON de un workflow real en preprod (uno por tipo de workflow):
   - `temporal workflow show --workflow-id <sales_session_id> --output json > tests/fixtures/history_sales_session_v1.json`
   - `temporal workflow show --workflow-id <remarketing_session_id> --output json > tests/fixtures/history_remarketing_session_v1.json`
   - **Importante**: capturar despues de F6.1 si ya se aplico (sino la fixture queda obsoleta inmediatamente).
2. Crear `tests/test_replay_sales.py`:
   ```python
   import pytest
   from pathlib import Path
   from temporalio.client import WorkflowHistory
   from temporalio.worker import Replayer
   from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow

   FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_session_v1.json"

   @pytest.mark.asyncio
   async def test_sales_session_replay_does_not_diverge():
       history = WorkflowHistory.from_json("test", FIXTURE.read_text())
       replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
       await replayer.replay_workflow(history)
   ```
3. Idem `tests/test_replay_remarketing.py`.
4. Documentar en `tests/fixtures/README.md` el procedimiento para regenerar fixtures.
5. Si la captura desde preprod no es viable, generar fixtures sinteticas:
   - Test que arranca `WorkflowEnvironment.start_time_skipping`.
   - Ejecuta el workflow happy-path con activities mockeadas.
   - Captura `await handle.fetch_history()`.
   - Persiste a JSON en `tests/fixtures/`.

### Done si
- Existen `tests/test_replay_sales.py` y `tests/test_replay_remarketing.py` con fixtures correspondientes.
- Ambos pasan con `pytest tests/test_replay_*.py -v`.
- Documentacion de regeneracion en `tests/fixtures/README.md`.

### Riesgos
- Si las fixtures se generan antes de F6.1/F6.3, hay que regenerarlas tras esos cambios. **Mitigacion**: ejecutar F6.2 al final de Fase 6, despues de F6.1 y F6.3.
- Acceso a preprod puede no estar disponible. **Mitigacion**: usar fixtures sinteticas via `WorkflowEnvironment`.

---

## F6.3 - `load_remarketing_brain_activity` (cierra N-3 / supera ADR-006)

### Contexto
`src/domains/remarketing_whatsapp/workflows/remarketing.py:116,144` invocan `_load_remarketing_brain()` que termina en `Path.read_text(...)`. ADR-006 lo dejo como deuda consciente. Es el ultimo I/O de filesystem dentro del workflow.

### Tareas
1. Crear activity `load_remarketing_brain_activity() -> list[str]` en `src/domains/remarketing_whatsapp/activities.py`. Internamente llama `load_brain(REMARKETING_BRAIN_DIR)`.
2. Cachear el resultado en el workflow:
   ```python
   if self._brain_cache is None:
       self._brain_cache = await workflow.execute_activity(
           load_remarketing_brain_activity,
           start_to_close_timeout=timedelta(seconds=10),
       )
   plugin_context = self._brain_cache
   ```
3. Inicializar `self._brain_cache: list[str] | None = None` en `__init__`.
4. Eliminar `_load_remarketing_brain` del modulo del workflow (la unica funcion que llamaba a `load_brain` desde aqui).
5. Reemplazar las 2 callsites en lineas 116 y 144 por la version cacheada.
6. Mantener `core/brains.py::load_brain` (ahora solo lo usa la activity nueva y el `_load_shared_brain` de Sales que sigue siendo OK porque Sales lo carga desde el composition root, no desde `@workflow.run`).
7. Test:
   - `tests/test_load_brain_activity.py` - `ActivityEnvironment` ejecuta la activity con un `tmp_path` de brain falso, verifica que retorna list[str].

### Done si
- `grep -n "_load_remarketing_brain\|load_brain" src/domains/remarketing_whatsapp/workflows/remarketing.py` aparece solo dentro del bloque `imports_passed_through()` (si queda) o se elimina.
- El workflow no llama directamente a ninguna funcion que lea filesystem.
- Test pasa.

### Riesgos
- **Cambia shape de history del workflow Remarketing**. **Mitigacion**: drain operativo. **Coordinar con F6.1**: hacer ambos cambios en el mismo deploy para evitar dos drains seguidos.

---

## F6.4 - Eliminar shim `integrations.py` (cierra ADR-008)

### Contexto
`src/domains/sales_whatsapp/integrations.py` quedo como shim deprecated tras F3.7 (re-export desde `core/infrastructure/whatsapp/client.py`). ADR-008 dijo "eliminar cuando se confirme cero adopcion externa".

### Tareas
1. `grep -rn "from src.domains.sales_whatsapp.integrations\|from src.domains.sales_whatsapp import integrations" .` (excluyendo `docs/`, `tests/`, el propio shim).
2. Si 0 matches: eliminar `src/domains/sales_whatsapp/integrations.py` fisicamente.
3. Si hay matches: actualizar imports a la nueva ubicacion (`src.core.infrastructure.whatsapp.client`) y luego eliminar el shim.
4. Verificar que `python -c "from src.main import app"` y los workers siguen importando.

### Done si
- `src/domains/sales_whatsapp/integrations.py` no existe.
- `grep -rn "domains.sales_whatsapp.integrations" src/` -> 0 matches.

### Riesgos
- Imports externos al repo (scripts de operaciones, notebooks) que no captura el grep. **Mitigacion**: anunciar en changelog del PR; mantener el shim un release mas si hay duda.

---

## F6.5 - Limpieza menor

### Tareas
1. **F6.5.a** - Eliminar `metadata_content` no usado en `src/domains/remarketing_whatsapp/workflows/remarketing.py:84` (deuda menor preexistente, marcada en R7).
2. **F6.5.b** - Revisar si quedan `TODO` o `XXX` sin contexto en el codigo y limpiarlos o convertirlos en issues.
3. **F6.5.c** - Verificar que no queden `print(...)` de debug. Reemplazar por `logger`.
4. **F6.5.d** - `grep -rn "logger.info(.*temporal_client" src/` para detectar logs ruidosos del client.

### Done si
- No hay variables muertas reportadas por `ruff check --select F841 src/`.
- No hay `TODO` huerfanos.

### Riesgos
- Ninguno significativo.

---

## F6.6 - Validacion runtime de tests (cierra N-5)

### Contexto
Toda la suite de tests (creada en Fases 3-5) fue escrita pero no ejecutada en runtime. ADR-005 exige tests pasando antes de merge.

### Tareas
1. Ejecutar:
   ```bash
   cd hubara_agency
   uv sync --dev   # o equivalent segun el package manager
   pytest tests/ -v
   ```
2. Para cada test que falle: diagnosticar y arreglar (no eliminar tests).
3. Documentar en `tests/README.md` los comandos de ejecucion local + variables de entorno necesarias.
4. Validar que los workers arrancan:
   ```bash
   python -c "from src.main import app"
   python -c "from src.domains.sales_whatsapp.worker import main"
   python -c "from src.domains.remarketing_whatsapp.worker import main"
   ```

### Done si
- `pytest tests/ -v` -> verde.
- 3 imports de arriba retornan sin errores.
- `tests/README.md` documentado.

### Riesgos
- Si `pytest-asyncio` o `temporalio.testing` tienen incompatibilidad con la version Python del proyecto. **Mitigacion**: pinnear versiones explicitamente en `pyproject.toml`.
- Tests escritos sintacticamente correctos pueden fallar por mocks mal puestos. **Mitigacion**: arreglar uno a uno, no eliminar.

---

## F6.7 - CI gate: replay test obligatorio (consolida F6.2)

### Contexto
F6.2 crea los replay tests; F6.7 los hace bloqueantes en PRs.

### Tareas
1. Crear `.github/workflows/replay-tests.yml` (o equivalente segun el CI del proyecto):
   ```yaml
   name: Replay tests
   on: [pull_request]
   jobs:
     replay:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: "3.11"
         - run: pip install uv && uv sync --dev
         - run: cd hubara_agency && pytest tests/test_replay_*.py -v
   ```
2. Configurar branch protection rule en `main` para que el job sea required.
3. Documentar en `docs/refactor/README.md` el procedimiento de regeneracion de fixtures cuando un cambio legitimo del workflow shape lo requiera (con referencia a ADR-009).

### Done si
- El workflow CI corre en cada PR y bloquea merge si falla.
- Branch protection activado en `main`.

### Riesgos
- CI lento si la fixture es grande. **Mitigacion**: fixtures pequenas (3-5 turnos), no historias completas de produccion.

---

## Orden recomendado de ejecucion

1. **F6.5** (limpieza menor, sin riesgo).
2. **F6.4** (eliminar shim, bajo riesgo).
3. **F6.6** (validacion runtime de tests existentes - hacerlo antes de cambiar mas cosas).
4. **F6.1** + **F6.3** (cambian history shape - hacerlos juntos en el mismo PR/deploy con un solo drain).
5. **F6.2** (replay tests - despues de 6.1/6.3 para que las fixtures reflejen el codigo final).
6. **F6.7** (CI gate - al final, cuando los replay tests ya pasen localmente).

## Drain operativo necesario (ADR-009)

F6.1 y F6.3 cambian shape de history. Antes del deploy:

1. Activar feature flag para pausar nuevos signals (`process_incoming_message` rechaza con 503 temporalmente).
2. Esperar a que workflows en vuelo terminen:
   - Sales: `_IDLE_TIMEOUT` ~1 min + finalizacion del turno actual = ~2 min razonable.
   - Remarketing: `_IDLE_TIMEOUT` ~24h. Considerar forzar shutdown de workflows Remarketing si el tiempo no es aceptable.
3. Verificar via `temporal workflow list --query 'ExecutionStatus="Running"'` que no queden workflows activos.
4. Deploy del codigo nuevo (workers + API).
5. Desactivar feature flag.

## Done de Fase 6

- [ ] N-1, N-2, N-3 marcados como RESUELTO en AUDIT.md.
- [ ] R-DET cumplido al 100% (sin asteriscos en el veredicto arquitectonico).
- [ ] CI bloquea merges que rompan replay.
- [ ] Suite de tests ejecutada en verde al menos una vez.
- [ ] `integrations.py` eliminado fisicamente.
- [ ] `docs/refactor/README.md` actualizado a 6/6 fases completas.
- [ ] `docs/refactor/PROGRESS.md` con entradas de cada F6.x.

## Estimacion

| Fix | Esfuerzo | Bloqueante de | Tests nuevos |
|-----|----------|---------------|--------------|
| F6.1 | 2-3h | F6.2 | 1 |
| F6.2 | 3-4h | F6.7 | 2 |
| F6.3 | 1-2h | F6.2 | 1 |
| F6.4 | 30 min | - | 0 |
| F6.5 | 1h | - | 0 |
| F6.6 | variable (depende de fallos) | F6.7 | 0 |
| F6.7 | 1h | - | 0 |

**Total**: ~10h de trabajo concentrado + tiempo de drain operativo (~24h ventana).

## Riesgos globales de Fase 6

1. **Drain de Remarketing puede ser largo** (idle 24h). **Mitigacion**: aceptar window de mantenimiento o forzar shutdown de Remarketing en vuelo (ya hay `force_shutdown` flag en el workflow).
2. **Capturar history fixture desde preprod requiere acceso a Temporal Cloud / cluster preprod**. **Mitigacion**: usar fixtures sinteticas si no hay acceso.
3. **CI puede no estar configurado para correr tests Python**. **Mitigacion**: F6.7 asume GitHub Actions; adaptar al CI real del proyecto.

## Despues de Fase 6

Si Fase 6 se completa, la deuda DEHA esta cerrada al 100%. Posibles futuras fases:

- **F7**: aplicar el mismo patron `bootstrap_session_activity` a Sales (consistencia, no urgencia).
- **F8**: introducir `domain/ports/` formales con `Protocol` para `MessageHistoryStore`, `MetadataStore`, `BrainLoader`. Hoy estan implicitos.
- **F9**: extraer `application/use_cases/` desde `service.py` (`IngestInboundMessage`, etc.).
- **F10**: setup observabilidad (`opentelemetry`, metricas custom de turnos / latencia LLM).

Estas no son obligatorias para cumplir DEHA estricto, pero son el siguiente nivel de madurez arquitectonica.
