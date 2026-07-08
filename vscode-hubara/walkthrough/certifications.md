# Certificaciones y el "compilador"

Cada plugin/agente/tool tiene un nivel **C0–C3** (el TCK). Se ve en tres
lugares a la vez:

- **Badge en el Explorer** (FileDecorations) sobre la carpeta del plugin o
  el manifest — rojo=C0, amarillo=C1, verde=C2/C3.
- **CodeLens** arriba de cada manifest — "⌂ Ver en grafo" y "✓ Check"
  (corre el compiler real y actualiza el Problems panel).
- **Problems panel** — guardar un manifest roto corre `check` y pinta el
  error exacto (`error[P-27]`, `error[G-BIND]`, etc.) con su archivo.

Los manifests (`plugin.yaml`, `*.agent.yaml`, `tool.yaml`) tienen
autocomplete + validación inline si tenés instalada la extensión
`redhat.vscode-yaml` (opcional).

[Ver el catálogo](command:workbench.view.extension.hubaraStudio)
