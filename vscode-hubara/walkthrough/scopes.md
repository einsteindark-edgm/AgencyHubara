# Scopes — project/target, estilo Xcode

El canvas tiene tres niveles, igual que elegir un scheme en Xcode:

- **Workspace** — los dos sistemas juntos (GraphAgents + System Map), cada
  uno como un cluster colapsable, con las costuras cross-sistema declaradas
  en `seams.yaml`.
- **System** — un solo sistema completo (solo GraphAgents, o solo System Map).
- **Focus** — un nodo + su vecindad a N saltos (el ego-graph). Doble-click en
  cualquier nodo del canvas baja a su focus; el breadcrumb de arriba sube de
  nuevo.

El scope activo y las posiciones que arrastres se guardan por separado —
volvés al workspace y no perdiste el layout que armaste en un focus.

```
Workspace ▸ GraphAgents ▸ agent:ctwa-report
```

[Abrir el grafo](command:acktos.openStudio)
