# Capability Index

Lista de todas las capability specs activas. Generado manualmente; el
comando `hubara-archive-hu` lo actualiza al introducir nuevas capabilities.

## Plugins (bounded contexts)

| Capability | Spec | Backend | Frontend | Status |
|---|---|---|---|---|
| `plugins/orders` | [spec.md](plugins/orders/spec.md) | `hubara_agency/src/plugins/orders/` | `frontend_dashboard/src/plugins/orders/` | ✅ active |
| `plugins/chats` | [spec.md](plugins/chats/spec.md) | `hubara_agency/src/plugins/chats/` | `frontend_dashboard/src/plugins/chats/` | ✅ active |
| `plugins/catalog` | _bootstrap pendiente_ | `hubara_agency/src/plugins/catalog/` | `frontend_dashboard/src/plugins/catalog/` | ⏳ todo |
| `plugins/eta` | _bootstrap pendiente_ | `hubara_agency/src/plugins/eta/` | `frontend_dashboard/src/plugins/eta/` | ⏳ todo |
| `plugins/agents_admin` | _bootstrap pendiente_ | `hubara_agency/src/plugins/agents_admin/` | `frontend_dashboard/src/plugins/agents_admin/` | ⏳ todo |
| `plugins/system_map` | _bootstrap pendiente_ | `hubara_agency/src/plugins/system_map/` | `frontend_dashboard/src/plugins/system_map/` | ⏳ todo |

## Agents / Workers

| Capability | Spec | Code | Status |
|---|---|---|---|
| `agents/sales-worker` | [spec.md](agents/sales-worker/spec.md) | `hubara_agency/src/plugins/chats/workers/sales.py` + `agent/sales/` | ✅ active |
| `agents/remarketing-worker` | _bootstrap pendiente_ | `hubara_agency/src/plugins/chats/workers/remarketing.py` + `agent/remarketing/` | ⏳ todo |

## Cross-cutting

| Capability | Spec | Status |
|---|---|---|
| `messaging` | [spec.md](messaging/spec.md) | ✅ active |
| `observability` | _bootstrap pendiente_ | ⏳ todo |
| `auth` | _N/A — no auth implementada todavía_ | — |

## Convenciones de naming

- `plugins/<plugin_id>` — comportamiento del plugin completo
- `agents/<worker_name>` — comportamiento de un agent worker específico
- Otras capabilities cross-cutting al raíz (`messaging`, `observability`, `auth`)
