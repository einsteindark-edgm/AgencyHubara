# Tech refinement (frontend) — Eliminar controles de edición del panel de detalle de agente en Chats

HU id: HU-20260515-021114-eliminar-controles-de-edicion-del-panel
Source: $ARTIFACTS_DIR/hu-original.md
Target frontend: frontend_dashboard (cwd: /Users/edgm/Documents/Projects/AgencyHubara/frontend_dashboard)
Layout status: FSD in place
Refiner: frontend-tech-refiner-archon
Date: 2026-05-14
Iteration: 1
requires_backend_change: false

---

## 1. Scope

**Summary:** Eliminar del tab "Agente" del `ChatsInspector` los cuatro botones de acción (Prompt, Flujo, Probar, Clonar) y los dos campos de configuración (Temperatura, Tokens), dejando solo datos de solo lectura.

**Acceptance criteria:**

- Given que estoy en la sección Chats con un agente seleccionado, when navego al tab "Agente actual" del inspector derecho, then no aparecen los botones Prompt, Flujo, Probar ni Clonar.
- Given que estoy en el tab "Agente actual" del inspector de Chats, when inspecciono los campos mostrados, then no aparecen los labels "Temperatura" ni "Tokens".
- Given que se eliminan esos controles, when el `AgentTab` se renderiza, then el `Panel` muestra el avatar/nombre del agente, el status "Active routing handler", la Plataforma y el Modelo sin elementos vacíos ni padding sobrante.
- Given que el panel de Chats carece de botones de acción, when el usuario quiere editar prompt o temperatura, then no hay ruta de edición desde ese panel (la edición ocurre en la sección Agentes).

**Out of scope:**

- `AgentsInspector` (`features/agents-inspector`) — sin cambios.
- `AgentsPrompts` (`features/agents-prompts`) — sin cambios.
- Agregar enlace "Ir a configurar" o cualquier ruta de navegación nueva.
- Cambiar el layout del inspector más allá de la eliminación indicada.
- Modificar entidades, endpoints o datos del backend.
- `features/session-metadata` — feature legacy no montada en Dashboard, sin cambios.

---

## 2. Page(s) affected

**Decision:** no page change

**Justification:** El cambio es interno a `features/chats-inspector`. La página `Dashboard.tsx` ya monta `<ChatsInspector chatId={selectedChatId} />` (línea 168) sin modificar nada. No se añade ni se levanta estado cross-feature.

**Cross-feature state added/lifted:** none

---

## 3. Entities affected/created

Ninguna entidad se modifica. El `AgentTab` dentro de `ChatsInspector` usa datos hardcodeados (mock) — no hay hooks de entidad que cambiar.

---

## 4. Features affected/created

### `features/chats-inspector/` — extended (deletions only)

| File | Status | Change |
|------|--------|--------|
| `ui/ChatsInspector.tsx` | edit | En la función interna `AgentTab()`: eliminar el `<div className="form-row">` de Temperatura (líneas ~185-188), el `<div className="form-row">` de Tokens (líneas ~189-192) y el bloque `<div className="btn-grid">` con los 4 botones Prompt/Flujo/Probar/Clonar (líneas ~193-213). |
| `ui/ChatsInspector.test.tsx` | edit | Añadir 6 nuevas assertions en el `describe` existente para confirmar ausencia de los 6 elementos eliminados (ver §13). |
| `index.ts` | no change | El barrel solo reexporta `ChatsInspector`; sin cambios. |

**Props shape:** sin cambio — `ChatsInspector` recibe `{ chatId: string | null }` y eso no varía.

**Entity hooks consumed:** sin cambio — `AgentTab` no consume hooks de entidad.

**Detalle de los elementos a eliminar** (para el implementador, referencias a líneas actuales de `ChatsInspector.tsx`):

```
// Eliminar: Temperatura form-row (aprox. líneas 185-188)
<div className="form-row">
  <span className="lbl">Temperatura</span>
  <span className="val">0.4</span>
</div>

// Eliminar: Tokens form-row (aprox. líneas 189-192)
<div className="form-row">
  <span className="lbl">Tokens</span>
  <span className="val">12,840 / 200k</span>
</div>

// Eliminar: btn-grid completo (aprox. líneas 193-213)
<div className="btn-grid" style={{ marginTop: 10 }}>
  <button className="insp-button"><Icon.edit />Prompt</button>
  <button className="insp-button"><Icon.workflow />Flujo</button>
  <button className="insp-button"><Icon.bolt />Probar</button>
  <button className="insp-button"><Icon.copy />Clonar</button>
</div>
```

**Post-eliminación el `AgentTab` renderiza solo:**
1. Avatar + nombre del agente + status "Active routing handler"
2. `form-row` Plataforma: WhatsApp API
3. `form-row` Modelo: claude-haiku-4-5

No quedan contenedores vacíos. El `Panel` colapsa naturalmente al contenido restante.

**Nota sobre imports:** `Icon` es importado como namespace (`import { Icon, Panel } from "@/shared/ui"`). `Icon.workflow` y `Icon.bolt` quedan sin uso después de la eliminación, pero al ser propiedades de acceso sobre el namespace `Icon` (no destructured imports), TypeScript no emite error por referencias no usadas. Aun así, es buena práctica verificar con `npm run lint`.

---

## 5. Shared primitives

No se necesitan nuevas primitivas compartidas. Los componentes `Icon`, `Panel` ya existen en `shared/ui`.

---

## 6. Backend contract dependencies

| Endpoint | Status | Cited backend file | Frontend Zod schema |
|----------|--------|-------------------|---------------------|
| — | n/a | n/a | n/a |

**Blocked work items:** none — no blocking dependencies.

**Behavior verification (Step 1.5):** No aplica. Esta HU no introduce visualización de nuevos datos; elimina controles de UI existentes. Step 1.5 se saltea.

---

## 7. Cross-feature state

No se añade estado cross-feature. Sin cambios.

---

## 8. Tailwind token deltas

No se añaden tokens. Sin cambios.

---

## 9. App-layer wiring

**Provider added:** none
**main.tsx change:** no

Sin cambios en la capa app.

---

## 10. Composition wiring

Sin cambios en la composición de la página. `<ChatsInspector chatId={selectedChatId} />` ya está montado en `Dashboard.tsx:168`.

---

## 11. Hard rules check

| Regla | Estado |
|-------|--------|
| Import rules (layering) | no applicable — la HU no añade imports nuevos; solo elimina JSX dentro de una función interna. |
| Barrel-only public API | no applicable — no se crea ninguna feature/entity nueva. |
| Zod at HTTP boundary | no applicable — no hay fetch nuevo; `AgentTab` usa datos hardcodeados. |
| TanStack Query for server data | no applicable — sin nuevo server state. |
| No cross-feature imports | applies — `ChatsInspector` no importa de ninguna otra feature; sin cambio. |
| No deep imports | applies — los imports existentes van por barrels (`@/entities/chat`, `@/shared/ui`); sin cambio. |
| No fetch() in components/pages | not applicable — sin fetch nuevo. |
| Tailwind token naming | not applicable — sin tokens nuevos. |
| JSX files use .tsx | applies — el archivo editado es `.tsx`; sin cambio. |

---

## 12. Risks / open questions

1. **`Icon.workflow` / `Icon.bolt` sin uso tras eliminar los botones.** El linter puede (o no) emitir warning dependiendo de la configuración de `@typescript-eslint/no-unused-vars` sobre propiedades de namespace. Recomendación: verificar con `npm run lint` post-edición; si hay warning, nada impide que el build pase — los iconos son propiedades de un objeto, no variables sueltas.

2. **Datos hardcodeados en `AgentTab`.** El nombre del agente ("remarketing"), el modelo ("claude-haiku-4-5"), y la plataforma ("WhatsApp API") son valores mock fijos en el componente. La HU no pide conectar estos datos a la entidad real. Este es un item de deuda técnica preexistente — fuera del scope de esta HU.

3. **`features/session-metadata` no montada.** Existe el directorio pero `Dashboard.tsx` no lo importa ni lo usa. Esta HU no lo toca. Cleanup separado si aplica.

4. **Backend dependency:** none.

5. **Defer to follow-up design doc:** none.

6. **Pre-existing FSD violation in touched code:** ninguna en `chats-inspector`. `session-metadata` es legacy/unused pero no se toca.

---

## 13. Tests

El test suite existente en `ChatsInspector.test.tsx` cubre el tab `tag` (tab por defecto). Para cubrir el tab `agent` hay que simular el click en el botón de tab de agente antes de las assertions.

| Test file | Type | Asserts |
|-----------|------|---------|
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx` | RTL | **Añadir** 6 nuevos `it` dentro del `describe` existente (ver pseudo-código abajo) |

**Pseudo-código de las nuevas assertions:**

```tsx
// pseudo — añadir al describe existente

import userEvent from '@testing-library/user-event'; // si no está ya importado

it('no muestra el campo "Temperatura" en tab Agente', async () => {
  render(<ChatsInspector chatId="test-chat-id" />);
  await userEvent.click(screen.getByTitle('Agente actual'));
  expect(screen.queryByText('Temperatura')).not.toBeInTheDocument();
});

it('no muestra el campo "Tokens" en tab Agente', async () => {
  render(<ChatsInspector chatId="test-chat-id" />);
  await userEvent.click(screen.getByTitle('Agente actual'));
  expect(screen.queryByText('Tokens')).not.toBeInTheDocument();
});

it('no muestra el botón "Prompt" en tab Agente', async () => {
  render(<ChatsInspector chatId="test-chat-id" />);
  await userEvent.click(screen.getByTitle('Agente actual'));
  expect(screen.queryByText('Prompt')).not.toBeInTheDocument();
});

it('no muestra el botón "Flujo" en tab Agente', async () => {
  render(<ChatsInspector chatId="test-chat-id" />);
  await userEvent.click(screen.getByTitle('Agente actual'));
  expect(screen.queryByText('Flujo')).not.toBeInTheDocument();
});

it('no muestra el botón "Probar" en tab Agente', async () => {
  render(<ChatsInspector chatId="test-chat-id" />);
  await userEvent.click(screen.getByTitle('Agente actual'));
  expect(screen.queryByText('Probar')).not.toBeInTheDocument();
});

it('no muestra el botón "Clonar" en tab Agente', async () => {
  render(<ChatsInspector chatId="test-chat-id" />);
  await userEvent.click(screen.getByTitle('Agente actual'));
  expect(screen.queryByText('Clonar')).not.toBeInTheDocument();
});
```

**Nota:** El botón del tab tiene `title="Agente actual"` (`ChatsInspector.tsx:43`). Confirmar que el selector `getByTitle('Agente actual')` funciona con RTL. Si el botón no expone texto visible (solo icono), `getByTitle` es la query correcta.

---

## 14. Implementation order (suggested)

1. Editar `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.tsx`: eliminar los 3 bloques en `AgentTab()` (Temperatura form-row, Tokens form-row, btn-grid). Verificar visualmente que no queden contenedores vacíos ni margin sobrante.
2. Añadir las 6 nuevas assertions en `ChatsInspector.test.tsx`. Verificar que todas pasan: `cd frontend_dashboard && npm test -- chats-inspector`.
3. Correr FSD compliance greps: `grep -rEn "fetch\(" src/features/chats-inspector` (debe estar vacío).
4. Correr lint: `cd frontend_dashboard && npm run lint`.
5. Correr type-check: `cd frontend_dashboard && npx tsc -b`.
6. Correr full test suite: `cd frontend_dashboard && npm test`.
