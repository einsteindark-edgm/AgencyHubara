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
    ├── entities/                        # Entities DEL plugin (post-F1-F8; antes src/entities/)
    │   └── <entity>/                    #   api.ts + contracts.ts + keys.ts + model.ts + index.ts
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
    ├── icons.tsx                        # (opcional) glifos NUEVOS del plugin → PLUGIN_ICONS (§5)
    └── styles.css                       # (opcional) tokens específicos del plugin
```

### Reglas críticas:

- **`index.ts` debe `export default`** — el registry generado usa
  `lazy(() => import("@plugins/<id>/frontend").then(assertPluginModule))`;
  `assertPluginModule` hace que `tsc` FALLE EN COMPILACIÓN si falta el default.
- **`<Id>Section.tsx`** es el componente que renderiza el shell — no
  tiene `<html>`, `<body>` ni router. Es un subtree React. NO recibe props
  (contrato PluginHost — ver §3).
- **Las entities del dominio viven ACÁ** (`frontend/entities/<x>/`), no en
  `src/entities/` central (que DEBE quedar vacío — gate P-11). Imports vía
  alias `@plugins/<id>/frontend/entities/<x>`, nunca `../../` ni `@/entities/`.
- **Cross-feature dentro del plugin OK** (relajación del FSD strict):
  `features/<a>/` PUEDE importar de `features/<b>/`.
- **Cross-plugin SIEMPRE prohibido**: `@plugins/chats/* ❌→ @plugins/orders/*`
  (incluye la entity de otro plugin — gate P-22; el dato cross-plugin va por
  cast declarado, PLUGIN_CONTRACT.md §5.3).

---

## §2. Barrel mínimo (`index.ts`)

```typescript
// canonical — plugins/<id>/frontend/index.ts
export { default, <Id>Section } from "./<Id>Section";
```

El `default` es lo que `lazy()` importa (y lo que `assertPluginModule`
verifica en compilación). El named export queda para uso interno o tests.
Ya no hay `<Id>SectionProps` que exportar — el Page no recibe props (§3).

---

## §3. Section component — contrato "props bandejón"

> **(post-refactor F1-F8)** El "props bandejón" (las 12 props
> `selectedChatId/setSelectedChatId/...`) **YA NO EXISTE** — el nombre de
> esta sección quedó por compatibilidad de anchors. El shell renderiza
> `<ActivePage />` SIN props, dentro de `<PluginHostProvider>`. El plugin
> lee el shell vía los hooks de `@/shared/lib`: `usePluginHost()` (chrome:
> `showSidebar`/`showInspector`) y `useSelection("<plugin-id>")`
> (selección persistente cross-sección).

### §3.1 Props bandejón (contrato canónico)

```typescript
// canonical — plugins/<id>/frontend/<Id>Section.tsx
import { usePluginHost, useSelection } from "@/shared/lib";

export function <Id>Section() {
  // Chrome global del shell (toggles del Toolbar).
  const { showSidebar, showInspector } = usePluginHost();
  // Selección persistente del plugin: clave = tu plugin id; el shell NO
  // conoce las claves. Segundo arg opcional = fallback inicial.
  const [selectedId, setSelectedId] = useSelection("<plugin-id>");

  return (
    <>
      {showSidebar && <aside className="sidebar"><SidebarContent onSelect={setSelectedId} /></aside>}
      <main className="main"><MainContent id={selectedId} /></main>
      {showInspector && <aside className="inspector"><InspectorContent id={selectedId} /></aside>}
    </>
  );
}

export default <Id>Section;
```

### §3.2 Por qué "props bandejón"

El contrato genérico PluginHost (que reemplazó al bandejón) permite:

- **Consistencia visual** — sidebar y inspector son iguales cross-plugin.
- **Toggling unificado** — el operador clickea "hide sidebar" en el
  Toolbar y TODOS los plugins responden uniforme.
- **Cero acoplamiento** — el plugin no necesita saber qué otros plugins
  existen ni cómo orquestar el shell, y el shell no conoce el estado de
  selección de ningún plugin (la clave del mapa la elige cada plugin).
- **Dashboard.tsx intocado** — un plugin nuevo con selección propia NO
  edita ningún archivo central: `useSelection("mi_plugin")` y listo.

Si tu plugin NO necesita sidebar o inspector, simplemente no leas esos
campos de `usePluginHost()`.

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

> **(post-refactor F1-F8)** Plugin-local icons YA LLEGÓ. Un glifo NUEVO
> ya NO se appendea a `shared/ui/Icon.tsx`: el plugin lo trae consigo.

```typescript
// canonical — plugins/<id>/frontend/icons.tsx
export const icons = {
  nombreDelGlifo: ComponenteSvg,   // mismo trato visual que Icon.tsx (stroke 1.6 / 16px / currentColor)
} as const;
```

`npm run plugins:sync` detecta el archivo y mergea esos glifos al export
`PLUGIN_ICONS` del registry generado. El Toolbar resuelve en orden:
contribuciones (`PLUGIN_ICONS`) → set base (`Icon.tsx`) → fallback bot.

`shared/ui/Icon.tsx` queda como **SET BASE compartido** — sigue listado
como spinal en `spinal-files.yaml` pero solo se edita para glifos
genuinamente compartidos cross-plugin (raro; ver
`sections/07-shared-files.md`). El gate **P-12** valida que todo icon
referenciado en un manifest exista en base ∪ contribuciones.

### Convención si el plugin pide un icon que NO existe:

`Toolbar.tsx` fallback a `Icon.bot`. NO rompe el shell — solo se ve raro
(y P-12 lo caza en `npm run test:arch` si vino de un manifest).

---

## §6. Tests del frontend del plugin

```typescript
// canonical — plugins/<id>/frontend/<Id>Section.test.tsx
import { render, screen } from "@testing-library/react";
import { PluginHostProvider, type PluginHostState } from "@/shared/lib";
import { <Id>Section } from "./<Id>Section";

// El Page no recibe props: el shell-state entra por PluginHostProvider.
function renderWithHost(overrides: Partial<PluginHostState> = {}) {
  const host: PluginHostState = {
    showSidebar: true,
    showInspector: true,
    selection: {},
    setSelection: vi.fn(),
    ...overrides,
  };
  return render(
    <PluginHostProvider value={host}>
      <<Id>Section />
    </PluginHostProvider>,
  );
}

test("renders main content always", () => {
  renderWithHost({ showSidebar: false, showInspector: false });
  expect(screen.getByRole("main")).toBeInTheDocument();
});

test("renders sidebar when showSidebar is true", () => {
  renderWithHost({ showInspector: false });
  expect(screen.getByRole("complementary", { name: /sidebar/i })).toBeInTheDocument();
});

test("hides inspector when showInspector is false", () => {
  renderWithHost({ showSidebar: false, showInspector: false });
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
Page: lazy(() => import("@plugins/chats/frontend").then(assertPluginModule)),
```

El `Dashboard.tsx` envuelve la `ActivePage` en `<Suspense>` con un
fallback. Cuando el operador clickea la section, el bundle del plugin se
descarga en background. `assertPluginModule` verifica EN COMPILACIÓN que
el entry default-exporte el componente Page.

**Implicancia para vos:** mantenete dentro del plugin dir. Si importás
algo de `@/shared/*`, eso queda en el bundle principal (es código shared,
ya cargado); tus entities (`@plugins/<id>/frontend/entities/*`) viajan en
TU bundle. Si importás algo de `@plugins/<other>/*` (prohibido por
dep-cruiser + P-22), rompés el code splitting + el aislamiento.

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
| 1 | `<Id>Section` sin `export default` | `assertPluginModule` rompe `tsc` — registry roto en compilación | Agregar `export default <Id>Section` al fin del archivo |
| 2 | `import { X } from "@plugins/orders"` desde plugin `chats` | Cross-plugin — viola dep-cruiser + P-22 | UI genérica → `shared/ui/`; dato de otro plugin → cast declarado + entity LOCAL (PLUGIN_CONTRACT.md §5.3) |
| 3 | Component del shell hardcodeado (e.g. `<Toolbar />` dentro del plugin) | El plugin no orquesta el shell | El plugin lee `usePluginHost()`/`useSelection()`; el shell orquesta |
| 4 | `fetch(...)` directo en Section | Viola FSD "no fetch in components" | Usar entity hook (`useX()` de `frontend/entities/<x>`) |
| 5 | Glifo nuevo appendeado a `shared/ui/Icon.tsx` | Icon.tsx es el SET BASE compartido, no el registry para glifos de UN plugin | Traerlo en `frontend/icons.tsx` del plugin (`export const icons = {...}`) + `npm run plugins:sync` (P-12) |

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
