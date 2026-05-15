# Tech refinement (frontend) — Simplificar panel derecho de chats eliminando campos y acciones no usados

**HU id:** HU-20260515-001219-simplificar-panel-derecho-de-chats-elimi
**Source:** $ARTIFACTS_DIR/hu-original.md
**Target frontend:** frontend_dashboard (cwd: /Users/edgm/Documents/Projects/AgencyHubara/frontend_dashboard)
**Layout status:** FSD in place
**Refiner:** frontend-tech-refiner-archon
**Date:** 2026-05-14
**Iteration:** 1
**requires_backend_change:** false

---

## 1. Scope

**Summary:** Eliminar del panel derecho de Chats cuatro elementos de presentación — campo "Mensajes", campo "Sentimiento", botón "Cambiar tag" y botón "Cerrar" — sin tocar lógica de negocio ni endpoints.

**Acceptance criteria:**

- Given que el operador selecciona un chat, When visualiza el panel derecho (tab "Estado actual"), Then el `form-row` con label "Mensajes" no aparece.
- Given que el operador selecciona un chat, When visualiza el panel derecho, Then el `form-row` con label "Sentimiento" no aparece.
- Given que el operador inspecciona las opciones del panel derecho, When está en cualquier tab, Then no existe ningún botón con texto "Cambiar tag".
- Given que el operador inspecciona las acciones del panel derecho, When está en la tab "Estado actual", Then no existe ningún botón con texto "Cerrar".
- Given que los cuatro elementos fueron eliminados, When el operador ve el panel derecho con cualquier chat, Then el layout restante no tiene gaps vacíos ni elementos desalineados (flexbox + `gap` absorbe el espacio libre).

**Out of scope:**

- Modificar backend, API, Zod schemas o query hooks.
- Modificar el panel izquierdo (lista de chats) o la zona central (mensajes).
- Agregar nuevos campos o acciones al panel derecho.
- Ocultar condicionalmente por rol o feature flag.
- Tocar cualquier campo/acción fuera del `TagsTab` dentro de `ChatsInspector.tsx`.

---

## 2. Page(s) affected

**Decision:** no page change

**Justification:** La eliminación es interna a la feature `chats-inspector`. `Dashboard.tsx` compone `<ChatsInspector>` pero no pasa props que representen los elementos a eliminar; el cambio no requiere levantar estado ni modificar el punto de composición.

**Cross-feature state added/lifted:** none

---

## 3. Entities affected/created

**None.** La HU es pura presentación — no añade, extiende ni elimina tipos, schemas, query keys ni hooks de entidades.

---

## 4. Features affected/created

### `features/chats-inspector/` — extended (modificación mínima)

| File | Status | Change |
|------|--------|--------|
| `ui/ChatsInspector.tsx` | edit | Eliminar 4 bloques JSX en la función `TagsTab` (ver §14) |
| `index.ts` | no change | Barrel ya exporta `ChatsInspector`; no cambia |

**Detalle de los bloques a eliminar en `TagsTab` (función interna, líneas referencia al archivo actual):**

| Elemento | Líneas a eliminar | Descripción JSX |
|----------|-------------------|-----------------|
| Campo Mensajes | 109–112 | `<div className="form-row"><span className="lbl">Mensajes</span>…</div>` |
| Campo Sentimiento | 113–118 | `<div className="form-row"><span className="lbl">Sentimiento</span>…</div>` |
| Botón Cambiar tag | 121–124 | `<button className="insp-button"><Icon.tag />Cambiar tag</button>` |
| Botón Cerrar | 129–132 | `<button className="insp-button danger"><Icon.archive />Cerrar</button>` |

**Post-eliminación — estado esperado del `quick-row`:**
Solo queda el botón "Reasignar" (líneas 125–128). El contenedor `.quick-row` usa `display: flex; flex-wrap: wrap; gap: 5px` (`index.css:824`) — un solo botón ocupa su ancho natural sin gaps.

**Importaciones huérfanas:**
- `Icon.archive` queda sin uso tras eliminar el botón Cerrar. Como `Icon` se importa como namespace (`import { Icon, Panel } from "@/shared/ui"`), no hay un specifier individual que remover. TypeScript no emitirá error porque `Icon` sigue usándose. Sin embargo, para código limpio se puede hacer notar en §14.
- `Icon.tag` sigue en uso (tab button, línea 38) — **no huérfano**.

**Props shape:** Sin cambio. `ChatsInspector` ya recibe `{ chatId: string | null }`.

**Entity hooks consumidos:** sin cambio — `useChatInbox`, `useChatMemory`, `useChatRoutingLog` permanecen.

---

## 5. Shared primitives

No se añaden ni modifican primitivas compartidas. Los estilos `.form-row`, `.quick-row`, `.insp-button` y `.insp-button.danger` ya existen en `src/index.css` y siguen presentes para otros usos.

---

## 6. Backend contract dependencies

**Ninguna.** La HU es puramente presentacional. No se agrega ni modifica ningún endpoint, shape de respuesta ni schema Zod.

**Blocked work items:** none — no blocking dependencies.

---

## 7. Cross-feature state

No se añade ni modifica estado cross-feature.

---

## 8. Tailwind token deltas

No se añaden tokens. No se modifican tokens existentes.

---

## 9. App-layer wiring

No app-layer change. `AppProviders`, `main.tsx` y `Dashboard.tsx` no se tocan.

---

## 10. Composition wiring

No hay features nuevas que montar. `ChatsInspector` ya está montado en `Dashboard.tsx:168`:

```tsx
// Dashboard.tsx:168 — sin cambio
{showInspector && <ChatsInspector chatId={selectedChatId} />}
```

---

## 11. Hard rules check

| Regla | Aplica | Cómo se cumple |
|-------|--------|----------------|
| Import rules (layering) | no applicable | Solo se eliminan líneas JSX dentro de un archivo de feature; ningún import cruzado nuevo. |
| Barrel-only public API | not applicable | No se crean features/entities nuevas. |
| Zod at HTTP boundary | not applicable | No hay fetch nuevo. |
| TanStack Query for server data | not applicable | No hay server state nuevo. |
| No cross-feature imports | not applicable | La edición es interna a `chats-inspector`. |
| No deep imports | not applicable | Ningún consumidor cambia. |
| No fetch() in components/pages | not applicable | No se añade fetch. |
| Tailwind token naming | not applicable | No se añaden tokens. |
| JSX files use .tsx | applies — handled | El archivo editado es `ChatsInspector.tsx`; extensión correcta. |

---

## 12. Risks / open questions

- **Botón "Reasignar" queda solo en `quick-row`:** el layout flex absorbe el cambio sin problema CSS (`.quick-row` no tiene `justify-content: space-between` ni conteo fijo de hijos). Verificar visualmente tras aplicar el cambio. Recommended default: ninguna acción CSS adicional.
- **Backend dependency:** none.
- **Defer to follow-up design doc:** none.
- **Pre-existing FSD violation en código tocado:** `TagsTab`, `AgentTab`, `MemoryTab` son funciones de módulo privadas (no exportadas) definidas dentro del mismo archivo que `ChatsInspector`. Este patrón no viola FSD (son sub-componentes internos de un feature), pero si crecen mucho deberían moverse a archivos `ui/TagsTab.tsx`, etc. No es parte de esta HU.

---

## 13. Tests

| Test file | Tipo | Asserts esperados |
|-----------|------|-------------------|
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx` | RTL (nuevo) | Dado un `chatId`, renderiza `<ChatsInspector>`; la tab "Estado actual" NO contiene el texto "Mensajes", NO contiene "Sentimiento", NO contiene "Cambiar tag", NO contiene "Cerrar"; SÍ contiene "Reasignar". |

El componente no tiene test actualmente (no existe archivo `*.test.*` en `features/chats-inspector/`). La HU justifica crear uno mínimo para los criterios de aceptación. El test es RTL con `@testing-library/react` y un `QueryClientProvider` wrapper (patrón existente en la suite).

---

## 14. Implementation order (suggested)

1. **Editar `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.tsx`:**
   - En la función `TagsTab`, eliminar el bloque `<div className="form-row">` de "Mensajes" (líneas 109–112).
   - Eliminar el bloque `<div className="form-row">` de "Sentimiento" (líneas 113–118).
   - Dentro del `<div className="quick-row">`, eliminar el `<button>` "Cambiar tag" (líneas 121–124).
   - Dentro del mismo `<div className="quick-row">`, eliminar el `<button>` "Cerrar" (líneas 129–132).
   - Verificar que no queden referencias a `Icon.archive` (si queda alguna, es el Cerrar button — asegurarse de haberlo eliminado).

2. **Verificar layout:** compilar con `cd frontend_dashboard && npx tsc -b` — debe pasar sin errores.

3. **Crear `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx`** con RTL mínimo que valide los 4 criterios de ausencia + 1 de presencia ("Reasignar").

4. **Ejecutar suite:** `cd frontend_dashboard && npm test -- features/chats-inspector` — todos los tests deben pasar.

5. **FSD compliance grep:** confirmar que los greps del project-context retornan vacío para el archivo editado.

6. **Build final:** `cd frontend_dashboard && npm run build` — sin errores ni advertencias nuevas.
