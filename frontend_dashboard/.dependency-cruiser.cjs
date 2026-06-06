/**
 * dependency-cruiser — FSD architectural contracts for frontend_dashboard.
 *
 * Run from frontend_dashboard/:  npm run arch:cruise
 * Also invoked from src/test/architecture/test_dep_cruiser.arch.test.ts as
 * part of `npm run test:arch`.
 *
 * Rule index ↔ contract:
 *   #1 shared-no-internal-imports     — src/shared must not import from src/* siblings
 *   #2 entities-layering              — entities/<a> only imports shared/ + other entities/
 *   #3 features-layering              — features/<a> never imports features/<b>, pages/, app/
 *   #4 pages-layering                 — pages/ never imports app/ or other pages
 *   #5 barrel-only                    — no deep imports past index.ts (e.g. @/features/x/ui/Y)
 *   #6 no-cross-layer-relative        — no '../../../' relative imports
 *
 * Notes:
 *   - `frontend-feature-sliced` is the canonical reference; this file mirrors it.
 *   - Test files (*.test.ts/.test.tsx) inside slices are co-located by FSD convention
 *     and are EXEMPTED from the barrel-only rule (they import from sibling files
 *     directly to test internals).
 */

/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    // ------------------------------------------------------------------------
    // #1 — shared/ owns no internal dependency. It is the floor of FSD.
    // ------------------------------------------------------------------------
    {
      name: "shared-no-internal-imports",
      severity: "error",
      comment:
        "src/shared/* must not import from src/{app,pages,features,entities}. " +
        "It is the floor of the layering. Lift only generic, domain-free code into shared/.",
      from: { path: "^src/shared/" },
      to: {
        path: "^src/(app|pages|features|entities)/",
      },
    },

    // ------------------------------------------------------------------------
    // #2 — entities/<a> can import shared/ and OTHER entities/, nothing above.
    // ------------------------------------------------------------------------
    {
      name: "entities-no-features-pages-app",
      severity: "error",
      comment:
        "src/entities/* must not import from src/{features,pages,app}. " +
        "Entities are the domain layer; features consume them, never the other way around.",
      from: { path: "^src/entities/" },
      to: {
        path: "^src/(features|pages|app)/",
      },
    },

    // ------------------------------------------------------------------------
    // #3 — features/<a> cannot import features/<b>, pages, or app.
    // ------------------------------------------------------------------------
    {
      name: "features-no-cross-feature",
      severity: "error",
      comment:
        "src/features/<a>/* must not import from src/features/<b>/*. " +
        "If two features need shared logic, lift it to entities/ or shared/.",
      from: { path: "^src/features/([^/]+)/" },
      to: {
        path: "^src/features/(?!\\1)([^/]+)/",
        pathNot: "^src/features/\\1(/|$)",
      },
    },
    {
      name: "features-no-pages-app",
      severity: "error",
      comment:
        "src/features/* must not import from src/pages or src/app. " +
        "Features are reusable across pages; depending on pages/app inverts the arrow.",
      from: { path: "^src/features/" },
      to: {
        path: "^src/(pages|app)/",
      },
    },

    // ------------------------------------------------------------------------
    // PR2 — plugin-internal isolation. Features dentro de un mismo plugin
    // pueden compartir entre sí (relajación del FSD strict). Pero NO se permite:
    //   - Cross-plugin: @plugins/chats → @plugins/orders
    //   - Plugin → src/{pages,app,features}: el plugin debe ser autónomo
    //   - Features sueltas → @plugins/<id>: solo el shell (pages) consume plugins
    // ------------------------------------------------------------------------
    {
      name: "plugins-no-cross-plugin",
      severity: "error",
      comment:
        "src/plugins/<a>/* must not import from src/plugins/<b>/*. " +
        "Cross-plugin coupling rompe la regla R3 (cada plugin es autónomo).",
      from: { path: "^src/plugins/([^/]+)/" },
      to: {
        path: "^src/plugins/(?!\\1)([^/]+)/",
      },
    },
    {
      name: "plugins-no-pages-app",
      severity: "error",
      comment:
        "src/plugins/<id>/* must not import from src/pages or src/app. " +
        "Los plugins son consumidos por el shell (pages), no al revés.",
      from: { path: "^src/plugins/" },
      to: {
        path: "^src/(pages|app)/",
      },
    },
    {
      name: "features-no-plugins",
      severity: "error",
      comment:
        "src/features/* must not import from src/plugins/*. " +
        "Solo el shell (src/pages, src/app) puede consumir plugins.",
      from: { path: "^src/features/" },
      to: {
        path: "^src/plugins/",
      },
    },
    {
      // PLUGIN_CONTRACT.md P-FECROSS — cierra el gap F10 de la auditoría
      // (PLUGIN_ISOLATION_AUDIT.md §2-F10). Inverso de features-no-plugins.
      name: "plugins-no-features",
      severity: "error",
      comment:
        "src/plugins/<id>/* must not import from src/features/* (la capa features " +
        "compartida). Un plugin es autónomo: usa @/shared + sus propias entities. " +
        "Acoplarse a un feature compartido rompe su portabilidad por-tenant.",
      from: { path: "^src/plugins/" },
      to: {
        path: "^src/features/",
      },
    },

    // ------------------------------------------------------------------------
    // #4 — pages/ cannot import app/ or other pages.
    // ------------------------------------------------------------------------
    {
      name: "pages-no-app-or-cross-page",
      severity: "error",
      comment:
        "src/pages/<X>.tsx must not import src/app/* or src/pages/<Y>.tsx. " +
        "Pages compose features; cross-page navigation goes through the router (not direct imports). " +
        "Excepción: el registry generado de plugins (`src/app/plugin-registry.generated.ts`) " +
        "es consumido por el shell — es un artefacto autogenerado, no parte de `app/`.",
      from: { path: "^src/pages/" },
      to: {
        path: "^src/app/",
        pathNot: "^src/app/plugin-registry\\.generated\\.ts$",
      },
    },

    // ------------------------------------------------------------------------
    // #5 — barrel-only public API. Deep imports past index.ts are forbidden.
    //      Test files inside slices are exempted (they probe internals legitimately).
    // ------------------------------------------------------------------------
    {
      name: "no-deep-import-entities",
      severity: "error",
      comment:
        "Importing @/entities/<x>/<subpath> directly bypasses the barrel. " +
        "Always import through @/entities/<x> (the index.ts). " +
        "Exception: test files co-located inside the same slice may import siblings directly.",
      from: {
        path: "^src/",
        pathNot: [
          "^src/entities/[^/]+/",
          "\\.test\\.(ts|tsx)$",
        ],
      },
      to: {
        path: "^src/entities/[^/]+/(?!index\\.ts$).+",
      },
    },
    {
      name: "no-deep-import-features",
      severity: "error",
      comment:
        "Importing @/features/<x>/<subpath> directly bypasses the barrel. " +
        "Always import through @/features/<x>. " +
        "Exception: files inside the same feature slice (and tests) may import siblings.",
      from: {
        path: "^src/",
        pathNot: [
          "^src/features/[^/]+/",
          "\\.test\\.(ts|tsx)$",
        ],
      },
      to: {
        path: "^src/features/[^/]+/(?!index\\.ts$).+",
      },
    },

    // ------------------------------------------------------------------------
    // #6 — no relative imports that cross layer boundaries (use @/ alias).
    //      Relative imports inside a slice (e.g. ./SearchBar from SessionList) are fine.
    // ------------------------------------------------------------------------
    {
      name: "no-relative-cross-layer",
      severity: "error",
      comment:
        "Relative imports with '../../' typically cross layer boundaries. " +
        "Cross-layer imports must use the @/ alias (so the boundary is visible).",
      from: { path: "^src/" },
      to: {
        path: "^\\.\\./\\.\\./",
        pathNot: "^src/",
      },
    },
  ],

  options: {
    doNotFollow: {
      // `plugin-registry.generated.ts` es un archivo autogenerado que importa
      // dinámicamente desde múltiples plugins. Excluirlo evita falsos positivos
      // en las reglas FSD (ej. "shell importa features"). Ver
      // PLUGIN_REFACTOR_PLAN.md §3 PR1.
      path: [
        "node_modules",
        "dist",
        "src-tauri",
        "src/app/plugin-registry.generated.ts",
      ],
    },
    tsConfig: {
      fileName: "tsconfig.app.json",
    },
    tsPreCompilationDeps: true,
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default"],
      mainFields: ["module", "main", "types", "typings"],
    },
    reporterOptions: {
      text: {
        highlightFocused: true,
      },
    },
  },
};
