# Task F01 — Eliminar controles de edición en AgentTab de ChatsInspector

- Slug: eliminar-controles-agente-tab
- HU id: HU-20260515-021114-eliminar-controles-de-edicion-del-panel
- Target frontend: frontend_dashboard
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections §4, §11, §12, §13, §14)
- Planner: frontend-task-planner-archon
- Date: 2026-05-14
- Iteration: 1
- Estimated LOC: 70 (~18 LOC eliminadas + ~52 LOC añadidas en tests)
- Risk: low

---

## 1. Context

Delivers acceptance criteria (verbatim from refinement §1):

- **AC-1:** Given que estoy en la sección Chats con un agente seleccionado, when navego al tab "Agente actual" del inspector derecho, then no aparecen los botones Prompt, Flujo, Probar ni Clonar.
- **AC-2:** Given que estoy en el tab "Agente actual" del inspector de Chats, when inspecciono los campos mostrados, then no aparecen los labels "Temperatura" ni "Tokens".
- **AC-3:** Given que se eliminan esos controles, when el `AgentTab` se renderiza, then el `Panel` muestra el avatar/nombre del agente, el status "Active routing handler", la Plataforma y el Modelo sin elementos vacíos ni padding sobrante.
- **AC-4:** Given que el panel de Chats carece de botones de acción, when el usuario quiere editar prompt o temperatura, then no hay ruta de edición desde ese panel.

Refinement sections that informed this task: §2 (no page change), §3 (no entity change), §4 (files affected), §11 (FSD rules), §12 (risks), §13 (tests), §14 (implementation order).

---

## 2. Dependencies

- depends_on: []
- blocks: []
- Inherits from upstream tasks: none (foundation task)
- Backend dependency: none

---

## 3. Files affected

| Path | Action | Role | LOC budget |
|------|--------|------|-----------|
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.tsx` | modify | Eliminar 3 bloques de JSX en función `AgentTab()` | -18 |
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx` | modify | Añadir import userEvent + 6 nuevas assertions | +52 |

Ningún archivo coincide con un glob de `spinal-files.yaml`. No se requieren `wiring_intents`.

---

## 4. Entity layer snippets (R-Zod boundary)

No aplica — esta tarea no crea ni modifica ninguna entidad.
`AgentTab` usa datos hardcodeados (mock); no consume hooks de entidad (refinamiento §3).

---

## 5. Feature layer snippets

### ChatsInspector.tsx — bloques a eliminar (forma canónica del delta)

El implementador debe localizar exactamente los 3 bloques siguientes dentro de la
función `AgentTab()` (lines ~187-212 del archivo actual) y eliminarlos sin dejar
contenedores vacíos ni atributos `style` residuales:

```tsx
// canonical — ELIMINAR estos 3 bloques de AgentTab()

// Bloque 1: form-row Temperatura (~líneas 187-190)
<div className="form-row">
  <span className="lbl">Temperatura</span>
  <span className="val">0.4</span>
</div>

// Bloque 2: form-row Tokens (~líneas 191-194)
<div className="form-row">
  <span className="lbl">Tokens</span>
  <span className="val">12,840 / 200k</span>
</div>

// Bloque 3: btn-grid completo (~líneas 195-212)
<div className="btn-grid" style={{ marginTop: 10 }}>
  <button className="insp-button"><Icon.edit />Prompt</button>
  <button className="insp-button"><Icon.workflow />Flujo</button>
  <button className="insp-button"><Icon.bolt />Probar</button>
  <button className="insp-button"><Icon.copy />Clonar</button>
</div>
```

### Estado resultante de AgentTab() tras la eliminación

```tsx
// canonical — AgentTab() post-eliminación (shape, no implementación completa)
function AgentTab() {
  return (
    <Panel title="Detalles del agente">
      {/* avatar + nombre + status — sin cambio */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        {/* ... */}
      </div>
      <div className="form-row">
        <span className="lbl">Plataforma</span>
        <span className="val">WhatsApp API</span>
      </div>
      <div className="form-row">
        <span className="lbl">Modelo</span>
        <span className="val mono">claude-haiku-4-5</span>
      </div>
      {/* FIN — no hay form-rows de Temperatura/Tokens, no hay btn-grid */}
    </Panel>
  );
}
```

---

## 6. Page mount (composition wiring)

Sin cambios. `<ChatsInspector chatId={selectedChatId} />` ya está montado en
`frontend_dashboard/src/pages/Dashboard.tsx:168` (refinamiento §10). No se modifica
`Dashboard.tsx`.

---

## 7. Tailwind tokens (if any)

Sin cambios en `index.css`. No se añaden tokens (refinamiento §8).

---

## 8. Entity / feature barrel updates

Sin cambios en barrels. `frontend_dashboard/src/features/chats-inspector/index.ts`
solo re-exporta `ChatsInspector` y permanece intacto (refinamiento §4).

---

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx` | modified | Añadir import `userEvent` + 6 nuevos `it` en el `describe` existente |

**Import a añadir** (si aún no está):
```tsx
import userEvent from '@testing-library/user-event';
```

**Test names + assertions** (el implementador escribe los cuerpos):

```
ChatsInspector — panel derecho simplificado
  ✓ (existentes × 5 — no modificar)
  + no muestra el campo "Temperatura" en tab Agente
      → click en title="Agente actual", queryByText("Temperatura") → null
  + no muestra el campo "Tokens" en tab Agente
      → click en title="Agente actual", queryByText("Tokens") → null
  + no muestra el botón "Prompt" en tab Agente
      → click en title="Agente actual", queryByText("Prompt") → null
  + no muestra el botón "Flujo" en tab Agente
      → click en title="Agente actual", queryByText("Flujo") → null
  + no muestra el botón "Probar" en tab Agente
      → click en title="Agente actual", queryByText("Probar") → null
  + no muestra el botón "Clonar" en tab Agente
      → click en title="Agente actual", queryByText("Clonar") → null
```

**Nota de implementación:** el tab selector usa `screen.getByTitle('Agente actual')`
(el botón en `ChatsInspector.tsx:43` tiene `title="Agente actual"`). Los tests deben
ser `async` con `await userEvent.click(...)` y usar `setup()` de userEvent v14:
`const user = userEvent.setup(); await user.click(...)`. Verificar la versión de
`@testing-library/user-event` en `frontend_dashboard/package.json`.

**Pseudo-código canónico de un test** (refinamiento §13):
```tsx
// canonical — shape de cada nuevo it
it('no muestra el campo "Temperatura" en tab Agente', async () => {
  const user = userEvent.setup();
  render(<ChatsInspector chatId="test-chat-id" />);
  await user.click(screen.getByTitle('Agente actual'));
  expect(screen.queryByText('Temperatura')).not.toBeInTheDocument();
});
```

---

## 10. Verification commands

```bash
# 1. Tests focalizados (debe terminar con 0 failures, 11 passed = 5 existentes + 6 nuevos)
cd frontend_dashboard && npm test -- chats-inspector

# 2. Type-check incremental
cd frontend_dashboard && npx tsc -b

# 3. Build prod (catch tree-shaking issues con Icon.workflow / Icon.bolt)
cd frontend_dashboard && npm run build

# 4. Lint (verificar warning de iconos sin uso)
cd frontend_dashboard && npm run lint

# 5. Full test suite (regresión)
cd frontend_dashboard && npm test
```

FSD compliance greps (deben retornar vacío):
```bash
cd frontend_dashboard

# No rogue fetch
grep -rEn "fetch\(" src/features/chats-inspector | grep -v "// allowed:" || echo "no rogue fetch"

# No deep imports
grep -rEn "from ['\"]@/features/[^'\"]+/(ui|model)/" src/features | grep -v "from ['\"]@/features/" || echo "no deep imports"
```

---

## 11. Definition of Done

- [ ] `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.tsx` modificado: los 3 bloques (`form-row` Temperatura, `form-row` Tokens, `div.btn-grid`) eliminados de `AgentTab()`.
- [ ] `AgentTab()` no contiene contenedores vacíos ni atributos `style` residuales tras la eliminación.
- [ ] `frontend_dashboard/src/features/chats-inspector/ui/ChatsInspector.test.tsx` modificado: import `userEvent` añadido + 6 nuevos `it` dentro del `describe` existente.
- [ ] `cd frontend_dashboard && npm test -- chats-inspector` → 11 tests passed (5 previos + 6 nuevos), 0 failed.
- [ ] `cd frontend_dashboard && npx tsc -b` → exit 0.
- [ ] `cd frontend_dashboard && npm run build` → exit 0.
- [ ] `cd frontend_dashboard && npm run lint` → exit 0 (o solo warnings no-error por Icon.workflow/Icon.bolt si aplica).
- [ ] `cd frontend_dashboard && npm test` → suite completa sin regresiones.
- [ ] FSD compliance greps (§10) retornan vacío.
- [ ] `Dashboard.tsx` no modificado.
- [ ] `features/chats-inspector/index.ts` no modificado.

---

## 12. FSD rules check

- **Import rules (layering):** no applicable — la tarea solo elimina JSX; no se añaden imports nuevos.
- **Barrel-only public API:** no applicable — no se crea ninguna feature/entity nueva.
- **Zod at HTTP boundary:** no applicable — `AgentTab` usa datos hardcodeados; no hay fetch nuevo.
- **TanStack Query for server data:** no applicable — sin server state nuevo.
- **No cross-feature imports:** applies — `ChatsInspector` no importa de ninguna otra feature; sin cambio tras la edición.
- **No deep imports:** applies — los imports existentes (`@/entities/chat`, `@/shared/ui`) van por barrels; sin cambio.
- **No fetch() in components/pages:** not applicable — sin fetch nuevo.
- **Tailwind token naming:** not applicable — sin tokens nuevos.
- **JSX files use .tsx:** applies — el archivo editado es `.tsx`; sin cambio de extensión.

---

## 13. Open questions / risks

1. **`Icon.workflow` / `Icon.bolt` sin uso tras la eliminación.** Estos iconos se acceden como propiedades de namespace (`Icon.workflow`, `Icon.bolt`) en el botón del `btn-grid`. Al eliminar el `btn-grid`, las referencias desaparecen, pero `import { Icon, Panel } from "@/shared/ui"` continúa siendo válido (Icon y Panel siguen usándose). TypeScript NO emitirá error. El linter puede emitir warning solo si está configurado para detectar propiedades de namespace no accedidas — comportamiento no garantizado. Verificar con `npm run lint`; si hay warning, no bloquea el build.

2. **Versión de `@testing-library/user-event`.** Los nuevos tests usan `userEvent.setup()` (API de v14). Confirmar con `cat frontend_dashboard/package.json | grep user-event` antes de escribir el cuerpo de los tests. Si es v13 o anterior, usar `userEvent.click(element)` directamente.

3. **Selector `getByTitle('Agente actual')`.** El botón de tab en `ChatsInspector.tsx:43` tiene `title="Agente actual"`. RTL `getByTitle` es la query correcta cuando el elemento solo muestra un ícono (sin texto visible). Confirmar que el botón no cambió desde la revisión del archivo.

4. **Datos hardcodeados en `AgentTab`.** El nombre del agente ("remarketing"), el modelo ("claude-haiku-4-5") y la plataforma ("WhatsApp API") son valores mock fijos. Deuda técnica preexistente — fuera de scope (refinamiento §12, punto 2).

5. **Backend dependency:** none.
