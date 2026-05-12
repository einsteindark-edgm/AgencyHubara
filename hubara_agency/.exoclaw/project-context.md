# Project context — AgencyHubara / hubara_agency

This file is read by every skill in the exoclaw pipeline (refiner, planner,
implementer, merger) to know the concrete layout of THIS project. It is copied
to `$ARTIFACTS_DIR/project-context.md` by each workflow's `cargar-*` node.

## Repo layout

- Repo root: `/Users/edgm/Documents/Projects/AgencyHubara/` (the directory that
  contains `.archon/`, `.claude/`, `hubara_agency/`, `exoclaw-temporal/`, etc.)
- This is a **uv workspace**. Members:
  - `hubara_agency/` ← agent code (the focus of this pipeline)
  - `exoclaw-temporal/` ← the framework (do NOT modify from this pipeline)
- The `uv.lock` is at REPO ROOT (workspace lock, single file for both members).

## Agent layout (multi-agent inside hubara_agency)

```
hubara_agency/
├── .exoclaw/
│   ├── refinements/<HU-id>-{original,tech}.md      ← refinar-hu persists here
│   ├── plans/<HU-id>/{plan-manifest.yaml,tareas/}  ← planificar-hu persists here
│   ├── results/<HU-id>/F<NN>-result.yaml           ← implementar-tarea persists here
│   ├── spinal-files.yaml                            ← convention (read by planner/merger)
│   └── project-context.md                           ← THIS FILE
├── pyproject.toml          ← agency-level pyproject (uv "package")
├── src/
│   ├── sales_whatsapp/     ← agent #1
│   │   ├── workflows/
│   │   ├── activities/
│   │   ├── tools/
│   │   ├── use_cases/
│   │   ├── workspace/{IDENTITY,SOUL,USER,TOOLS,AGENTS}.md
│   │   ├── worker.py            ← SPINAL
│   │   ├── composition.py       ← SPINAL
│   │   ├── contracts.py         ← SPINAL
│   │   ├── prompts.py           ← SPINAL
│   │   ├── state.py
│   │   └── parsers.py
│   ├── remarketing_whatsapp/   ← agent #2 (same shape)
│   ├── catalog_sync/           ← agent #3 (same shape)
│   ├── dashboard/              ← FastAPI dashboard (less relevant for DEHA pipeline)
│   └── platform/               ← cross-agent infrastructure
│       ├── temporal/{client,dispatcher,heartbeat,retry_policies,activities}.py
│       ├── whatsapp/{client,activities}.py
│       ├── catalog/{client,composition,dtos,errors,...}.py
│       ├── medusa/
│       ├── contracts.py        ← cross-agent DTOs, SPINAL
│       └── ...
└── tests/                      ← all tests under hubara_agency/tests/
    ├── conftest.py
    ├── sales_whatsapp/
    │   ├── tools/test_<tool>.py
    │   └── workspace/test_<...>.py
    ├── remarketing_whatsapp/
    ├── catalog_sync/
    ├── platform/{catalog,medusa,...}
    └── integration/
```

## Path conventions for skills

When you write paths in the refinement / plan / task / wiring_intents:

| Layer | Path FROM REPO ROOT | Python import |
|-------|---------------------|---------------|
| Tool | `hubara_agency/src/<agent>/tools/<concept>.py` | `from src.<agent>.tools.<concept> import ...` |
| Activity | `hubara_agency/src/<agent>/activities/<concept>.py` | `from src.<agent>.activities.<concept> import ...` |
| Workflow | `hubara_agency/src/<agent>/workflows/<concept>.py` | `from src.<agent>.workflows.<concept> import ...` |
| Use case | `hubara_agency/src/<agent>/use_cases/<concept>.py` | `from src.<agent>.use_cases.<concept> import ...` |
| DTO (agent) | `hubara_agency/src/<agent>/contracts.py` | `from src.<agent>.contracts import ...` |
| DTO (cross-agent) | `hubara_agency/src/platform/contracts.py` | `from src.platform.contracts import ...` |
| Workspace catalog | `hubara_agency/src/<agent>/workspace/TOOLS.md` | (markdown, no import) |
| Workspace persona | `hubara_agency/src/<agent>/workspace/{IDENTITY,SOUL,USER,AGENTS}.md` | (markdown) |
| Tool test | `hubara_agency/tests/<agent>/tools/test_<tool>.py` | (test module) |
| Workspace test | `hubara_agency/tests/<agent>/workspace/test_<...>.py` | (test module) |

**Import path trick**: Python imports use `from src.<agent>...` because
`hubara_agency/pyproject.toml` makes `hubara_agency/` the package root.
DO NOT write `from hubara_agency.src.<agent>...` — that would not resolve.

## Command conventions (CWD-sensitive)

The operator invokes `archon run <workflow>` from REPO ROOT. The workflow's
CWD inside the worktree is also REPO ROOT. But `uv` / `pytest` / `ruff` /
`mypy` need to run with CWD = `hubara_agency/` because:
  - `uv` resolves the project at the CWD (workspace members need explicit cd).
  - `pytest` discovers `tests/` relative to CWD and resolves `from src.X`
    imports via the pyproject at CWD.

ALWAYS prefix verification commands with `cd hubara_agency &&`:

```bash
# Correct
cd hubara_agency && uv run pytest tests/sales_whatsapp/tools/test_<tool>.py -xvs
cd hubara_agency && uv run ruff check src/sales_whatsapp/tools/
cd hubara_agency && uv run mypy src/sales_whatsapp/

# Wrong — will fail
uv run pytest hubara_agency/tests/sales_whatsapp/tools/test_<tool>.py -xvs
pytest hubara_agency/tests/...
```

## Available agents (for HU targeting)

- `sales_whatsapp`: WhatsApp sales agent (most mature, has all DEHA pieces).
- `remarketing_whatsapp`: WhatsApp remarketing follow-up.
- `catalog_sync`: Background catalog sync to Medusa.

When refining / planning a HU, identify the target agent in §0 of the
refinement. If the HU spans multiple agents (cross-agent feature), use
`src/platform/` for shared concerns.

## Existing pre-pipeline conventions (legacy)

The repo already has `hubara_agency/.exoclaw/refinements/` with files named
`01-foo-tech.md`, `02-bar-tech.md`, etc. (from a previous manual workflow).
The new pipeline uses `<HU-id>-tech.md` where `<HU-id>` is generated
(typically `HU-YYYYMMDD-<slug>`). Both naming styles coexist; do not
overwrite the old files.
