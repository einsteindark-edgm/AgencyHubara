# Task F01 — Simplificar UI de ChatsInspector: eliminar 4 bloques JSX y crear test RTL

- Slug: simplificar-ui-chats-inspector
- HU id: HU-20260515-001219-simplificar-panel-derecho-de-chats-elimi
- Target frontend: frontend_dashboard
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections §1, §4, §13, §14)
- Planner: frontend-task-planner-archon
- Date: 2026-05-14
- Iteration: 1
- Estimated LOC: 70 (test file new; production is -20 deletions)
- Risk: low

---

## 1. Context

Delivers acceptance criteria (verbatim from refinement §1):

- **AC-1:** Given que el operador selecciona un chat, When visualiza el panel derecho (tab "Estado actual"), Then el `form-row` con label "Mensajes" no aparece.
- **AC-2:** Given que el operador selecciona un chat, When visualiza el panel derecho, Then el `form-row` con label "Sentimiento" no aparece.
- **AC-3:** Given que el operador inspecciona las opciones del panel derecho, When está en cualquier tab, Then no existe ningún botón con texto "Cambiar tag".
- **AC-4:** Given que el operador inspecciona las acciones del panel derecho, When está en la tab "Estado actual", Then no existe ningún botón con texto "Cerrar".
- **AC-5:** Given que los cuatro elementos fueron eliminados, When el operador ve el panel derecho con cualquier chat, Then el layout restante no tiene gaps vacíos ni elementos desalineados (flexbox + `gap` absorbe el espacio libre).

Refinement sections that informed this task: §1 (ACs), §4 (bloques a eliminar con números de línea), §5 (sin shared primitives), §8 (sin tokens), §11 (hard rules), §12 (riesgos), §13 (test spec), §14 (orden de implementación).

---

## 2. Dependencies

- depends_on: []
- blocks: []
- Inherits from upstream tasks: ninguna (tarea foundation)
- Backend dependency: none

---

## 3. Files affected

| Path | Action | Role | LOC budget |
|------|--------|------|-----------|
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.tsx` | modify | Eliminar 4 bloques JSX en función `TagsTab` | -20 (eliminaciones) |
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx` | new | Test RTL: ausencia de 4 elementos + presencia de "Reasignar" | ~70 |

`features/chats-inspector/index.ts` — **no change** (barrel ya exporta `ChatsInspector`; no necesita edición).

> **Nota spinal:** `ChatsInspector.tsx` es acción "modify" sobre archivo NO declarado en
> `spinal-files.yaml`. Con una única tarea en este plan no existe riesgo de colisión. Si una
> HU futura modifica este archivo en un lote paralelo, debe ir en un batch posterior o el
> archivo debe declararse spinal.

---

## 4. Entity layer snippets (R-Zod boundary)

**Not applicable.** Esta tarea no introduce ni modifica entidades, schemas Zod ni query hooks.
Hooks existentes consumidos por `ChatsInspector` (`useChatInbox`, `useChatMemory`,
`useChatRoutingLog`) permanecen sin cambio.

---

## 5. Feature layer snippets

### 5a. Bloques a eliminar en `ChatsInspector.tsx` — función `TagsTab`

Los números de línea son los del archivo actual (al momento del refinamiento, §4):

```tsx
// canonical — ELIMINAR: Campo Mensajes (líneas 109-112 actuales en ChatsInspector.tsx)
<div className="form-row">
  <span className="lbl">Mensajes</span>
  {/* valor del campo */}
</div>
```

```tsx
// canonical — ELIMINAR: Campo Sentimiento (líneas 113-118 actuales en ChatsInspector.tsx)
<div className="form-row">
  <span className="lbl">Sentimiento</span>
  {/* valor del campo */}
</div>
```

```tsx
// canonical — ELIMINAR: Botón Cambiar tag (líneas 121-124 actuales en ChatsInspector.tsx)
// Dentro de <div className="quick-row">
<button className="insp-button"><Icon.tag />Cambiar tag</button>
```

```tsx
// canonical — ELIMINAR: Botón Cerrar (líneas 129-132 actuales en ChatsInspector.tsx)
// Dentro de <div className="quick-row">
<button className="insp-button danger"><Icon.archive />Cerrar</button>
```

### 5b. Estado esperado de `quick-row` tras las eliminaciones

```tsx
// canonical — estado resultante: solo queda el botón Reasignar
// .quick-row usa display:flex; flex-wrap:wrap; gap:5px (index.css:824)
// Un único hijo ocupa su ancho natural sin gaps artificiales (AC-5).
<div className="quick-row">
  <button className="insp-button">{/* Reasignar — líneas 125-128 sin cambio */}</button>
</div>
```

### 5c. Importaciones a verificar tras las eliminaciones

- `Icon.archive` — queda sin uso en `TagsTab` después de eliminar el botón Cerrar.
  `Icon` se importa como namespace: `import { Icon, Panel } from "@/shared/ui"`.
  TypeScript **no emitirá error** (el namespace sigue en uso vía `Icon.tag` en la tab button,
  línea ~38). El implementer debe confirmar que no queda ninguna otra referencia a
  `Icon.archive` en el archivo; si la única era el botón Cerrar, el namespace no requiere
  cambio.
- `Icon.tag` — **sigue en uso** (tab button ~línea 38). No remover.

---

## 6. Page mount (composition wiring)

**Not applicable.** `ChatsInspector` ya está montado en `Dashboard.tsx:168` sin cambio:

```tsx
// Dashboard.tsx:168 — sin modificación requerida
{showInspector && <ChatsInspector chatId={selectedChatId} />}
```

---

## 7. Tailwind tokens (if any)

**None.** No se añaden ni modifican tokens. Las clases CSS existentes (`.form-row`,
`.quick-row`, `.insp-button`, `.insp-button.danger`, `display:flex`, `gap:5px`) ya están
declaradas en `src/index.css:824` y siguen presentes para otros usos; no se eliminan.

---

## 8. Entity / feature barrel updates

**No existing barrel edits.** `features/chats-inspector/index.ts` ya exporta `ChatsInspector`
y no requiere modificación.

---

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx` | new | Ausencia de "Mensajes", "Sentimiento", "Cambiar tag", "Cerrar"; presencia de "Reasignar" |

Test name list (el implementer escribe los cuerpos):

- `ChatsInspector — panel derecho simplificado :: no muestra el campo "Mensajes" en tab Estado actual`
- `ChatsInspector — panel derecho simplificado :: no muestra el campo "Sentimiento" en tab Estado actual`
- `ChatsInspector — panel derecho simplificado :: no muestra el botón "Cambiar tag" en ningún tab`
- `ChatsInspector — panel derecho simplificado :: no muestra el botón "Cerrar" en tab Estado actual`
- `ChatsInspector — panel derecho simplificado :: sí muestra el botón "Reasignar" en tab Estado actual`

**Approach guidance for implementer:**

El componente usa `useChatInbox`, `useChatMemory` y `useChatRoutingLog` internamente.
El test debe:
1. Envolver el componente en `QueryClientProvider` (patrón existente en la suite).
2. Interceptar las llamadas de red — usar `msw` si está configurado en `vitest.config.ts`/`test/setup.ts`, o mockear los hooks directamente con `vi.mock`.
3. Si el panel necesita un `chatId` non-null para renderizar `TagsTab`, pasar un valor
   sintético (p.ej. `chatId="test-chat-id"`).
4. Verificar que el tab "Estado actual" esté activo por defecto o hacer click en él antes de
   los asserts (ver §13 — open question sobre tab inicial).

---

## 10. Verification commands

Todos deben salir con exit code 0.

```bash
# 1. Test del feature modificado
cd frontend_dashboard && npm test -- features/chats-inspector

# 2. Type-check incremental (sin errores nuevos)
cd frontend_dashboard && npx tsc -b

# 3. Build de producción (catch de issues que escapan a tsc -b)
cd frontend_dashboard && npm run build

# 4. Suite completa (sin regresiones en otros features)
cd frontend_dashboard && npm test
```

FSD compliance greps (deben retornar vacío o "no rogue ..."):

```bash
cd frontend_dashboard

# Sin fetch() rogue en features/pages/app
grep -rEn "fetch\(" src/features src/pages src/app | grep -v "// allowed:" || echo "no rogue fetch"

# Sin deep imports de features
grep -rEn "from ['\"]@/features/[^'\"]+/(ui|model)/" src/features || echo "no deep imports"

# Sin cross-feature imports
grep -rEn "from ['\"]@/features/" src/features \
  | grep -vE "^src/features/([a-z-]+)/[^:]+:.*from ['\"]@/features/\1" || echo "no cross-feature"
```

---

## 11. Definition of Done

- [ ] Los 4 bloques JSX eliminados de `TagsTab` en `ChatsInspector.tsx` (Mensajes, Sentimiento, Cambiar tag, Cerrar).
- [ ] No quedan referencias huérfanas a `Icon.archive` en `ChatsInspector.tsx` (verificar con `grep -n "Icon.archive" frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.tsx`).
- [ ] El contenedor `.quick-row` contiene exactamente 1 botón ("Reasignar") después de las eliminaciones.
- [ ] `ChatsInspector.test.tsx` creado con los 5 tests listados en §9.
- [ ] `cd frontend_dashboard && npm test -- features/chats-inspector` → exit 0, los 5 tests pasan.
- [ ] `cd frontend_dashboard && npx tsc -b` → exit 0, sin errores nuevos.
- [ ] `cd frontend_dashboard && npm run build` → exit 0.
- [ ] `cd frontend_dashboard && npm test` → exit 0 (sin regresiones).
- [ ] FSD compliance greps de §10 retornan vacío para los archivos modificados.
- [ ] `index.ts` de `features/chats-inspector/` **no fue tocado**.
- [ ] `Dashboard.tsx` **no fue tocado**.
- [ ] `src/index.css` **no fue tocado**.

---

## 12. FSD rules check

| Regla | Aplica | Cómo esta tarea cumple |
|-------|--------|------------------------|
| Import rules (layering) | not applicable | Solo se eliminan líneas JSX; no se añaden imports ni referencias cross-layer. |
| Barrel-only public API | not applicable | No se crean features/entities nuevas; el barrel existente no cambia. |
| Zod at HTTP boundary | not applicable | No hay fetch ni parsing nuevo. |
| TanStack Query for server data | not applicable | No hay server state nuevo. |
| No cross-feature imports | not applicable | La edición es interna a `chats-inspector`; ningún consumidor cambia. |
| No deep imports | not applicable | Ningún consumidor cambia. |
| No fetch() in components/pages | not applicable | No se añade fetch. |
| Tailwind token naming | not applicable | No se añaden tokens. |
| JSX files use .tsx | applies — handled | `ChatsInspector.tsx` y `ChatsInspector.test.tsx` usan extensión `.tsx` correctamente. |

---

## 13. Open questions / risks

- **Tab inicial activo:** No queda claro si `TagsTab` es el tab renderizado por defecto al
  abrir `ChatsInspector`. Si el tab activo por defecto es diferente, el test RTL deberá hacer
  click en la tab "Estado actual" antes de los asserts. Recommended default: leer el estado
  inicial del hook de tab (o el `defaultValue` del componente) en el archivo actual y
  ajustar el test.

- **Mocking de API hooks en tests:** `ChatsInspector` consume `useChatInbox`, `useChatMemory`
  y `useChatRoutingLog`. Verificar si `vitest.config.ts` o `src/test/setup.ts` configura MSW
  handlers globales para las rutas `/api/dashboard/inbox`, etc. Si no, el implementer debe
  añadir `vi.mock("@/entities/...")` o usar `msw` con handlers de test.

- **Layout visual (AC-5):** El flexbox del `.quick-row` absorbe el espacio libre
  automáticamente; sin `justify-content: space-between` ni width fija, un solo botón
  se renderiza sin gap. Verificar visualmente en el dev server tras aplicar el cambio.
  No se requiere acción CSS adicional según refinamiento §12.

- **`Icon.archive` namespace:** TypeScript no reportará error por el namespace no usado.
  Si el equipo tiene una regla ESLint de `no-unused-vars` extendida a namespaces, correr
  `cd frontend_dashboard && npm run lint` para confirmar.
