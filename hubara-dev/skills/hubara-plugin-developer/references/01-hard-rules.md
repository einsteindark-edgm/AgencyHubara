# 01 · Reglas duras (qué te frena cada gate, antes de editar)

> Subset operativo de `ARCHITECTURE_FINAL_fable.md §3`. La tabla completa
> (todos los P-#) está allá; acá los que más muerden + el modelo mental.

## El invariante: aislamiento de plugins

Un plugin importa de `@/shared` (frontend) / `src.sdk` (backend) y de **sí
mismo**. JAMÁS de otro plugin. La comunicación cross-plugin va por **tres
canales declarativos**, nunca por imports:

- **Canal 1** — push UI al dashboard: `src.sdk.dashboardkit` (in-process SSE).
- **Canal 2** — orquestación cross-worker: `src.sdk.eventkit` (eventos frozen
  + dispatcher + transitions; durable). NUNCA importás la workflow class ajena.
- **Canal 3** — consumir datos de otro plugin: `cast` declarado en tu manifest
  (`depends_on` + `consumes` + un router bajo `/api/<tu-id>/`).

Si te encontrás escribiendo `if plugin ==` en `platform/`, o importando
`src.plugins.Y`, parate: falta una declaración en el manifest.

## Los que más muerden (full en §3)

| Si hacés esto… | Te frena | Fix |
|---|---|---|
| Importar `src.plugins.Y` / `@plugins/Y` desde el plugin X | P-3 / dep-cruiser + P-22 | canal 1/2/3 |
| String `/api/<otro>/` en tu frontend (hasta en comments) | P-9 + P-23 | tu cast bajo `/api/<tu-id>/` |
| Entity en `src/entities/` central | P-11 | `plugins/<id>/frontend/entities/` |
| `platform/` importando un plugin | P-4 + import-linter | invertir: port en platform |
| Import `src.platform.*` nuevo desde un plugin | P-28 (ratchet) | importá `src.sdk` / agregá al SDK (receta §4.7) |
| Worker sin `ensure_plugin_enabled("<id>")` primero | P-21 | agregarlo |
| Worker con `get_task_queue("<otro>", ...)` | P-16 | su propio (plugin, worker) |
| Editar compose generado a mano | drift test + P-20 | `render-compose.py` |
| Campo de manifest sin código que lo consuma | P-2 / regla de oro §4.5 | las 3 patas (campo+código+check) |
| Tocar un path PROTECTED sin label | meta-gates | label `architecture-change` + ADR (ver L-14) |

## La regla de oro (todo lo nuevo lleva 3 patas, en el MISMO PR)

Campo de manifest / símbolo del SDK / check nuevo ⇒ (1) la cosa, (2) el código
que la consume, (3) su check en el TestKit o gate. Sin las 3 patas, es una
mentira en potencia.

---
Fuente canónica: `ARCHITECTURE_FINAL_fable.md §3` + `PLUGIN_CONTRACT.md`. Si
difiere del código vivo, gana el código vivo.
