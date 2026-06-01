# Plugin: agents_admin

> Behavior contract — bootstrap inicial 2026-06-01.
> Fuente: `hubara_agency/src/plugins/agents_admin/api/routes.py` + `frontend_dashboard/src/plugins/agents_admin/`.

## Purpose

El plugin `agents_admin` provee la **sección Agents del dashboard** que el operador usa para
inspeccionar la configuración y prompts de todos los agentes conversacionales activos en el sistema.
Expone un endpoint `GET /api/agents_admin` que lee los workspace files (IDENTITY.md, SOUL.md,
TOOLS.md, AGENTS.md, USER.md, skills/*.md) de los plugins con `agentic: true` y los devuelve
como JSON. El frontend renderiza la lista de agentes, sus prompts y un inspector de metadata.

## Requirements

### Requirement: workspace-api.SHALL — Listar agentes agénticos

El sistema SHALL exponer `GET /api/agents_admin` que devuelva la lista de todos los workers
declarados en plugins con `agentic: true`, incluyendo el contenido real de sus workspace files.

#### Scenario: AC-1 — Solo plugins agénticos (agentic filter)

- GIVEN uno o más plugins registrados en el manifest
- WHEN `GET /api/agents_admin` con header `X-Internal-Dashboard: 1`
- THEN se devuelve únicamente los workers de plugins donde `agentic: true`
- AND plugins con `agentic: false` (e.g. `catalog`, `orders`, `agents_admin` mismo) no aparecen

#### Scenario: AC-2 — Contenido real de workspace (not just schema)

- GIVEN un plugin agéntico con `IDENTITY.md` que contiene texto real
- WHEN `GET /api/agents_admin` con header `X-Internal-Dashboard: 1`
- THEN `data[i].workspace.identity` contiene el texto del archivo, no cadena vacía
- AND el campo `name` es extraído del heading `# ` del IDENTITY.md
- AND el campo `role` es el primer párrafo de texto (no heading ##/###) tras el heading `# `

#### Scenario: AC-3 — Skills de subdirectorio

- GIVEN un workspace con `skills/hubara_catalog/skill.md` existente
- WHEN `GET /api/agents_admin` con header `X-Internal-Dashboard: 1`
- THEN `data[i].workspace.skills` contiene `{name: "hubara_catalog", content: "..."}` con el contenido real del skill.md

#### Scenario: AC-4 — Fallback para archivos faltantes

- GIVEN un workspace donde algunos archivos opcionales (SOUL.md, TOOLS.md, etc.) no existen
- WHEN `GET /api/agents_admin` con header `X-Internal-Dashboard: 1`
- THEN los campos faltantes retornan cadena vacía `""`
- AND el endpoint devuelve HTTP 200 (no 500)

#### Scenario: AC-5 — agentic:false excluye el plugin

- GIVEN un plugin con campo `agentic: false` en su plugin.yaml
- WHEN `GET /api/agents_admin` con header `X-Internal-Dashboard: 1`
- THEN ese plugin NO aparece en la respuesta aunque declare workers en `agent.workers[]`

### Requirement: workspace-api.SHALL — Autenticación interna mínima

El sistema SHALL requerir el header `X-Internal-Dashboard: 1` en todas las requests a
`GET /api/agents_admin`. Requests sin el header recibirán HTTP 403.

#### Scenario: Header ausente

- GIVEN un request a `GET /api/agents_admin` sin header `X-Internal-Dashboard`
- WHEN el servidor procesa el request
- THEN retorna HTTP 403 Forbidden
- AND el dashboard frontend incluye el header en todas sus requests

### Requirement: workspace-api.SHALL — Resiliencia ante archivos malformados

El sistema SHALL manejar archivos de workspace con bytes inválidos UTF-8 o errores de I/O
devolviendo cadena vacía para ese campo en lugar de HTTP 500.

#### Scenario: Archivo con bytes inválidos UTF-8

- GIVEN un archivo de workspace (e.g. TOOLS.md) con bytes no decodificables en UTF-8
- WHEN `GET /api/agents_admin` con header `X-Internal-Dashboard: 1`
- THEN el campo correspondiente retorna `""`
- AND el error queda registrado en el log estructurado con `plugin_id`, `worker_name`, `file`, `error`
- AND los demás campos del workspace se devuelven normalmente
