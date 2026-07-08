# Edit mode — conectar sin salir de VS Code

En un scope **system** o **focus** de GraphAgents (no en workspace, no en
System Map — ahí el canvas es solo lectura) el toolbar muestra
`✎ edit mode`:

- **Arrastrá** de un agente a una tool o a otro agente para conectarlos.
  VS Code te muestra el binding sugerido y pedís confirmación antes de que
  se escriba nada.
- **Click derecho en una arista** (`uses`/`agent`, no en `consumes`/costuras)
  para desconectarla.
- **"+ Conectar desde…"** en el árbol Catálogo abre el mismo flujo desde un
  picker, sin tocar el canvas.

Cada conexión pasa por el mismo gate que corre en CI — si algo rompe, el
manifest vuelve byte-idéntico al original, nunca queda a medio escribir.

Cuando termines: **Save Production** guarda el snapshot; la status bar
inferior muestra `dirty`/`saved`. **Publish Production** hace commit + push
+ abre un PR — pide confirmación explícita antes de tocar git.
