# Tests - hubara_agency

Suite de tests del refactor DEHA (Fases 3-5). El criterio de merge esta definido
en ADR-005 (`docs/adr/`): la suite tiene que pasar antes de mergear.

## Comando estandar

Desde la raiz del workspace (`AgencyHubara/`) o desde `hubara_agency/`:

```bash
cd hubara_agency
uv sync --dev
uv run pytest tests/ -v
```

`uv` resuelve dependencias contra el workspace declarado en
`AgencyHubara/pyproject.toml`. La virtualenv vive en `AgencyHubara/.venv`.

## Variables de entorno

La suite **no** necesita ningun secreto para correr en local porque todos los
tests con I/O externo estan mockeados (monkeypatch + `WorkflowEnvironment`).
Aun asi, algunos modulos productivos importados por los tests
(`src/core/temporal_client.py`, `src/core/config.py`) leen estas vars al
importarse y caen a defaults benignos:

| Variable                | Default               | Notas                                  |
| ----------------------- | --------------------- | -------------------------------------- |
| `TEMPORAL_URL`          | `localhost:7233`      | Solo se usa cuando se llama al cluster |
| `TEMPORAL_NAMESPACE`    | `default`             |                                        |
| `TEMPORAL_TLS_CERT_PATH`| `""`                  | mTLS opcional (Temporal Cloud)         |
| `TEMPORAL_TLS_KEY_PATH` | `""`                  |                                        |
| `DEEPSEEK_API_KEY`      | `mispolainas`         | Mock-safe                              |
| `API_BASE_LLMLITE`      | `http://localhost:4000` |                                      |
| `WORKSPACE_VAULT_DIR`   | `./hubara_vault`      | Tests usan `tmp_path`                  |

**Nota**: si tu `.env` exporta `TEMPORAL_TLS_CERT_PATH`/`TEMPORAL_TLS_KEY_PATH`
hacia rutas que no existen, vas a ver un warning pero no falla la suite.

## Correr un test individual

```bash
uv run pytest tests/test_transfer_tool.py -v
uv run pytest tests/test_dispatcher_activities.py::test_schedule_remarketing_uses_start_delay -v
```

Para diagnostico extra:

```bash
uv run pytest tests/ -vv -x --tb=long      # detener al primer fallo
uv run pytest tests/ -k "transfer or tag"  # filtro por nombre
```

## Estructura

```
tests/
  __init__.py
  conftest.py                       # fixture `temporal_env` (start_time_skipping)
  test_smoke.py                     # smoke: WorkflowEnvironment arranca
  test_imports.py                   # imports de modulos del refactor F3
  test_parsers.py                   # parser de WhatsApp Cloud (puro)
  test_prompts.py                   # policies de prompts (puros)
  test_remarketing_contract.py      # DTO + signature del workflow remarketing
  test_run_agent_turn.py            # helper compartido `run_agent_turn`
  test_dispatcher_activities.py     # ADR-001: activities-dispatcher
  test_transfer_tool.py             # ADR-001: tools devuelven decision payload
```

## Smoke check de boot (workers + API)

Ademas de la suite, la fase F6.6 valida que los modulos productivos cargan en
boot. Estos comandos deben retornar exit 0:

```bash
uv run python -c "import src.main"
uv run python -c "import src.plugins.chats.workers.sales"
uv run python -c "import src.plugins.chats.workers.remarketing"
```

> Tras el cierre de B-1 el entrypoint canonico de cada worker es la corutina
> `main` (invocada bajo `if __name__ == "__main__":`). El check `import <worker>`
> y `from <worker> import main` son equivalentes: validan que el modulo cargue
> sin romper imports.

Para arrancar los workers de verdad (no solo importarlos):

```bash
uv run python -m src.plugins.chats.workers.sales
uv run python -m src.plugins.chats.workers.remarketing
```

Estos requieren un cluster Temporal accesible en `TEMPORAL_URL` y NO se corren
como parte de la suite de tests.

## Tests skippeados / marks de integracion

A la fecha de F6.6 ningun test esta `skip` ni `xfail`. La suite entera corre
offline. Si en el futuro se introduce un test que dependa de un servicio real
(Temporal Cloud, LiteLLM, WhatsApp API), marcalo con
`@pytest.mark.integration` y excluilo del run por defecto via `-m "not integration"`.
