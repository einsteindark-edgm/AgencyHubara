# Task F01 — Auto-scroll y badge "Nuevo mensaje" en ChatsConversation

- Slug: auto-scroll-chats-conversation
- HU id: HU-20260514-014934-auto-scroll-al-mensaje-mas-reciente-en-l
- Target frontend: frontend_dashboard
- Refinement source: $ARTIFACTS_DIR/hu-refinada.md (sections cited inline)
- Planner: frontend-task-planner-archon
- Date: 2026-05-13
- Iteration: 1
- Estimated LOC: 265
- Risk: low

---

## 1. Context

Delivers acceptance criteria (verbatim from refinement §1):

- **AC-1:** Given que el operador está viendo una conversación con scroll ≤50 px del bottom, when llega un mensaje nuevo (TanStack Query incrementa `messages.length`), then el contenedor `.msgs` hace scroll suave (`behavior: 'smooth'`) hasta el sentinel al final de la lista.
- **AC-2:** Given que el operador ha hecho scroll hacia arriba (>50 px del bottom), when llega un mensaje nuevo, then NO se fuerza scroll automático y aparece el badge "↓ Nuevo mensaje" superpuesto sobre el compositor.
- **AC-3:** Given que el badge "↓ Nuevo mensaje" es visible, when el operador hace clic en él, then el scroll salta al sentinel (`behavior: 'smooth'`) y el badge desaparece.
- **AC-4:** Given que la vista de conversación se monta por primera vez o cambia de `chatId`, when el componente termina de renderizar la lista de mensajes, then el scroll se posiciona directamente en el último mensaje de forma instantánea (`behavior: 'auto'`).
- **AC-5:** Given que el operador hace scroll manualmente hasta ≤50 px del bottom, when ese evento de scroll ocurre, then el badge desaparece si estaba visible.

Refinement sections that informed this task: §1, §2, §4, §5, §8, §10, §11, §12, §13, §14.

---

## 2. Dependencies

- depends_on: []
- blocks: []
- Inherits from upstream tasks: none (foundation task)
- Backend dependency: none — los mensajes ya fluyen vía `useChatMessages(chatId)` con polling de 3 s (entities/chat/api.ts); ningún endpoint nuevo ni cambio de schema (refinement §6).

---

## 3. Files affected

| Path (relativo a repo root) | Action | Role | LOC budget |
|-----------------------------|--------|------|-----------|
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.ts` | new | Hook de estado local (refs, estado, efectos, handlers) | ~70 |
| `frontend_dashboard/src/features/chats-conversation/ui/ChatsMessageList.tsx` | new | Subcomponente interno: lista de burbujas + sentinel + badge | ~65 |
| `frontend_dashboard/src/features/chats-conversation/ui/ChatsConversation.tsx` | modify | Sustituir bloque inline del tab Chat por `<ChatsMessageList>` | +5 net |
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.test.ts` | new | Tests del hook (renderHook + act, mocks de scrollIntoView y layout) | ~75 |
| `frontend_dashboard/src/features/chats-conversation/ui/ChatsMessageList.test.tsx` | new | Tests RTL del subcomponente (mock del hook) | ~50 |

**Nota sobre ChatsConversation.tsx:** acción "modify" sobre un fichero NO declarado spinal. Plan de 1 tarea → sin riesgo de contención en batch. Sin wiring_intent necesario.

**Nota sobre el barrel `features/chats-conversation/index.ts`:** NO se modifica. `ChatsMessageList` es subcomponente interno de la feature y NO se exporta desde el barrel (refinement §10, §11.2).

---

## 4. Entity layer snippets (R-Zod boundary)

No new entities. No entity modifications.

Los mensajes llegan al subcomponente vía props desde `ChatsConversation`, que ya llama `useChatMessages(chatId)`. El tipo relevante es `ChatMessageItem` de `entities/chat/model.ts`:

```ts
// existente — entities/chat/model.ts (no modificar)
export type MessageKind = "in" | "out" | "day" | "system" | "tag" | "audio";

export interface ChatMessageItem {
  kind: MessageKind;
  text?: string;
  time?: string;
  status?: "sent" | "read";
  dur?: string;
}
```

Reused from existing entities: `ChatMessageItem` (importado via `@/entities/chat` en ChatsConversation; se pasa como prop a ChatsMessageList).

---

## 5. Feature layer snippets

### `model/useAutoScroll.ts` — nuevo hook de estado local

```ts
// canonical — src/features/chats-conversation/model/useAutoScroll.ts
import { useEffect, useRef, useState } from "react";

export function useAutoScroll(messagesLength: number) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sentinelRef  = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  const [showNewBadge, setShowNewBadge] = useState(false);

  // mount / chatId-change: scroll instantáneo
  useEffect(() => {
    sentinelRef.current?.scrollIntoView({ behavior: "auto" });
    isAtBottomRef.current = true;
    setShowNewBadge(false);
  }, []); // key={chatId} en parent garantiza remount limpio

  // nuevo mensaje
  useEffect(() => {
    // guard: no corre en mount (depende de messagesLength, que tiene valor inicial)
    // implementer debe usar useRef para distinguir mount vs update
  }, [messagesLength]);

  const handleScroll = () => { /* lee scrollHeight - scrollTop - clientHeight */ };
  const scrollToBottom = () => { /* scrollIntoView smooth + reset badge */ };

  return { containerRef, sentinelRef, showNewBadge, handleScroll, scrollToBottom };
}
```

**Nota de implementación (refinement §4 — lógica interna):**

- `isAtBottomRef` es `useRef<boolean>(true)` — ref mutable, sin re-render.
- El segundo `useEffect([messagesLength])` NO debe dispararse en el primer render. El implementer debe usar un ref de "has mounted" (`const mountedRef = useRef(false)`) dentro del efecto para saltarse la ejecución en mount. Alternativamente, separar el efecto de mount (array vacío) del de "nuevo mensaje" (array `[messagesLength]`) y aceptar que el efecto de mensaje corra en mount pero con `isAtBottomRef.current = true`, lo que producirá scroll smooth en mount antes del efecto de mount (que hace `auto`). **Recommended default:** usar un `isMounted` ref dentro del efecto `[messagesLength]` para saltar la primera ejecución.
- `handleScroll` — leer `containerRef.current.scrollHeight - containerRef.current.scrollTop - containerRef.current.clientHeight`; si ≤50 → `isAtBottomRef.current = true`, `setShowNewBadge(false)`; si >50 → `isAtBottomRef.current = false`.
- `scrollToBottom` — `sentinelRef.current?.scrollIntoView({ behavior: 'smooth' })`, luego `isAtBottomRef.current = true`, `setShowNewBadge(false)`.

### `ui/ChatsMessageList.tsx` — subcomponente interno

```tsx
// canonical — src/features/chats-conversation/ui/ChatsMessageList.tsx
import type { ChatMessageItem } from "@/entities/chat";
import { ChatsBubble } from "./ChatsBubble";
import { ChatsComposer } from "./ChatsComposer";
import { useAutoScroll } from "../model/useAutoScroll";

interface Props {
  messages: ChatMessageItem[];
}

export function ChatsMessageList({ messages }: Props) {
  const { containerRef, sentinelRef, showNewBadge,
          handleScroll, scrollToBottom } = useAutoScroll(messages.length);
  return (
    <>
      <div className="msgs" ref={containerRef} onScroll={handleScroll}>
        {messages.map((m, i) => <ChatsBubble key={i} message={m} />)}
        <div ref={sentinelRef} />
      </div>
      {showNewBadge && (
        <button
          className="/* badge classes usando tokens existentes */"
          onClick={scrollToBottom}
        >
          ↓ Nuevo mensaje
        </button>
      )}
      <ChatsComposer />
    </>
  );
}
```

**Nota sobre el badge (refinement §5 y §8):**
El badge usa clases Tailwind con tokens del `@theme` ya existentes:
- `bg-accent-soft` (`--color-accent-soft: rgba(10,132,255,0.18)`)
- `text-accent-fg` (`--color-accent-fg: #4ea3ff`)
- Posición: `absolute bottom-[56px]` (arbitrary value one-off, no nuevo token).

El implementer debe añadir `relative` al contenedor padre o al wrapper de `ChatsMessageList` si el badge usa `absolute`. Verificar el layout existente en `ChatsConversation.tsx` para determinar el elemento ancestor correcto.

**Nota sobre `ChatsComposer`:** El refinement §10 muestra que `<ChatsComposer />` sigue inmediatamente después del bloque de mensajes dentro del tab "Chat". `ChatsMessageList` lo incluye como hermano después del badge para reproducir la estructura.

### Barrel — sin cambios

`features/chats-conversation/index.ts` no se modifica:
```ts
// existente — no tocar
export { ChatsConversation } from "./ui/ChatsConversation";
```

`ChatsMessageList` es interno; no se exporta.

---

## 6. Page mount (composition wiring)

No page mount change. `Dashboard.tsx` no se modifica (refinement §2 — "no page change").

El wiring es interno a la feature: `ChatsConversation.tsx` importa y monta `ChatsMessageList`.

```diff
// frontend_dashboard/src/features/chats-conversation/ui/ChatsConversation.tsx
+ import { ChatsMessageList } from "./ChatsMessageList";

  {subTab === "Chat" && (
    <>
-     <div className="msgs">
-       {messages.map((m, i) => (
-         <ChatsBubble key={i} message={m} />
-       ))}
-     </div>
-     <ChatsComposer />
+     <ChatsMessageList messages={messages} key={chatId ?? ""} />
    </>
  )}
```

El `key={chatId ?? ""}` en `ChatsMessageList` garantiza que React desmonte y remonte el componente cuando cambia `chatId`, ejecutando el efecto de mount y haciendo scroll instantáneo al último mensaje (AC-4, refinement §4 nota sobre `key`).

Los imports de `ChatsBubble` y `ChatsComposer` en `ChatsConversation.tsx` pueden eliminarse si `ChatsMessageList` los importa internamente — el implementer debe verificar si quedan otros usos antes de borrar.

---

## 7. Tailwind tokens (if any)

No new tokens. El badge reutiliza tokens existentes del `@theme` de `frontend_dashboard/src/index.css` (refinement §8):

| Necesidad | Clase Tailwind | Token |
|-----------|---------------|-------|
| Fondo del badge | `bg-accent-soft` | `--color-accent-soft` |
| Texto del badge | `text-accent-fg` | `--color-accent-fg` |
| Borde (opcional) | `border-accent-soft` | mismo |

Valores de offset de posición (si se necesitan) → arbitrary Tailwind: `bottom-[56px]`, `right-4`, etc. Sin nuevo token.

---

## 8. Entity / feature barrel updates

No existing barrel edits — el barrel `features/chats-conversation/index.ts` queda intacto.

`ChatsMessageList` es interno a la feature (un único consumidor: `ChatsConversation`). No se promueve a shared ni se exporta desde el barrel.

---

## 9. Tests

| Test file | New / modified | Scenarios |
|-----------|---------------|-----------|
| `frontend_dashboard/src/features/chats-conversation/model/useAutoScroll.test.ts` | new | 5 escenarios del hook (detalle abajo) |
| `frontend_dashboard/src/features/chats-conversation/ui/ChatsMessageList.test.tsx` | new | 4 escenarios RTL (detalle abajo) |

**Setup requerido en ambos archivos de test:**

```ts
// en beforeEach de cada archivo
Element.prototype.scrollIntoView = vi.fn();
// Para handleScroll: asignar layout properties via Object.defineProperty
// (jsdom no calcula layout físico — refinement §12.1 y §12.2)
```

**Test names — useAutoScroll.test.ts:**

- `useAutoScroll :: mount → showNewBadge is false and scrollIntoView called with behavior auto`
- `useAutoScroll :: new message when isAtBottom=true → scrollIntoView called with behavior smooth, badge stays hidden`
- `useAutoScroll :: new message when isAtBottom=false → badge becomes visible, scrollIntoView NOT called`
- `useAutoScroll :: handleScroll with offset ≤50 → isAtBottom becomes true, badge hides`
- `useAutoScroll :: scrollToBottom → scrollIntoView called with behavior smooth, badge hides`

**Test names — ChatsMessageList.test.tsx:**

- `ChatsMessageList :: renders without crash when messages is empty`
- `ChatsMessageList :: renders ChatsBubble for each message, no badge initially`
- `ChatsMessageList :: shows badge when useAutoScroll returns showNewBadge=true`
- `ChatsMessageList :: clicking badge calls scrollToBottom`

---

## 10. Verification commands

Todos los comandos deben ejecutarse con CWD = `frontend_dashboard/` (project-context.md §Command conventions).

```bash
# Tipo-check incremental después de cada fichero nuevo
cd frontend_dashboard && npx tsc -b

# Tests del hook
cd frontend_dashboard && npm test -- chats-conversation/model

# Tests del subcomponente
cd frontend_dashboard && npm test -- chats-conversation/ui/ChatsMessageList

# Lint
cd frontend_dashboard && npm run lint

# Suite completa (verificar sin regresiones)
cd frontend_dashboard && npm test

# Build de producción
cd frontend_dashboard && npm run build
```

**FSD compliance greps (deben devolver vacío):**

```bash
cd frontend_dashboard

# Sin fetch() rogue en features o pages
grep -rEn "fetch\(" src/features src/pages src/app | grep -v "// allowed:"

# Sin deep imports de features
grep -rEn "from ['\"]@/features/[^'\"]+/(ui|model)/" src/features

# Sin cross-feature imports
grep -rEn "from ['\"]@/features/" src/features \
  | grep -vE "^src/features/([a-z-]+)/[^:]+:.*from ['\"]@/features/\1"
```

---

## 11. Definition of Done

- [ ] `model/useAutoScroll.ts` creado con lógica completa (refs, estados, 2 efectos, `handleScroll`, `scrollToBottom`).
- [ ] `ui/ChatsMessageList.tsx` creado con: `containerRef`, `sentinelRef`, `onScroll={handleScroll}`, `.map()` de burbujas, sentinel `<div>`, badge condicional, `<ChatsComposer />`.
- [ ] `ui/ChatsConversation.tsx` editado: bloque inline `{subTab === "Chat" && …}` reemplazado por `<ChatsMessageList messages={messages} key={chatId ?? ""} />`.
- [ ] `features/chats-conversation/index.ts` sin cambios (ChatsMessageList no exportado).
- [ ] `model/useAutoScroll.test.ts` creado con los 5 tests nombrados en §9. Todos pasan.
- [ ] `ui/ChatsMessageList.test.tsx` creado con los 4 tests nombrados en §9. Todos pasan.
- [ ] `cd frontend_dashboard && npm test` — suite completa verde sin regresiones.
- [ ] `cd frontend_dashboard && npm run build` — build de producción sin errores.
- [ ] `cd frontend_dashboard && npx tsc -b` — type-check limpio.
- [ ] FSD compliance greps (§10) devuelven vacío.
- [ ] Badge visible solo cuando `showNewBadge=true`; clic en badge dispara `scrollToBottom` y oculta el badge.
- [ ] `key={chatId ?? ""}` presente en el JSX de `<ChatsMessageList>` dentro de `ChatsConversation.tsx`.

---

## 12. FSD rules check

- **Import rules (layering):** aplica — `ChatsMessageList` importa de `@/entities/chat` (capa inferior) y `./ChatsBubble` / `./ChatsComposer` (misma feature). Sin imports upward. Conforme.
- **Barrel-only public API:** aplica — `ChatsMessageList` es internal; `ChatsConversation` lo importa con path relativo (`./ChatsMessageList`), correcto para subcomponentes de la misma feature. El barrel `index.ts` no cambia. Conforme.
- **Zod at HTTP boundary:** not applicable — no se añade fetch ni query nueva. Los datos fluyen por schemas Zod ya existentes en `entities/chat/contracts.ts`.
- **TanStack Query for server data:** not applicable — no se añade nuevo server state. `useChatMessages` ya existe y no se modifica.
- **No cross-feature imports:** aplica — `useAutoScroll` y `ChatsMessageList` están dentro de `features/chats-conversation/`. Sin imports a otras features. Conforme.
- **No deep imports:** aplica — imports relativos entre subcomponentes de la misma feature (`./ChatsMessageList`, `../model/useAutoScroll`) son correctos y no cruzan barrera de feature. Conforme.
- **No fetch() in components/pages:** not applicable — no se añade fetch.
- **Tailwind token naming:** aplica — el badge usa `bg-accent-soft`, `text-accent-fg` (tokens existentes). Sin tokens nuevos `--color-text-*`. Conforme.
- **JSX files use .tsx:** aplica — `ChatsMessageList.tsx` (JSX) y `useAutoScroll.ts` (hook puro sin JSX). Correcto.

---

## 13. Open questions / risks

1. **scrollIntoView en jsdom (refinement §12.1):** `Element.prototype.scrollIntoView` no está implementado en jsdom y arroja `not a function` sin mock. El setup global `src/test/setup.ts` no incluye el mock. **Recommended default:** añadir `Element.prototype.scrollIntoView = vi.fn()` en el `beforeEach` de cada test file de esta tarea (no en el setup global, para evitar impacto en otros tests que podrían depender del comportamiento nativo).

2. **scrollHeight / scrollTop en jsdom (refinement §12.2):** jsdom no calcula layout físico. Para testear `handleScroll`, usar `Object.defineProperty(containerRef.current!, 'scrollHeight', { value: 500, configurable: true })` etc. antes de invocar el handler. **Recommended default:** documentar el patrón en el primer test de `handleScroll`.

3. **Segundo efecto `[messagesLength]` en mount:** El efecto correrá una vez en mount con el valor inicial de `messagesLength`. Si `isAtBottomRef.current = true` (valor inicial), producirá un scroll smooth antes de que el efecto de mount haga el scroll auto. **Recommended default:** usar un `isMounted` ref dentro del segundo efecto para saltarse la primera ejecución (ver §5 nota de implementación). Verificar que AC-4 se cumple: scroll instantáneo en mount.

4. **`ChatsComposer` en `ChatsMessageList`:** El refinement §10 indica que `ChatsComposer` se incluye dentro del tab "Chat". Al moverlo a `ChatsMessageList`, los imports de `ChatsComposer` (y `ChatsBubble`) en `ChatsConversation.tsx` quedan sin uso. El implementer debe eliminarlos para evitar lint warnings.

5. **Posición del badge (absolute vs sticky):** El refinement §4 describe el badge como "superpuesto sobre el compositor". Si el badge es `absolute` necesita un ancestor con `position: relative`. Verificar el layout CSS de `<main className="center">` antes de elegir la estrategia de posicionamiento.

6. **Backend dependency:** none.
