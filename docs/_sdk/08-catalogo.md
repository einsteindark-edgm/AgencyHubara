# 08 · Certificación en el catálogo (system_map)

> Fase F-SDK-5 · Fuente: `src/plugins/system_map/domain/certification.py` · Endpoint: `GET /api/system-map/graph`

## Qué problema soluciona

El system-explorer mostraba estructura y orfandad, pero no CONFORMIDAD: un
plugin podía estar dibujado lindo en el grafo violando media docena de
reglas. Ahora el grafo lleva el **veredicto del TCK por plugin** — el
explorer se convierte en el catálogo con scorecard: "el lugar donde validás
tu plugin antes del PR".

## Cómo funciona

- `collect_certifications()` (dominio del system_map) corre
  `run_conformance` del SDK por cada plugin del grafo — **en vivo, por
  request**. Decisión deliberada: los checks son filesystem-only y baratos
  (ms/plugin), así el catálogo local NUNCA muestra un veredicto stale; el
  reporte JSON de `.hubara/certification/` sigue siendo el artefacto para
  CI/PRs.
- El payload del `GET /api/system-map/graph` gana `certifications[]`:

```json
{
  "plugin_id": "eta",
  "archetype": "notifier",
  "level": "C2",
  "fails": 0, "warns": 0,
  "failed_checks": [],            // cuarentena: [{code, detail}]
  "warning_checks": [],           // migraciones pendientes
  "sdk": "0.1.0", "git_sha": "dcd5e46", "generated_at": "..."
}
```

- **Tolerancia honesta**: si el testkit explota, el grafo sale SIN
  certificación + `certification_unavailable` en `warnings` (un bug del TCK
  no tumba el mapa). Si el artefacto de deploy no trae `tests/` (containers),
  P-27 se reporta `skip` con "se verifica en CI" — ni fail falso ni verde
  inventado.

## Cómo se usa

```bash
curl -s localhost:8000/api/system-map/graph | jq '.certifications[] | {plugin_id, level, warns}'
```

El frontend del explorer (`system_explorer/`, puerto 5175) ya recibe el
campo en su fetch actual.

## Siguiente paso anotado (UI del explorer)

El backend está completo; la UI (badges por nivel en los nodos, sección
**Cuarentena** en el Sidebar con `failed_checks` + su fix vía el catálogo de
diagnósticos, scorecard drawer) es el siguiente PR del explorer — los datos
ya viajan en el payload y el contrato Zod del explorer debe extenderse con
`certifications` (regla L-10: contrato del boundary en el MISMO cambio que
lo consuma).
