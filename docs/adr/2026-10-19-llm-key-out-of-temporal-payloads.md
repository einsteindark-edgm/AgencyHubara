# ADR 2026-10-19 — La llave del LLM sale de los payloads de Temporal (`LLMConfig.api_key_env`)

- **Status**: Accepted (pendiente de merge y deploy)
- **Date**: 2026-10-19
- **Context surfacing**: premortem de la Ola 0 de la auditoría de Temporal (PR #444), hallazgo fuera de ese PR
- **Spinal file touched**: `hubara_agency/src/platform/registries.py` (`build_default_llm_config`; la nota de `spinal-files.yaml` pide ADR para cambios al registry base)

## Contexto

Todo lo que cruza el borde workflow↔activity queda en la historia de Temporal
Cloud, y lo ve cualquiera con lectura del namespace (UI, CLI, historias
exportadas para fixtures). `build_default_llm_config` armaba
`LLMConfig(api_key=DEEPSEEK_API_KEY)`. Ese config viaja en el `SessionInput`
que devuelve el bootstrap de cada sesión (ventas V1/V2, remarketing, ETA) y en
cada input de `build_prompt`, `llm_chat` y `record_turn`. Un turno de ventas V2
guardaba la llave en 8 eventos. La única que la usa es `llm_chat`, que corre
en el mismo worker que tiene la variable en su entorno. El plugin `catalog` ya
resolvía así el token de Meta: `PushMetaActivityInput` no lo lleva y la
activity lo lee del entorno.

## Decisión

- **exoclaw** — `LLMConfig.api_key_env: str | None = None` nombra la variable
  de entorno del worker. `llm_chat` usa `api_key` si el payload la trae (inputs
  agendados antes del deploy); si no, lee la variable al llamar, para el
  principal y para el respaldo. Si la variable no está, avisa con
  `llm_api_key_missing` (con el nombre, nunca un valor).
- **hubara** — `build_default_llm_config` lleva `api_key_env=LLM_API_KEY_ENV`
  (`"DEEPSEEK_API_KEY"`, en `config.py`) y ya no lleva `api_key`. El proceso ya
  no guarda la llave en una constante de `config.py`.
- **Guard** — `tests/plugins/test_llm_key_not_in_temporal_history.py` corre un
  turno real de ventas V2 con la sonda del laboratorio, con la llave en el
  entorno antes de importar la app, y revisa su historia completa. También
  revisa el resultado de los bootstraps de ventas, remarketing y ETA. Falla si
  un campo con nombre de secreto (`api_key`, `*_api_key`, `access_token`,
  `*_secret`, `*_password`, `authorization`) trae valor o si aparece el valor de
  la llave. `tests/platform/test_llm_chat_worker_key.py` asierta el
  `Authorization` que sale por el cable. Los dos corren en el paso «Guards del
  proxy LLM» de `architecture-gates.yml`, porque CI no corre `tests/plugins` ni
  `tests/platform` completos.

## Alternativas descartadas

- **Una `llm_chat` de hubara registrada con el mismo nombre**: quedarían dos
  activities `llm_chat`, y un worker que registre la de exoclaw se queda sin
  llave en runtime (clase L-3).
- **Un resolvedor global que cada worker registra al arrancar**: estado de
  módulo en exoclaw (R-STATELESS), y un worker nuevo que lo olvide falla en
  runtime.
- **Un `PayloadCodec` que cifre la historia**: protege todo, pero pide llaves
  del codec y un codec server para la UI. La activity ya puede leer la llave
  del entorno.
- **Una variable fija de exoclaw (`EXOCLAW_LLM_API_KEY`)**: obliga a tocar
  Terraform, SSM, compose y k8s, cuando `DEEPSEEK_API_KEY` ya está en el
  entorno de todos los workers.

## Consecuencias

- **Replay**: es solo payload, así que no lleva `patched`. El replay no compara
  los argumentos de las activities, y el converter ignora campos que no conoce.
  Las fixtures de replay siguen pasando (traen llaves de prueba, `fixture-key`
  y `sk-test`, no la real).
- **Runs vivos al desplegar**: ventas V2 vuelve a hacer bootstrap en cada run (su
  continue-as-new solo lleva `SalesSessionInput`). Desde su run siguiente, un run
  vivo deja de mandar la llave; hasta entonces manda la de su bootstrap, que ya
  estaba guardada.
- **Deploy escalonado**: si un worker viejo recibe un input nuevo (sin llave),
  el principal sale igual (litellm lee `DEEPSEEK_API_KEY` por su cuenta). El
  respaldo usa la `OPENAI_API_KEY` que el provider de exoclaw deja en el entorno
  del proceso con su primera llamada con llave; si el proceso todavía no la
  tiene, falla y Temporal reintenta el intento. En la caja, un solo
  `docker compose up -d` recrea todos los workers: la ventana dura lo que tarda
  en recrearlos.
- **Historias viejas**: conservan la llave hasta que venza la retención del
  namespace. Rotarla lo decide el operador. El código nuevo funciona igual con o
  sin rotación, porque lee la variable.
- **Worker sin `DEEPSEEK_API_KEY`**: antes caía al default `mispolainas` de
  `config.py`; ahora avisa con `llm_api_key_missing` y el respaldo falla (no hay
  ninguna llave en el proceso). Todos
  los entornos la traen: la caja (SSM → `box.env`), el compose local (`.env`) y
  el laboratorio (`DEEPSEEK_API_KEY_LAB`, que su guard exige).
