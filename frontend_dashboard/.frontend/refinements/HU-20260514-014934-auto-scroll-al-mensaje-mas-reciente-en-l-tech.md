# Tech refinement (frontend) — Auto-scroll al mensaje más reciente en la vista de conversación

HU id: HU-20260514-014934-auto-scroll-al-mensaje-mas-reciente-en-l
Source: $ARTIFACTS_DIR/hu-original.md
Target frontend: frontend_dashboard (cwd: /Users/edgm/Documents/Projects/AgencyHubara/frontend_dashboard)
Layout status: FSD in place
Refiner: frontend-tech-refiner-archon
Date: 2026-05-13
Iteration: 1
requires_backend_change: false

---

## 1. Scope

**Summary:** Gestión inteligente del scroll en la vista de mensajes de `ChatsConversation` — auto-scroll suave cuando el operador está al fondo, badge "↓ Nuevo mensaje" cuando no lo está.

**Acceptance criteria:**

- **Given** que el operador está viendo una conversación con scroll ≤50 px del bottom, **when** llega un mensaje nuevo (TanStack Query incrementa `messages.length`), **then** el contenedor `.msgs` hace scroll suave (`behavior: 'smooth'`) hasta el sentinel al final de la lista.
- **Given** que el operador ha hecho scroll hacia arriba (>50 px del bottom), **when** llega un mensaje nuevo, **then** NO se fuerza scroll automático y aparece el badge "↓ Nuevo mensaje" superpuesto sobre el compositor.
- **Given** que el badge "↓ Nuevo mensaje" es visible, **when** el operador hace clic en él, **then** el scroll salta al sentinel (`behavior: 'smooth'`) y el badge desaparece.
- **Given** que la vista de conversación se monta por primera vez o cambia de `chatId`, **when** el componente termina de renderizar la lista de mensajes, **then** el scroll se posiciona directamente en el último mensaje de forma instantánea (`behavior: 'auto'`).
- **Given** que el operador hace scroll manualmente hasta ≤50 px del bottom, **when** ese evento de scroll ocurre, **then** el badge desaparece si estaba visible.

**Out of scope:**

- Notificaciones push, sonido o alertas del sistema operativo.
- Lógica de "marcar como leído" o conteo de mensajes no leídos.
- Indicador de "el agente está escribiendo" (typing indicator).
- Auto-scroll en las sub-tabs "Notas" y "Archivos" (solo aplica al tab "Chat").
- Auto-scroll en la lista de conversaciones (inbox sidebar).
- Gestión de paneles múltiples side-by-side.

---

## 2. Page(s) affected

**Decision:** no page change

**Justification:** La HU es completamente interna a `features/chats-conversation/`. `Dashboard.tsx` monta `<ChatsConversation chatId={selectedChatId} />` en `ChatsSection` (~línea 167) y no necesita cambiar. El estado de scroll (`isAtBottom`, `showNewBadge`) vive dentro del nuevo subcomponente `ChatsMessageList`.

**Cross-feature state added/lifted:** none

---

## 3. Entities affected/created

Ninguna entidad nueva ni modificada.

Los mensajes ya fluyen vía `useChatMessages(chatId)` → `entities/chat/api.ts:161` → `entities/session/api.ts` con polling cada 3 s. `ChatsConversation` ya recibe `messages: ChatMessageItem[]` correctamente. No se agrega ninguna nueva capa de transporte ni query hook.

---

## 4. Features affected/created

### `features/chats-conversation/` — extended

| File | Status | Change |
|------|--------|--------|
| `model/useAutoScroll.ts` | **new** | Hook de estado local: `isAtBottomRef`, `showNewBadge`, `handleScroll`, `scrollToBottom`, `containerRef`, `sentinelRef` |
| `ui/ChatsMessageList.tsx` | **new** | Subcomponente interno: renderiza `<div className="msgs">` con los mensajes, sentinel `<div>` al final, y badge condicional. Consume `useAutoScroll`. |
| `ui/ChatsConversation.tsx` | **edit** | Reemplaza el bloque inline `{subTab === "Chat" && <div className="msgs">…</div>}` por `<ChatsMessageList messages={messages} key={chatId ?? ""} />` |

**Props shape de `ChatsMessageList`** (datos locales a la feature, sin cross-feature state):

```ts
// pseudo
interface Props {
  messages: ChatMessageItem[];  // del modelo entities/chat/model.ts
}
```

**Entity hooks consumed:** ninguno nuevo — los mensajes llegan al componente vía props desde `ChatsConversation` (que ya llama `useChatMessages`).

**Local state hook creado: `useAutoScroll`**

```ts
// pseudo — model/useAutoScroll.ts
function useAutoScroll(messagesLength: number): {
  containerRef: RefObject<HTMLDivElement>;
  sentinelRef:  RefObject<HTMLDivElement>;
  showNewBadge: boolean;
  handleScroll: () => void;
  scrollToBottom: () => void;
}
```

Lógica interna:

- `isAtBottomRef` (mutable `useRef<boolean>`, no state) — evita stale closure en efectos. Valor inicial: `true`.
- `showNewBadge` (useState) — controla visibilidad del badge. Valor inicial: `false`.
- `useEffect([], [])` — mount: `sentinelRef.current?.scrollIntoView({ behavior: 'auto' })`, `isAtBottomRef.current = true`, `setShowNewBadge(false)`. Corre una vez al montar (el `key={chatId}` en `ChatsConversation` garantiza remount limpio al cambiar conversación).
- `useEffect([messagesLength])` — nuevo mensaje: si `isAtBottomRef.current` → `sentinelRef.current?.scrollIntoView({ behavior: 'smooth' })`; si no → `setShowNewBadge(true)`. Este efecto NO corre en el mount inicial (solo en cambios de `messagesLength`).
- `handleScroll` — lee `scrollHeight - scrollTop - clientHeight` del `containerRef`; si ≤50 → `isAtBottomRef.current = true`, `setShowNewBadge(false)`; si >50 → `isAtBottomRef.current = false`.
- `scrollToBottom` — `sentinelRef.current?.scrollIntoView({ behavior: 'smooth' })`, `isAtBottomRef.current = true`, `setShowNewBadge(false)`.

**Nota sobre `key` en `ChatsConversation`:** `<ChatsMessageList messages={messages} key={chatId ?? ""} />` desmonta y remonta `ChatsMessageList` al cambiar `chatId`, lo que ejecuta el `useEffect` de mount y hace scroll instantáneo sin animación, reseteando el estado de badge. Esto satisface el AC de "se cambia de conversación activa".

---

## 5. Shared primitives

No new shared primitives.

El badge "↓ Nuevo mensaje" es un botón local dentro de `ui/ChatsMessageList.tsx` (un único consumidor → no llega al umbral de 2+ para promover a `shared/ui/`). Usa clases Tailwind con tokens existentes del `@theme`.

---

## 6. Backend contract dependencies

| Endpoint | Status | Cited backend file | Frontend Zod schema |
|----------|--------|--------------------|---------------------|
| `GET /api/dashboard/sessions/:id` | exists, no change | `hubara_agency/src/dashboard/` — polling 3 s en `entities/session/api.ts` | `sessionDetailsSchema` en `entities/session/contracts.ts` |
| `GET /api/dashboard/stream` (SSE) | exists, no change | mounts once en `Dashboard.tsx` vía `useSessionsStream()` | no relevante para esta HU |

**Blocked work items:** none — sin dependencias bloqueantes.

**Behavior verification (Step 1.5):**
Step 1.5 **no aplica** a esta HU. El HU trata sobre comportamiento de scroll en la UI, no sobre la emisión de datos nuevos del backend. Los verbos de trigger ("auto-scroll", "aparece un badge") son de UX, no de visualización de datos nuevos. La cadena de datos `messages` ya está confirmada como funcional (polling vía `useSession` + SSE en `useSessionsStream`).

---

## 7. Cross-feature state

No cross-feature state added. Todo el estado (`isAtBottomRef`, `showNewBadge`) es local a `ChatsMessageList` / `useAutoScroll`.

---

## 8. Tailwind token deltas

No new tokens. El badge reutiliza utilidades existentes generadas por el `@theme` de `frontend_dashboard/src/index.css`:

| Necesidad | Utilidad Tailwind existente | Token fuente |
|-----------|-----------------------------|--------------|
| Fondo del badge | `bg-accent-soft` | `--color-accent-soft: rgba(10,132,255,0.18)` |
| Texto del badge | `text-accent-fg` | `--color-accent-fg: #4ea3ff` |
| Borde opcional | `border-accent-soft` | mismo token |

Si la posición del badge requiere un valor de offset arbitrario (no token), se usa `bottom-[56px]` o similar (one-off → arbitrary value, no nuevo token).

---

## 9. App-layer wiring

no app-layer change

`main.tsx` no cambia. `app/providers/index.tsx` no cambia. No se añade ningún proveedor nuevo.

---

## 10. Composition wiring

| Feature | Mount file | Cambio en JSX | Props pasadas |
|---------|-----------|---------------|--------------|
| `ChatsMessageList` | `frontend_dashboard/src/features/chats-conversation/ui/ChatsConversation.tsx` | Reemplaza bloque `{subTab === "Chat" && (<> <div className="msgs">{messages.map(…)}</div> <ChatsComposer /> </>)}` por `{subTab === "Chat" && (<> <ChatsMessageList messages={messages} key={chatId ?? ""} /> <ChatsComposer /> </>)}` | `messages`, `key` |

`ChatsMessageList` es un subcomponente interno de la feature — no se exporta desde `features/chats-conversation/index.ts`.

---

## 11. Hard rules check

1. **Import rules (layering):** aplica — `ChatsMessageList` importa de `entities/chat` (capa inferior) y `shared/ui` (capa inferior). Sin imports upward. Conforme.
2. **Barrel-only public API:** aplica — `ChatsMessageList` es subcomponente interno; NO se exporta desde `features/chats-conversation/index.ts`. Correcto.
3. **Zod at HTTP boundary:** not applicable — no se añade ningún fetch ni query nueva. Los datos pasan por `sessionDetailsSchema` ya existente.
4. **TanStack Query for server data:** not applicable — no se añade nuevo server state; `useChatMessages` ya existe.
5. **No cross-feature imports:** aplica — `useAutoScroll` y `ChatsMessageList` están dentro de `features/chats-conversation/`. Sin imports a otras features. Conforme.
6. **No deep imports:** aplica — `ChatsConversation` importa `ChatsMessageList` con path interno relativo (`./ChatsMessageList`), lo cual es correcto para subcomponentes de la misma feature (no cruza barrera de feature).
7. **No fetch() in components/pages:** not applicable — no se añade fetch.
8. **Tailwind token naming:** aplica — el badge usa `bg-accent-soft`, `text-accent-fg`. Sin tokens nuevos `--color-text-*`. Conforme.
9. **JSX files use .tsx:** aplica — `ChatsMessageList.tsx` (JSX) y `useAutoScroll.ts` (hook puro, sin JSX). Correcto.

---

## 12. Risks / open questions

1. **`scrollIntoView` en jsdom:** `scrollIntoView` no está implementado en jsdom (entorno de Vitest). Los tests de `useAutoScroll.test.ts` deben mockear `Element.prototype.scrollIntoView = vi.fn()` en `beforeEach`. Recommended default: agregar el mock en `src/test/setup.ts` si no existe ya, o en el archivo de test individual.

2. **`scrollHeight` / `scrollTop` en jsdom:** jsdom no calcula layout real; `handleScroll` no puede testarse con valores físicos de scroll. Recommended default: en los tests, asignar manualmente propiedades con `Object.defineProperty(el, 'scrollHeight', { value: 500, configurable: true })` antes de llamar `handleScroll()`, luego verificar el cambio de estado (`showNewBadge`).

3. **Primer render con mensajes vacíos:** `useChatMessages` puede devolver `data = []` brevemente antes de la respuesta del servidor. El `useEffect` de mount invoca `scrollIntoView` sobre un sentinel en lista vacía (no-op inofensivo). Al llegar los datos, `messagesLength` cambia de 0 → N; como `isAtBottomRef.current = true` (valor inicial), el efecto de "nuevo mensaje" ejecuta scroll smooth al sentinel. Comportamiento correcto y aceptable.

4. **Código muerto `features/session-chat/`:** La feature `session-chat` (con su propio `ChatMessageList.tsx` con auto-scroll simplificado) ya no está montada en `Dashboard.tsx`. Es código muerto pero sin violación activa. Recommended default: eliminarlo en una PR de limpieza separada, NUNCA bundlear con esta HU.

5. **Backend dependency:** none.

6. **Defer to follow-up design doc:** none.

7. **Pre-existing FSD violations in touched code:** ninguna en `features/chats-conversation/`. La feature está limpia.

---

## 13. Tests

| Test file | Type | Asserts |
|-----------|------|---------|
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.test.ts` | hook (renderHook + act) | (1) mount → `showNewBadge = false`; (2) messagesLength aumenta + isAtBottom=true → `showNewBadge` permanece false, `scrollIntoView` llamado con `{ behavior: 'smooth' }`; (3) messagesLength aumenta + isAtBottom=false → `showNewBadge = true`, `scrollIntoView` NOT called; (4) `handleScroll` con atBottom=true → `showNewBadge = false`; (5) `scrollToBottom()` → `showNewBadge = false`, `scrollIntoView` llamado con `{ behavior: 'smooth' }` |
| `frontend_dashboard/src/features/chats-conversation/ui/ChatsMessageList.test.tsx` | RTL | (1) con `messages=[]` → no crash, no badge visible; (2) con messages normales → burbujas renderizadas, badge ausente inicialmente; (3) badge aparece cuando `useAutoScroll` retorna `showNewBadge=true` (mock del hook); (4) click en badge → llama `scrollToBottom` del hook |

---

## 14. Implementation order

1. **Crear `model/useAutoScroll.ts`** — lógica pura sin JSX (refs, estado, efectos). Verificar: `cd frontend_dashboard && npx tsc -b`.

2. **Crear `ui/ChatsMessageList.tsx`** — renderiza `<div className="msgs" ref={containerRef} onScroll={handleScroll}>`, bubbles `.map()`, `<div ref={sentinelRef} />` al final, badge condicional `{showNewBadge && <button …>↓ Nuevo mensaje</button>}`. Verificar: `cd frontend_dashboard && npx tsc -b`.

3. **Editar `ui/ChatsConversation.tsx`** — importar `ChatsMessageList`, reemplazar el bloque inline del tab "Chat", agregar `key={chatId ?? ""}`. Verificar: `cd frontend_dashboard && npx tsc -b && npm run lint`.

4. **Escribir `model/useAutoScroll.test.ts`** — mocks de `scrollIntoView` y `scrollHeight`. Verificar: `cd frontend_dashboard && npm test -- chats-conversation/model`.

5. **Escribir `ui/ChatsMessageList.test.tsx`** — RTL + mock de hook. Verificar: `cd frontend_dashboard && npm test -- chats-conversation/ui/ChatsMessageList`.

6. **Suite completa** — `cd frontend_dashboard && npm test` (todos los tests deben pasar) + `npm run build` (sin errores de prod).
