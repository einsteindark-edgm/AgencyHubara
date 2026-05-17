# Sección 06 — Frontend de un plugin (estructura + Section + barrel + Icon)

> **Cuándo leer esto:** vas a crear o editar el frontend de un plugin
> (cualquier template). Asumí que ya leíste `sections/05-frontend-fsd.md`.
> **Pre-requisito:** `sections/01-general.md`, `sections/05-frontend-fsd.md`.
> **Tamaño:** ~8 KB.

---

## §1. Estructura del frontend de un plugin

Un plugin frontend tiene estos archivos (mínimo 2, hasta 10-20 según
complejidad):

```
frontend_dashboard/src/plugins/<id>/
├── plugin.yaml                          # MANIFEST (raíz del plugin)
└── frontend/
    ├── index.ts                         # BARREL — export default <Id>Section
    ├── <Id>Section.tsx                  # Page component (root del plugin)
    ├── features/                        # Features internas (cross-feature OK acá)
    │   ├── <feature-a>/
    │   │   ├── index.ts
    │   │   └── ui/
    │   │       └── <Component>.tsx
    │   └── <feature-b>/
    ├── components/                      # Subcomponentes que NO son features completas
    │   └── <Component>.tsx
    ├── hooks/                           # Hooks locales al plugin
    │   └── use<X>.ts
    └── styles.css                       # (opcional) tokens específicos del plugin
```

### Reglas críticas:

- **`index.ts` debe `export default`** — el registry generado usa
  `lazy(() => import("@plugins/<id>/frontend"))` y necesita el default.
- **`<Id>Section.tsx`** es el componente que renderiza el shell — no
  tiene `<html>`, `<body>` ni router. Es un subtree React.
- **Cross-feature dentro del plugin OK** (relajación del FSD strict):
  `features/<a>/` PUEDE importar de `features/<b>/`.
- **Cross-plugin SIEMPRE prohibido**: `@plugins/chats/* ❌→ @plugins/orders/*`.

---

## §2. Barrel mínimo (`index.ts`)

```typescript
// canonical — plugins/<id>/frontend/index.ts
export { default, <Id>Section } from "./<Id>Section";
export type { <Id>SectionProps } from "./<Id>Section";
```

El `default` es lo que `lazy()` importa. El named export + el `type`
quedan para uso interno o tests.

---

## §3. Section component — contrato "props bandejón"

El shell renderiza `<ActivePage showSidebar showInspector ... />` con un
set fijo de props. Tu plugin recibe esas props y decide qué mostrar.

### §3.1 Props bandejón (contrato canónico)

```typescript
// canonical — plugins/<id>/frontend/<Id>Section.tsx
export interface <Id>SectionProps {
  showSidebar: boolean;        // operador toggled la sidebar; respetá si renderizar
  showInspector: boolean;      // operador toggled el inspector (panel derecho)
  // futuras props del shell van acá — siempre OPTIONAL si las agregás
}

export function <Id>Section({ showSidebar, showInspector }: <Id>SectionProps) {
  return (
    <>
      {showSidebar && <aside className="sidebar"><SidebarContent /></aside>}
      <main className="main"><MainContent /></main>
      {showInspector && <aside className="inspector"><InspectorContent /></aside>}
    </>
  );
}

export default <Id>Section;
```

### §3.2 Por qué "props bandejón"

El shell pasa un set de booleans y callbacks fijos a TODOS los plugins.
Esto permite:

- **Consistencia visual** — sidebar y inspector son iguales cross-plugin.
- **Toggling unificado** — el operador clickea "hide sidebar" en el
  Toolbar y TODOS los plugins responden uniforme.
- **Cero acoplamiento** — el plugin no necesita saber qué otros plugins
  existen ni cómo orquestar el shell.

Si tu plugin NO necesita sidebar o inspector, simplemente ignorá las
props.

---

## §4. Sections vs sidebar en el manifest

```yaml
frontend:
  contributes:
    sections:                      # entradas del SEGMENTED CONTROL del Toolbar
      - { key: chat, label: Chats, order: 1, icon: chat }
    sidebar:                       # entradas del SIDEBAR (reservado para futuro)
      - { route: /chats, label: Chats, icon: chat }
```

| Concepto | Qué es | Cuándo usar |
|---|---|---|
| **`sections`** | Tab en el Toolbar superior. Click cambia el `ActivePage` | SIEMPRE. Es el entry point visual al plugin |
| **`sidebar`** | Entrada en sidebar lateral (reservado para futuro router) | Declarar por consistencia; hoy no se renderiza |
| **`dashboard_widgets`** | Widget embebido en otra page (reservado) | NO usar hoy; el campo está para futuro |

**`key` de la section es lo que el shell usa para identificar la page.**
Por convención: lowercase, sin guión medio (igual que `plugin_id` pero
puede diferir — e.g. `agents_admin` tiene section `key: agents`).

**`order` define el orden en el Toolbar.** Convención de los plugins
actuales:
- 1: chats
- 2: orders
- 3: eta
- 4: catalog (upload)
- 5: agents (admin)

Si agregás plugin nuevo, elegí `order` entre los existentes o al final.
Si dos plugins tienen el mismo `order`, el shell rompe el tie
alfabéticamente por `key`.

---

## §5. Icon registry (spinal hasta plugin-local icons)

```typescript
// frontend_dashboard/src/shared/ui/Icon.tsx (extracto)
export const ICONS = {
  chat: ChatIcon,
  workflow: WorkflowIcon,
  pkg: PkgIcon,
  bot: BotIcon,           // fallback si el plugin pide un icon que no existe
  bolt: BoltIcon,
  // ...
} as const;

export type IconName = keyof typeof ICONS;
```

**Hasta que plugin-local icons sea deferido**, agregar icon nuevo
requiere editar `Icon.tsx`. Es **spinal** (declarar `wiring_intent`
`ts_object_entries_append` en task-result.yaml si tu task agrega icons).

### Convención si el plugin pide un icon que NO existe:

`Toolbar.tsx` fallback a `Icon.bot` con un `console.warn(...)`. NO rompe
el shell — solo se ve raro.

---

## §6. Tests del frontend del plugin

```typescript
// canonical — plugins/<id>/frontend/<Id>Section.test.tsx
import { render, screen } from "@testing-library/react";
import { <Id>Section } from "./<Id>Section";

test("renders main content always", () => {
  render(<<Id>Section showSidebar={false} showInspector={false} />);
  expect(screen.getByRole("main")).toBeInTheDocument();
});

test("renders sidebar when showSidebar is true", () => {
  render(<<Id>Section showSidebar showInspector={false} />);
  expect(screen.getByRole("complementary", { name: /sidebar/i })).toBeInTheDocument();
});

test("hides inspector when showInspector is false", () => {
  render(<<Id>Section showSidebar={false} showInspector={false} />);
  expect(screen.queryByRole("complementary", { name: /inspector/i })).not.toBeInTheDocument();
});
```

**Si el plugin consume entity hooks** (e.g. `useChats()` que hace fetch
real), envolvé en `QueryClientProvider` con `retry: false` (ver
`sections/05-frontend-fsd.md §8`).

---

## §7. Code splitting (lazy + Suspense)

Cada plugin se carga **lazy** automáticamente desde el registry generado:

```typescript
// auto-gen en plugin-registry.generated.ts
Page: lazy(() => import("@plugins/chats/frontend")),
```

El `Dashboard.tsx` envuelve la `ActivePage` en `<Suspense>` con un
fallback. Cuando el operador clickea la section, el bundle del plugin se
descarga en background.

**Implicancia para vos:** mantenete dentro del plugin dir. Si importás
algo de `@/entities/*` o `@/shared/*`, eso queda en el bundle principal
(es código shared, ya cargado). Si importás algo de `@plugins/<other>/*`
(prohibido por dep-cruiser), rompés el code splitting + el aislamiento.

---

## §8. Features internas del plugin (cross-feature OK)

Dentro de `plugins/<id>/frontend/features/`, las features pueden
importarse entre sí (relajación del FSD strict). Razón: el plugin es una
unidad lógica; sus features internas suelen tener cross-dependencies
naturales (un wizard llama a un modal de confirmación, etc.).

```
plugins/catalog/frontend/features/
├── upload-wizard/
│   ├── index.ts
│   └── ui/UploadWizard.tsx       # importa de upload-jobs/ — OK dentro del plugin
├── upload-jobs/
│   ├── index.ts
│   └── ui/UploadJobsList.tsx
└── upload-inspector/
    └── ui/UploadInspector.tsx    # importa de upload-jobs/ — OK
```

**Lo que sigue prohibido dentro del plugin:**

- Importar de `@plugins/<other>/*`.
- Importar de `@/features/*` (legacy, fuera del plugin).
- Deep imports: usar `@plugins/<id>/frontend/features/<x>` (barrel),
  NO `@plugins/<id>/frontend/features/<x>/ui/<Component>`.

---

## §9. Anti-patterns top-5 del frontend de un plugin

| # | Anti-pattern | Por qué mal | Qué hacer |
|---|---|---|---|
| 1 | `<Id>Section` sin `export default` | `lazy(() => import(...))` falla — registry roto | Agregar `export default <Id>Section` al fin del archivo |
| 2 | `import { X } from "@plugins/orders"` desde plugin `chats` | Cross-plugin — viola dep-cruiser | Mover X a `entities/` o `shared/ui/` |
| 3 | Component del shell hardcodeado (e.g. `<Toolbar />` dentro del plugin) | El plugin no orquesta el shell | El shell pasa props bandejón; el plugin renderiza su content |
| 4 | `fetch(...)` directo en Section | Viola FSD "no fetch in components" | Usar entity hook (`useX()`) |
| 5 | Icon nuevo agregado sin declarar wiring_intent | Conflict si 2 plugins agregan icons | Declarar `wiring_intent` `ts_object_entries_append` para `shared/ui/Icon.tsx` |

---

## §10. Próximo paso

| Si vas a… | Leé después |
|---|---|
| Saber qué archivos shared editás cuando tu plugin necesita algo cross-cutting | `sections/07-shared-files.md` |
| Ver ejemplos reales de plugin frontend-only | `examples/plugin-frontend-only.md` |
| Diagnosticar fallo `npm run test:arch` | `sections/08-tests-and-gates.md` |
| Si tu plugin tiene worker Temporal | `sections/03-backend-plugin.md` + `sections/04-backend-agents.md` |

---

**Fin sección 06.**
