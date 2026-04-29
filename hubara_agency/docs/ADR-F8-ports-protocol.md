# ADR F8 — Ports formales con `typing.Protocol` y cierre de NEW-5

- **Date**: 2026-04-28
- **By**: DEHA architect agent (F8)
- **Status**: Accepted

## Context

Tras F7, el codigo ya cumplia parcialmente DIP (R-DIP): `domain/policies/prompts.py` no
importaba infra. Pero quedaban inversiones blandas:

1. **NEW-5**: `src/core/activities.py:execute_tool` hacia `from src.domains.sales_whatsapp.tools.routing import TransferToSalesAgentTool` para registrar la tool en runtime. Inversion explicita `core -> domain`.
2. Los callers (`service.py`, `dispatcher_activities`, las activities de bootstrap) construian directamente `WorkspaceConfig`, `LLMConfig`, `ToolRegistry` y `WhatsAppClient` por sus paths concretos. Sin Protocol, los tests que quisieran fakear estas capabilities tenian que mockear modulos completos (frágil) o instanciar las clases reales (acoplado a infraestructura).
3. `core/brains.py::load_brain` se llamaba como funcion modulo-level desde activities.

## Decision

Definir tres `typing.Protocol` con `@runtime_checkable` en `src/core/ports/`:

- `BrainLoaderPort.load(brain_dir, files) -> list[str]`
- `WhatsAppGatewayPort.send_message(phone_number_id, to, text) -> None`
- `ToolRegistryPort.{build_default_llm_config, build_workspace_config, get_base_tools_registry, get_base_tools_json}`

Tres adapters default (wrappers finos sobre las funciones existentes) en
`src/core/infrastructure/adapters/`:

- `DefaultBrainLoader`
- `DefaultWhatsAppGateway`
- `DefaultToolRegistry`

Cierre de NEW-5 sin reescribir `execute_tool`:

- Nuevo modulo `src/core/tool_extensions.py` con `register_tool_extension(key, factory)` y `apply_tool_extensions(registry, workspace_path)`.
- Cada `worker.py` registra su tool factory en el composition root (linea de boot, antes de `Worker(...)`).
- `execute_tool` consume `apply_tool_extensions(...)` en vez del import inline `from src.domains.sales_whatsapp...`.

## Consequences

**Positivas:**
- Los call-sites pueden depender de `BrainLoaderPort` / `WhatsAppGatewayPort` / `ToolRegistryPort` sin tocar las implementaciones (DIP completo).
- Tests pueden inyectar fakes minimalistas que cumplan el Protocol via duck typing.
- NEW-5 cerrado: `core/activities.py` ya no importa `src.domains.*`. La inversion vive solo en los `worker.py` (composition root legitimo).
- Cero cambios en signatures de activities, workflows, signals, queries, ni shape de history (no requiere regenerar fixtures de replay).

**Trade-offs:**
- `_EXTENSIONS: dict` es modulo-level. Es **registracion inmutable post-boot** (no estado mutable de runtime), aceptable bajo la excepcion documentada en `deha-architecture/anti-patterns.md` para registries.
- Los call-sites actuales (bootstrap activities) siguen llamando las funciones modulo-level de `src.core.registries` directamente. Migrar a inyectar el `ToolRegistryPort` queda como cleanup futuro (F9 candidate). F8 entrega el contrato; el wiring DI completo es siguiente iteracion.

## Estado de NEW-5

**Cerrado**. `src/core/activities.py:execute_tool` ya no importa `src.domains.*`.
La registracion de `TransferToSalesAgentTool` ocurre en:

- `src/domains/sales_whatsapp/worker.py` (linea de boot)
- `src/domains/remarketing_whatsapp/worker.py` (linea de boot, porque Remarketing tambien usa `transfer_to_sales_agent`)

## Validacion

- 3 tests nuevos en `tests/test_ports.py`:
  - `test_default_brain_loader_satisfies_port`
  - `test_default_whatsapp_gateway_satisfies_port`
  - `test_default_tool_registry_satisfies_port`
- 3 tests adicionales de smoke (brain loader vacio, brain loader con archivos, extensions apply).
- Baseline 44 tests -> esperado 50 tests (44 + 3 ports + 3 smoke).
- Sin regresiones en el resto de la suite.
