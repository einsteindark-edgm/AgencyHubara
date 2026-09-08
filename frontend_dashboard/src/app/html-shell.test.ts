/**
 * Guard — el shell HTML declara el idioma real y prohíbe la traducción
 * automática del navegador.
 *
 * Why
 * ---
 * Incidente 2026-09-08: la sección «chat» crasheaba recurrentemente con
 * `Failed to execute 'insertBefore' on 'Node': The node before which the new
 * node is to be inserted is not a child of this node` (visible traducido al
 * español en el ErrorBoundary — prueba de que la página estaba traducida).
 * Causa: `index.html` declaraba `lang="en"` con contenido en español, así que
 * Chrome ofrecía/aplicaba "traducir al español"; Google Translate reemplaza
 * los text nodes por `<font>` y React deja de ser dueño del DOM que
 * reconcilia → insertBefore/removeChild explotan en el primer re-render con
 * SSE. Solo un refresh (DOM limpio) lo destrababa.
 *
 * `lang="es"` quita el disparador y `translate="no"` + la meta `notranslate`
 * lo prohíben aunque el operador tenga "traducir siempre" activo.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// Vive en `app/` (el shell) y NO en `test/architecture/` — ese directorio es
// protegido por el meta-gate y este guard es de producto, no del contrato FSD.
// vitest corre desde frontend_dashboard/ (cwd); en jsdom import.meta.url no
// es file:// así que no sirve para resolver el path.
const html = readFileSync(join(process.cwd(), "index.html"), "utf-8");
const htmlTag = /<html\b[^>]*>/.exec(html)?.[0] ?? "";

describe("index.html — shell no traducible por el navegador", () => {
  it('declara lang="es" (la UI es en español)', () => {
    expect(htmlTag).toMatch(/\blang="es"/);
  });

  it('prohíbe la traducción automática con translate="no"', () => {
    expect(htmlTag).toMatch(/\btranslate="no"/);
  });

  it("incluye la meta google notranslate", () => {
    expect(html).toMatch(/<meta\s+name="google"\s+content="notranslate"\s*\/?>/);
  });
});
