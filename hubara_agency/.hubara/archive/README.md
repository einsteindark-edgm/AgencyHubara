# Archive — memoria institucional de HUs shipped

Cada HU que pasa por el pipeline `hu-hubara-pipeline` deposita un
snapshot acá al cerrar el PR (nodo `archive-hu` → command
`hubara-archive-hu`).

## Estructura

```
hubara_agency/.hubara/archive/
├── README.md                                 (este archivo)
├── 2026-05-25-HU-discount-orders/            (ejemplo)
│   ├── README.md
│   ├── hu-refinada.md
│   ├── spec-deltas/
│   │   └── plugins/orders/spec.md
│   ├── premortem.yaml
│   ├── evaluation.yaml
│   ├── code-review-findings.yaml
│   ├── task-result.yaml
│   └── feature-plan-manifest.yaml
└── 2026-05-26-HU-...
```

## Cómo grepear

- Buscar HUs que tocaron orders: `grep -r "plugins/orders" hubara_agency/.hubara/archive/*/spec-deltas/`
- Buscar HUs canceladas por premortem complejo: `grep -l "blocked" hubara_agency/.hubara/archive/*/task-result.yaml`
- Buscar premortems históricos para calibración: `cat hubara_agency/.hubara/archive/*/premortem.yaml`

## Qué NO va acá

- Sessions runtime → `hubara_agency/.hubara/sessions/` (gitignored, efímero)
- Handoffs entre sesiones interrumpidas → `hubara_agency/.hubara/handoffs/` (gitignored)
- Progress narrative durante la implementación → `hubara_agency/.hubara/progress-log/` (gitignored)
- Refinements / plans / results WIP de HUs activas → `hubara_agency/.hubara/{refinements,plans,results}/<HU_ID>/` (estos se MUEVEN acá al archivar)

## Calibración del pipeline

El archive es el corpus de evidencia para tunear:
- **Premortem rubric** — qué tipo de fallos imaginamos vs cuáles llegaron a producción
- **Evaluator rubric** — scoring históricos vs outcomes reales
- **Code review specialists** — findings recurrentes que podrían formalizarse como linters

Ver `hubara_agency/.hubara/evaluator-calibration/README.md` y
`hubara_agency/.hubara/stress-test-protocol.md`.
