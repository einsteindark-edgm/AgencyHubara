# system_explorer

Visualizador del sistema AgencyHubara tipo n8n. Lee el grafo del backend
(`/api/system-map/graph`) y lo renderiza con React Flow.

## Stack

- React 19 + Vite 8 (alineado con `frontend_dashboard/`)
- `@xyflow/react` v12 — grafos con drag-and-drop + custom nodes JSX
- `elkjs` — auto-layout `layered` direction RIGHT
- `@tanstack/react-query` v5 — fetch + cache
- `zod` v4 — schema validation al boundary
- Tailwind v4 (CSS-based config)

## Run local (dev)

```bash
# Necesitás FastAPI corriendo en puerto 8000 (default):
cd hubara_agency && uv run python run_api.py

# En otra terminal:
cd system_explorer
npm install
npm run dev
# → http://localhost:5175
```

Override del backend target:
```bash
VITE_API_TARGET=http://192.168.1.50:8000 npm run dev
```

## Build prod

```bash
npm run build              # → dist/
npm run preview            # smoke local
```

## Docker

El service está declarado en `hubara_agency/docker-compose.base.yml` como
`system-explorer`. Para correr con el stack completo:

```bash
cd hubara_agency
python scripts/render-compose.py
docker compose -f docker-compose.local.yml up -d
# → http://localhost:5175
```

El container nginx proxea `/api/*` al service `api` del compose.

## Estructura

```
src/
├── api/
│   ├── client.ts          # fetch wrapper minimal
│   └── schemas.ts         # Zod schemas (mirror del backend contracts.py)
├── components/
│   ├── SystemMap.tsx      # ReactFlow canvas + estado
│   ├── Sidebar.tsx        # panel lateral: stats + plugins + orphans
│   └── nodes/
│       └── index.tsx      # custom node types (1 archivo por simplicidad)
├── hooks/
│   ├── useSystemGraph.ts  # TanStack Query → GET /api/system-map/graph
│   └── useLayoutPersist.ts # localStorage save/restore
├── layout/
│   └── elkLayout.ts       # ELK auto-layout (layered RIGHT)
├── styles/
│   └── index.css          # Tailwind + React Flow CSS overrides
├── App.tsx
└── main.tsx
```

## Cómo agregar un nuevo tipo de nodo

1. Backend: agregar `NodeKind` literal en
   `hubara_agency/src/plugins/system_map/domain/contracts.py`.
2. Backend: extender el builder para detectarlo.
3. Frontend: actualizar `src/api/schemas.ts` con el nuevo enum value.
4. Frontend: agregar un componente en `src/components/nodes/index.tsx` y
   registrarlo en `nodeTypes`.
5. Opcionalmente: tamaño/altura en `src/layout/elkLayout.ts`.

## Orphan detection

El backend marca un nodo como `is_orphan=true` con `orphan_reason` en:
- `empty_plugin` — plugin sin frontend/api/agent.
- `section_without_sidebar` — section sin sidebar entry matching.
- `sidebar_without_section` — sidebar sin section matching.
- `worker_no_task_queue` — worker sin task_queue (schema bug).
- `api_router_no_prefix` — router sin prefix.
- `depends_on_missing` — plugin.depends_on apunta a plugin inexistente.

El frontend pinta el nodo con ring rojo + glow. El sidebar lista todos los
huérfanos clickeable → centra la vista en el nodo.

## V2 ideas (no V1)

- Tools/activities/workflows scan via import introspection.
- Edit manifest desde la UI (click nodo → form → PATCH → commit).
- Layout persistence en backend (compartir entre devs).
- Filtrado por plugin / kind / orphan.
- Search bar global.
- Export to PNG/SVG.
