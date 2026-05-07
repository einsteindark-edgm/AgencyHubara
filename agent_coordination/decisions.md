# Architecture Decision Records

## ADR-2026-05-06-01 — sales_whatsapp adopts the agent-runtime workspace layout

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: `sales_whatsapp` carries identity / tone / catalog / tag-taxonomy as raw strings under `shared_brain/{identity,instructions,knowledge}.md`, loaded via a custom `BrainLoaderPort` and shipped as `plugin_context: list[str]` through every workflow signal. The exoclaw-conversation runtime already supports the canonical workspace contract (`IDENTITY.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `AGENTS.md`, plus `skills/<name>/SKILL.md` with `exoclaw.always` frontmatter). Maintaining a parallel mechanism duplicates work and prevents the agent from using on-demand skills, hooks, or memory persistence.
- **Decision**: Migrate `sales_whatsapp` to the agent-runtime layout in four small PRs:
  1. **PR-A** — create `domains/sales_whatsapp/workspace/` from the plugin templates, bucket the shared_brain content into `IDENTITY.md` / `SOUL.md` / `TOOLS.md` / `AGENTS.md` and `skills/hubara_catalog/SKILL.md`. Wire `WorkspaceConfig(path=...)` from a per-domain `config/env.py` into `bootstrap_sales_session_activity` so it crosses the workflow boundary as a flat DTO (R-JSON). Keep `shared_brain/` and the `plugin_context` path live in parallel — diff of the resulting system prompt must be 0.
  2. **PR-B** — switchover. `DefaultConversation.create(workspace=Path(ws.path))` reads the new files; `plugin_context` is removed from `send_message` for identity/catalog. Regression test: system prompt diff = 0 vs PR-A.
  3. **PR-C** — rewrite `tools/routing.py` and `tools/tags.py` to inherit from `ToolBase`, drop `pydantic.Field` for params, implement `execute_with_context`. Move `ManageConversationTagTool` registration into the sales worker via `register_tool_extension`.
  4. **PR-D** — delete `shared_brain/`, `core/brains.py`, `core/infrastructure/brains/`, `core/ports/brain_loader.py`, and the brain-loader fields in `LoadOrStartSalesSession`.
- **Consequences**:
  - Identity / tone / catalog / tag policy live where the runtime expects them, unlocking on-demand skills and memory hooks.
  - `plugin_context` becomes optional for transient per-turn data only (A-MEM, retrieved snippets); identity and catalog stop riding through every signal.
  - One env var convention per agent runtime: `EXOCLAW_WORKSPACE_SALES` for sales, `EXOCLAW_WORKSPACE_REMARKETING` for remarketing. The two domains share a process but have independent workspaces because their identity files differ.
  - `plugin_context: list[str]` parameter in `send_message` survives PR-A unchanged; PR-B trims its semantics.
  - Each PR ships green tests; no Big Bang. PR-A introduces zero behavior change; PR-B is the hot swap.

## ADR-2026-05-06-02 — `hubara_catalog` skill is `exoclaw.always: true`

- **Date**: 2026-05-06
- **By**: deha-architect (per user instruction)
- **Context**: The catalog (12 SKUs + shipping/payment/policy fragments, 32 lines) is needed on virtually every turn for sales. Lazy-loading it via `load_skill("hubara_catalog")` would force an extra LLM round-trip per cold session and risks the model answering pricing questions before the skill is loaded.
- **Decision**: Mark `hubara_catalog` as `metadata.exoclaw.always = true` so its body is injected into the system prompt every turn.
- **Consequences**: ~32 lines of catalog stay in the system prompt every turn (minor token cost). Latency stays low. If the catalog grows past ~200 lines, revisit and split into always-on summary + load-on-demand details.

## ADR-2026-05-06-04 — sales_whatsapp PR-B switchover (workspace replaces shared_brain in system prompt)

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: PR-A wired `runtime_workspace_path` through the boundary but the activity ignored it — `build_workspace_config(session_id)` kept pointing the runtime at the per-session vault, and the legacy `plugin_context` (loaded from `shared_brain/{identity,knowledge,instructions}.md`) kept winning in `ContextBuilder`. The two sources of truth for identity coexisted.
- **Decision**: PR-B is the hot swap. (1) `bootstrap_sales_session_activity` now resolves `WorkspaceConfig(path=input.runtime_workspace_path)` and fails fast with `RuntimeError` if the field is missing — failfast surfaces composition-root miswires before they corrupt prompts. (2) `LoadOrStartSalesSession.execute` stops calling `_sales_brain_loader.load(...)` for the Sales path; it always passes `plugin_context=None` to `send_message` for that path. The Remarketing path still loads its own brain (out of scope for PR-B). (3) The `plugin_context: list[str] | None` parameter survives in the signal signature and `PendingMessage` to keep replay safe; PR-D audits whether to repurpose it for volatile per-turn data (A-MEM, retrieved snippets) or remove it entirely. (4) The per-session vault (`WORKSPACE_VAULT_DIR / session_id`) keeps owning `MessageHistoryStore` JSONL and `metadata.json` — only the workspace that `ContextBuilder` reads moves.
- **Consequences**:
  - Single source of truth for identity / tone / catalog: the workspace dir. `shared_brain/*.md` is now dead code on the Sales path (PR-D deletes it).
  - `hubara_catalog` skill (`exoclaw.always: true`) is automatically injected every turn via `SkillsLoader.get_always_skills()` — no `load_skill` round-trip needed.
  - Regression test `tests/test_workspace_system_prompt.py` instantiates `ContextBuilder` directly against the committed workspace and asserts that key tokens from each bootstrap file (IDENTITY/SOUL/USER/TOOLS/AGENTS) and the catalog skill all reach the system prompt.
  - `tests/test_load_or_start_sales_session.py` updated: Sales-path assertions now expect `args[2] is None` (was `["sales-brain"]`); the Remarketing-path test is unchanged.
  - Replay test stays at v2 — activity signature is identical (the body changed, not the shape). No fixture regeneration required for shape reasons.
  - Failfast in `bootstrap_sales_session_activity` adds a R-DET-friendly liveness probe: if a future PR adds a new caller that forgets to wire `runtime_workspace_path`, Temporal surfaces an `ApplicationError` on the bootstrap activity instead of producing an empty system prompt at the LLM activity.

## ADR-2026-05-06-05 — sales_whatsapp PR-C: tools comply with the exoclaw `Tool` Protocol

- **Date**: 2026-05-06
- **By**: activity-engineer
- **Context**: `TransferToSalesAgentTool` and `ManageConversationTagTool` declared `class Foo(Tool):` where `Tool` is a `runtime_checkable` `typing.Protocol` (no `__init__`, no Pydantic `__fields__`). They mixed `pydantic.Field(...)` declarations with that "base", which the Protocol does not understand. Their `execute(self, ctx: ToolContext = None, ...)` accepted `ctx` as a kwarg and tolerated `tag=None`/`resumen=None` with silent fallbacks (`if not isinstance(_tag, str): _tag = 'INTERESADO'`), masking validation errors that should have surfaced to the LLM. To make it work, `core/activities.py:execute_tool` injected `input.params["ctx"] = ctx` — contaminating `ExecuteToolInput.params: dict[str, Any]` (a JSON-serializable boundary DTO) with a non-serializable `ToolContext`. Separately, `core/registries.py` imported `ManageConversationTagTool` and registered it for **every** dominio in `get_base_tools_registry`, which is a DIP violation: `core/` knows about a tool from `domains/sales_whatsapp/`.
- **Decision**:
  1. Rewrite both tools to inherit from `exoclaw.agent.tools.ToolBase` (a real mixin that supplies `cast_params` / `validate_params` / `to_schema`). Drop Pydantic. `name` / `description` / `parameters` become plain class-level attributes containing a flat JSON schema.
  2. Implement `execute_with_context(self, ctx: ToolContext, **params)` instead of `execute(self, ctx=None, **kwargs)`. `ToolRegistry.execute` (`exoclaw/agent/tools/registry.py:102-105`) detects the method via `hasattr` and injects `ctx` as the first positional arg automatically.
  3. Move tag-taxonomy validation from runtime `if not isinstance(...)` defaults into the JSON schema (`enum: ["INTERESADO", "RECHAZO", "COMPRA_EXITOSA"]`, `minLength: 1`, `required: [...]`). Invalid params now return `Error: Invalid parameters ...` to the LLM via `ToolBase.validate_params`, instead of being silently rewritten.
  4. Remove the `input.params["ctx"] = ctx` hack from `core/activities.py:execute_tool`. The DTO is once again strictly JSON-serializable, matching `ExecuteToolInput`'s contract.
  5. Move `ManageConversationTagTool` registration out of `core/registries.py` and into `domains/sales_whatsapp/worker.py` via `register_tool_extension(...)`. `core/` now only registers the truly-cross-domain tools (`ReadFileTool`, `WriteFileTool`). Each domain's worker (composition root) wires its own tools at boot — DIP fix.
- **Consequences**:
  - Tools are now Protocol-compliant in the sense that the registry can dispatch them through both `cast_params` and `validate_params`. `isinstance(tool, Tool)` still returns `False` because the Protocol structurally requires an `execute()` method and our tools only implement `execute_with_context()`; this is by design — the registry's `hasattr` check picks the right path. Tests assert dispatch behavior, not Protocol `isinstance`.
  - The `ExecuteToolInput.params` DTO stays JSON-serializable end-to-end — no more `ToolContext` smuggled in via dict mutation. R-JSON is restored.
  - `core/registries.py` no longer imports anything from `src.domains.*`. R-DIP is restored.
  - Remarketing did **not** previously use `manage_conversation_tag` (its prompts never mention the tool); the legacy hardcoded registration leaked it but went unused. PR-C deactivates it for Remarketing without functional regression. If Remarketing ever wants this tool, add a parallel `register_tool_extension` call in `domains/remarketing_whatsapp/worker.py`.
  - Workspace files, brain loaders, and `shared_brain/` are untouched (PR-D scope).
  - The JSON envelope shapes (`transfer_decision: {session_id, target_route, summary}`, `schedule_remarketing: {session_id, motivo, delay_seconds}`) are byte-equivalent to the legacy ones — `core/workflow_helpers.py:_try_parse_decision_payload` keeps working without changes.
  - Tool tests reorganized: `tests/test_transfer_tool.py` (legacy ADR-001 inert-w.r.t.-Temporal asserts) is updated to use `execute_with_context`. New `tests/test_tools_protocol.py` adds 8 tests covering schema shape, registry dispatch, and validation rejection of invalid params (no-pydantic regression, missing-field rejection, invalid-enum rejection).

## ADR-2026-05-06-06 — sales_whatsapp PR-D cleanup (delete dead brain-loader path for Sales)

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: After PR-B made `bootstrap_sales_session_activity` consume `WorkspaceConfig(path=input.runtime_workspace_path)` and `LoadOrStartSalesSession.execute` stop calling `_sales_brain_loader.load(...)` for the Sales path, the `sales_brain_loader` / `sales_brain_dir` constructor params on `LoadOrStartSalesSession`, the `_SALES_BRAIN_DIR` constant in `composition.py`, and `domains/sales_whatsapp/shared_brain/{identity,knowledge,instructions}.md` were dead-on-arrival for Sales. Remarketing still uses its own `shared_brain/` and the shared `BrainLoaderPort` / `core/brains.py:load_brain` until its own DEHA migration lands.
- **Decision**:
  1. Delete `domains/sales_whatsapp/shared_brain/` (3 files). Nothing imports those paths anymore.
  2. Delete `sales_brain_loader: BrainLoaderPort` and `sales_brain_dir: Path` from `LoadOrStartSalesSession.__init__` and the matching attributes. Drop `_SALES_BRAIN_DIR` and `sales_brain_loader = DefaultBrainLoader()` from `composition.py`.
  3. Keep `core/brains.py`, `core/ports/brain_loader.py`, `core/infrastructure/brains/` intact — Remarketing still consumes them. Their cleanup belongs to a future "Remarketing DEHA migration" PR train.
  4. Keep `core/registries.py:build_workspace_config` — Remarketing's `bootstrap_remarketing_session_activity` still calls it. Sales no longer does.
  5. **`plugin_context` field** on `PendingMessage` and the third arg of `send_message` signal: **option (a) — keep, repurpose as turn-volatile data**. Documented in `core/workflow_helpers.py:PendingMessage` docstring that it carries A-MEM / retrieved snippets / motivos, NOT identity nor catalog. Sales path passes `None`; Remarketing path forwards `shared_brain/*.md` until its DEHA migration. Removing the field would require fixture v3 bump and signal-signature change for both workflows — not worth the disruption now and we keep flexibility for A-MEM.
  6. Fix the broken tests left by PR-A (`tests/test_bootstrap_sales_activity.py`): three tests now pass `SalesSessionInput(session_id=..., runtime_workspace_path=...)` instead of a raw string. Added a `failfast` test that exercises PR-B's `RuntimeError` when the path is missing.
- **Consequences**:
  - `sales_whatsapp` is fully on the DEHA agent-runtime layout: workspace/* is the single source of truth for identity / tone / catalog. The legacy "brain loader" is gone from the Sales path end-to-end (use case + composition root + filesystem).
  - `LoadOrStartSalesSession.__init__` is leaner (5 args -> 3). Sales-specific tests no longer need to pass a fake brain loader for Sales.
  - The `BrainLoaderPort` Protocol in `core/ports/` and `DefaultBrainLoader` in `core/infrastructure/brains/` survive but are now Remarketing-only. They become candidates for deletion when Remarketing's DEHA migration lands.
  - `plugin_context` semantics are documented in code: a turn-volatile slot, not an identity channel. Future A-MEM work can land in this slot without renaming.
  - Replay fixtures: shape unchanged (signal signature, activity input shape and `SalesSessionInput` field set are identical). v2 fixture stays valid. The only thing that may need regeneration is if the user's local v2 fixture was generated **before** PR-A landed — in that case the user runs `uv run python tests/fixtures/generate_fixtures.py` once.
  - File deletion (`git rm`) is a manual step the user must run from a shell — the agent doesn't have shell access in this environment. The list of paths is in the activity log.

## ADR-2026-05-06-07 — sales_whatsapp PR-E lean collapse (drop hexagonal sub-folders)

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: After PR-A → PR-D the `sales_whatsapp` domain (1.3K LoC) sat in a textbook hexagonal layout: `application/use_cases/`, `application/ports/`, `domain/policies/`, `infrastructure/storage/`. At this scale the layered folders cost more than they buy: each port has exactly one concrete adapter and exactly one fake in tests, `domain/policies/prompts.py` is a 22-line module, `application/ports/` adds 65 lines of `Protocol` declarations that Python's duck typing already covers, and the `service.py` legacy facade is 26 lines of dead back-compat. The "Standard layout" recommended by the deha-architect prompt is correct for greenfield agents and large codebases, but it's overkill for the current size of this domain. Following the no-Big-Bang rule, PR-E is the surgical collapse.
- **Decision**: Flatten the `sales_whatsapp` layout: `domain/policies/prompts.py` -> `prompts.py` (top-level). `infrastructure/storage/{filesystem_history,filesystem_metadata}_store.py` -> single `state.py`. `application/use_cases/*` -> `use_cases/*`. `application/ports/*` deleted (use cases type-hint the concrete `FilesystemMessageHistoryStore` / `FilesystemMetadataStore` directly; fakes pass via duck typing). `activities.py` (file) -> `activities/bootstrap_session.py` (folder, with re-export `__init__.py`) to leave room for one-module-per-activity growth. `service.py` neutered (dead since composition factory took over). The plugin's "Standard layout" recommendation will be revised in a follow-up PR to match this outcome — the lean collapse is the new north star for small-to-medium domains.
- **Consequences**:
  - Discoverability up: 8 top-level files instead of 5 + 4 nested sub-folders. New devs see the whole domain at a glance.
  - 86 lines deleted (Protocols + back-compat facade + `__init__.py` chains), 0 production behavior change.
  - `R-DIP` is preserved structurally: composition root (`composition.py`) remains the only place that knows both adapters and use cases. The use cases still take constructor-injected deps. Python duck typing is the new "port" — any object with `read(session_id)` / `write(session_id, data)` / `append_user_event(...)` is accepted.
  - `R-JSON`, `R-DET`, `R-STATELESS`, `R-HEARTBEAT` untouched (no workflow / activity body changes).
  - Test changes: only import-path updates. The test of fakes does not touch isinstance checks (was already duck-typed), so the suite stays at 82 passed.
  - File deletion is a manual `git rm` step (the agent has no shell access). Old paths are stubbed with deprecation docstrings until the user runs the rm. Python's import system picks the package (`activities/`) over the file (`activities.py`) when both coexist, so functional behavior is correct even pre-rm.
  - Trade-off accepted: when (if) the domain grows past ~3K LoC and a 2nd adapter for either store appears (e.g. S3, Redis), reintroduce the Protocol — that's a one-PR change. We're not paying upfront for a port we don't need.

## ADR-2026-05-06-09 — remarketing_whatsapp PR-B switchover (workspace replaces shared_brain in system prompt)

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: PR-A (ADR-2026-05-06-08) wired `runtime_workspace_path` through the boundary but the body of `bootstrap_remarketing_session_activity` ignored it — `build_workspace_config(session_id)` kept pointing the runtime at the per-session vault, and `_brain_cache` (loaded via `load_remarketing_brain_activity` from `shared_brain/{identity,knowledge,instructions}.md`) kept winning in `ContextBuilder`. The two sources of truth for identity coexisted in Remarketing — the same condition that PR-B/Sales ADR-2026-05-06-04 closed for the Sales path.
- **Decision**: PR-B is the hot swap for Remarketing. (1) `bootstrap_remarketing_session_activity` now resolves `WorkspaceConfig(path=input.runtime_workspace_path)` and fails fast with `RuntimeError` if the field is missing — failfast surfaces composition-root miswires before they corrupt prompts (analogous to ADR-2026-05-06-04 step 1). The `build_workspace_config` import is dropped from the Remarketing activity (Sales never used it; it remains in `core/registries.py` because Remarketing's per-session JSONL vault is still owned by the filesystem adapter, but the `bootstrap_remarketing_session_activity` no longer calls it). (2) `RemarketingSessionWorkflow` drops `_brain_cache`, the `_ensure_brain()` helper, the import of `load_remarketing_brain_activity`, and stops forwarding `shared_brain/*.md` through `plugin_context`. The two `await self._ensure_brain()` call sites become `plugin_context=None` (initial trigger pending message) and `fallback_plugin_context=None` (per-turn `run_agent_turn` invocation) — aligning Remarketing with the Sales path's behavior. (3) The worker drops `load_remarketing_brain_activity` from the activities registry list and from the import. The `@activity.defn` definition survives in `activities.py` until PR-D — by itself it is harmless to keep around, but it has no replay value once the workflow code stops scheduling it (the `Replayer` raises `NonDeterminismError` on shape change of the workflow code, regardless of whether the worker still registers the activity). (4) Replay fixture bumped from v2 to v3 because the workflow's activity sequence changed (one fewer activity event); `mock_load_remarketing_brain_activity` removed from `REMARKETING_ACTIVITIES`; fixture generator now passes `runtime_workspace_path` on the `RemarketingSessionInput` for shape symmetry with production. (5) Two new test files: `tests/test_workspace_system_prompt_remarketing.py` (8 regression tests modeled on the Sales version, locking in that identity/SOUL/USER/TOOLS/AGENTS/catalog tokens reach the system prompt via `ContextBuilder`); rewrite of `tests/test_bootstrap_remarketing_activity.py` (4 tests now passing the DTO with `runtime_workspace_path` plus the failfast probe).
- **Consequences**:
  - Single source of truth for identity / tone / catalog on the Remarketing path: the `workspace/` dir. `shared_brain/*.md` is now dead code on the Remarketing path (PR-D deletes it).
  - `hubara_catalog` skill (`exoclaw.always: true`) is automatically injected every turn via `SkillsLoader.get_always_skills()` — no `load_skill` round-trip needed (mirror of ADR-2026-05-06-02).
  - Replay fixtures: v3 bump required because workflow activity sequence shrank (no more `load_remarketing_brain_activity` event). User MUST run `uv run python tests/fixtures/generate_fixtures.py` to regenerate; otherwise `tests/test_replay_remarketing.py` fails with "fixture not found".
  - Production drain: in-flight `RemarketingSessionWorkflow` executions started against the v2 worker code (with `_brain_cache` + `load_remarketing_brain_activity` events in their history) cannot be replayed by v3 worker code. Operational hand-off: drain the Remarketing task queue (idle timeout is 24h) before deploying the new worker, OR run a versioned worker side-by-side.
  - `plugin_context` field on `PendingMessage` and the third arg of `send_message` signal: signature unchanged. Now both Sales and Remarketing paths pass `None`. Documented in `core/workflow_helpers.py:PendingMessage` as a turn-volatile slot for A-MEM / retrieved snippets — never identity nor catalog.
  - Failfast in `bootstrap_remarketing_session_activity` mirrors the R-DET-friendly liveness probe from Sales: if a future caller forgets to wire `runtime_workspace_path`, Temporal surfaces an `ApplicationError` on the bootstrap activity instead of producing an empty system prompt at the LLM activity.
  - The `@activity.defn load_remarketing_brain_activity` and `REMARKETING_BRAIN_DIR` constant survive in `activities.py` until PR-D. Their presence is no longer load-bearing for the running workflow — but removing them in PR-B would force a single-PR scope to also delete `core/brains.py`, `core/ports/brain_loader.py`, `core/infrastructure/brains/`, the `remarketing_brain_loader` field on `LoadOrStartSalesSession`, and `domains/remarketing_whatsapp/shared_brain/`. PR-D handles that cleanup as a single atomic operation.
  - `core/registries.py:build_workspace_config` is no longer called by Remarketing's bootstrap activity (Sales already stopped using it in PR-B/Sales). Audit in PR-D: if no other caller exists, delete.

## ADR-2026-05-06-08 — remarketing_whatsapp PR-A (workspace skeleton + DTO wiring)

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: `remarketing_whatsapp` still carries identity / tone / catalog / instructions as raw strings under `shared_brain/{identity,instructions,knowledge}.md`, loaded by `load_remarketing_brain_activity` and shipped as `plugin_context: list[str]` through every signal. The Sales path completed its DEHA workspace migration in PR-A → PR-E (ADR-2026-05-06-01/-04/-05/-06/-07). Symmetric work is needed for Remarketing: bucket the brain into the canonical workspace files, wire `EXOCLAW_WORKSPACE_REMARKETING` through a new `config/env.py`, and propagate `runtime_workspace_path` across the workflow boundary. PR-A mirrors Sales PR-A: zero behavior change — the new field is plumbed but unused; `shared_brain/` keeps winning. Remarketing's mission is *proactive recovery* (one-shot hook) — it does NOT close sales nor tag conversations. Sales handles the close + tagging via `manage_conversation_tag`. Hence Remarketing's `TOOLS.md` documents only `transfer_to_sales_agent`; `manage_conversation_tag` is intentionally absent.
- **Decision**:
  1. Create `domains/remarketing_whatsapp/workspace/` with `IDENTITY.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `AGENTS.md`, `memory/{MEMORY,HISTORY}.md`, and `skills/hubara_catalog/SKILL.md`. Bucket `shared_brain/identity.md` -> `IDENTITY.md`; "BREVEDAD EXTREMA" + "REGLAS DE FORMATO" + "no pidas perdón" -> `SOUL.md`; "ANÁLISIS HISTÓRICO" + "PROHIBICIÓN DE REDIRECCIÓN" + "PROHIBICIÓN ABSOLUTA DE DESCUENTOS" + "TRANSICIÓN AL AGENTE DE VENTAS" -> `AGENTS.md`; `knowledge.md` body -> `skills/hubara_catalog/SKILL.md`. The skill is marked `metadata: {"exoclaw": {"always": true}}` (ADR-2026-05-06-02 mirror — single-line inline JSON to avoid the line-by-line parser bug documented in agent-runtime.md:93-103).
  2. Add `domains/remarketing_whatsapp/config/{__init__,env}.py`. `get_workspace_path()` reads `EXOCLAW_WORKSPACE_REMARKETING`, defaults to `<repo>/hubara_agency/src/domains/remarketing_whatsapp/workspace/`. Symmetric with Sales (ADR-2026-05-06-03).
  3. Add `runtime_workspace_path: str | None = None` (last field, default `None` — replay safety) to `RemarketingSessionInput`.
  4. Bump `bootstrap_remarketing_session_activity` signature from `(session_id: str, motivo: str)` to `(input: RemarketingSessionInput)`. The **body** is unchanged: still `build_workspace_config(session_id)` per-sesion. The new `runtime_workspace_path` field is plumbed but unused. PR-B will be the switchover.
  5. Update the workflow call site (`workflows/remarketing.py`) to pass the input DTO instead of `args=[session_id, motivo]`. The signal signature, the body's logic, and the activity body are otherwise untouched.
  6. `dispatcher_activities.py:schedule_remarketing_workflow_activity` resolves `get_remarketing_workspace_path()` and forwards it on the `RemarketingSessionInput` it constructs.
  7. Bump replay fixture to `history_remarketing_session_v2.json`. Mock `bootstrap_remarketing_session_activity` mirrors the new signature.
  8. **Predicted breakage**: `tests/test_bootstrap_remarketing_activity.py` (3 tests) call the activity with `(session_id_str, motivo_str)` per the legacy signature. PR-A intentionally does NOT fix them — same handoff pattern Sales PR-A used (ADR-2026-05-06-01 step 1, fixed in PR-D / ADR-2026-05-06-06 step 6).
- **Consequences**:
  - `EXOCLAW_WORKSPACE_REMARKETING` joins `EXOCLAW_WORKSPACE_SALES` as the per-domain env var convention (ADR-2026-05-06-03).
  - System prompt diff = 0 vs pre-PR-A (the workspace files exist but `ContextBuilder` still reads the per-session vault built by `build_workspace_config`).
  - Remarketing's `TOOLS.md` documents only `transfer_to_sales_agent`. `manage_conversation_tag` is **NOT** registered, mentioned, nor surfaced — a deliberate scope decision: Remarketing's job is to open the door; Sales handles closure and tagging. If a future business need emerges for Remarketing to tag, it would be an explicit ADR + `register_tool_extension` call in `domains/remarketing_whatsapp/worker.py`.
  - `hubara_catalog` skill is `always: true` — same trade-off as Sales (ADR-2026-05-06-02). ~32 lines added to every Remarketing turn's system prompt (minor token cost, low latency).
  - Replay fixture v1 is invalidated; user must regenerate to v2 (`uv run python tests/fixtures/generate_fixtures.py`).
  - `tests/test_bootstrap_remarketing_activity.py` will fail with 3 broken tests (legacy positional args). This is expected breakage to be fixed in PR-B/PR-D — same pattern as Sales.
  - Preserved: signal signature on `RemarketingSessionWorkflow.send_message`, `plugin_context` plumbing through `run_agent_turn`, `shared_brain/` files, `core/brains.py`, `core/ports/brain_loader.py`. PR-D will delete them once PR-B switches `ContextBuilder` over.

## ADR-2026-05-06-10 — remarketing_whatsapp PR-D global cleanup (delete brain_loader machinery)

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: Tras PR-A/PR-B de Remarketing (ADR-2026-05-06-08/-09) y PR-D de Sales (ADR-2026-05-06-06), el legacy `BrainLoaderPort` + `DefaultBrainLoader` + `core/brains.py:load_brain` + `domains/remarketing_whatsapp/shared_brain/` + `@activity.defn load_remarketing_brain_activity` quedaron sin callers vivos en `src/`. El `LoadOrStartSalesSession.execute` aun cargaba `shared_brain/*.md` para la ruta Remarketing (ultimo consumer); el workflow de Remarketing ya ignoraba ese `plugin_context` (PR-B Remarketing pasa siempre `None` a `run_agent_turn`). Era hora de cerrar la ventana: borrar el codigo, alinear los tests, sin Big Bang.
- **Decision**:
  1. Neuterizar (con docstring deprecado, pendiente `git rm` por el usuario) los archivos del legacy brain machinery: `core/brains.py`, `core/ports/brain_loader.py`, `core/infrastructure/brains/__init__.py`, `core/infrastructure/brains/default_loader.py`, `core/infrastructure/adapters/brain_loader_adapter.py`, y `domains/remarketing_whatsapp/shared_brain/{identity,instructions,knowledge}.md`.
  2. Quitar el re-export de `BrainLoaderPort` de `core/ports/__init__.py` y de `DefaultBrainLoader` de `core/infrastructure/adapters/__init__.py`.
  3. Eliminar `remarketing_brain_loader: BrainLoaderPort` y `remarketing_brain_dir: Path` del `__init__` de `LoadOrStartSalesSession`. Reemplazar la llamada `self._remarketing_brain_loader.load(...)` por `plugin_context = None` — el workflow de Remarketing ahora se alinea con Sales (workspace canonico es la unica fuente de identidad).
  4. Drop del import de `DefaultBrainLoader` y la const `_REMARKETING_BRAIN_DIR` de `domains/sales_whatsapp/composition.py`. Los args correspondientes desaparecen del `LoadOrStartSalesSession(...)` en el composition root.
  5. Borrar la `@activity.defn load_remarketing_brain_activity` y el const `REMARKETING_BRAIN_DIR` de `domains/remarketing_whatsapp/activities.py`. Drop el import de `load_brain` del modulo. Worker comment refrescado.
  6. Actualizar `core/workflow_helpers.py:run_agent_turn` docstring (linea ~115): drop la mencion a `load_remarketing_brain_activity`. Aclarar que tanto Sales como Remarketing pasan `None` a `plugin_context` / `fallback_plugin_context`.
  7. **Mantener vivo** `core/registries.py:build_workspace_config` con docstring deprecado: tiene 2 callers estructurales en `core/infrastructure/adapters/tool_registry_adapter.py:DefaultToolRegistry` y `core/ports/tool_registry.py:ToolRegistryPort` (ambos son orphans en el sentido de que el runtime no los instancia, pero borrar la funcion sin tocar el Protocol/Adapter dejaria un import roto). Ese triad es candidato a borrar en una limpieza futura del Protocol/Adapter.
  8. Tests: borrar (neuterizar) `tests/test_load_brain_activity.py` (3 tests sobre la activity eliminada). Reescribir `tests/test_load_or_start_sales_session.py:_make_use_case` — drop `FakeBrainLoader`, drop los args `remarketing_brain_loader` / `remarketing_brain_dir`, drop el `Path` import. La assertion de la ruta Remarketing en test 3 ahora espera `args == ["vuelvo", None, None]` (pre-PR-D era `["vuelvo", None, ["rem-brain"]]`). El test 4 (`test_falls_back_to_sales_when_remarketing_handle_dead`) drop la verificacion `rem_loader.calls == [Path(...)]` que se hacia antes del fallback.
  9. **No tocar**: `core/registries.py:build_workspace_config` (vease 7), `tools/`, `contracts/`, `parsers/`, `prompts/`, `workflows/`. Out of scope.
- **Consequences**:
  - El repo queda libre de `BrainLoaderPort` / `DefaultBrainLoader` / `load_brain` / `shared_brain/` / `@activity.defn load_remarketing_brain_activity` callers vivos. Los archivos quedan neuterizados con docstring deprecado hasta que el usuario corra `git rm`.
  - `LoadOrStartSalesSession.__init__` baja de 5 args a 3 (+ default). Tests del use case ya no necesitan `FakeBrainLoader`.
  - Replay safety: la fixture `history_remarketing_session_v3.json` ya no contiene events `load_remarketing_brain_activity` (regenerada en PR-B). Los workflows v2 in-flight no son replayables con esta version del worker — la operacion debe drenar la queue de Remarketing antes de deployar (idle timeout 24h) o usar versioned worker.
  - `plugin_context` semantics: ambas rutas (Sales y Remarketing) pasan siempre `None`. El campo sobrevive en `PendingMessage`, `send_message` signal, y `run_agent_turn` por compatibilidad de signature/replay y como hueco para A-MEM.
  - Triad orphan documentado: `core/registries.py:build_workspace_config` + `core/infrastructure/adapters/tool_registry_adapter.py:DefaultToolRegistry` + `core/ports/tool_registry.py:ToolRegistryPort` quedan como dead code candidato para una limpieza futura. Ningun runtime caller los instancia; los workers/activities llaman a las funciones modulo-level de `core/registries.py` directo.
  - File deletion via `git rm` es el paso manual final del usuario:
    ```bash
    cd /Users/edgm/Documents/Projects/AgencyHubara
    git rm hubara_agency/src/core/brains.py
    git rm hubara_agency/src/core/ports/brain_loader.py
    git rm -r hubara_agency/src/core/infrastructure/brains/
    git rm hubara_agency/src/core/infrastructure/adapters/brain_loader_adapter.py
    git rm -r hubara_agency/src/domains/remarketing_whatsapp/shared_brain/
    git rm hubara_agency/tests/test_load_brain_activity.py
    ```
    Tras el `git rm`, los `__init__.py` actualizados de `core/ports/` y `core/infrastructure/adapters/` ya no exportan los simbolos eliminados — Python no fallara.

## ADR-2026-05-06-11 — remarketing_whatsapp PR-E lean collapse (drop hexagonal sub-folders)

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: Tras PR-A → PR-D de Remarketing (ADR-2026-05-06-08/-09/-10), el dominio quedo en un estado layout asimetrico vs `sales_whatsapp` (que ya completo su PR-E lean collapse en ADR-2026-05-06-07). `domain/policies/prompts.py` era un modulo de 18 lineas escondido tras dos sub-folders hexagonales, y `activities.py` era un file plano (vs `activities/` package en Sales). El sub-folder `domain/policies/` no aporta a esta escala — un solo modulo plano con la utilidad pura es mas claro de descubrir y testear; y un package `activities/` deja espacio para crecer con un modulo por activity sin tocar imports publicos. Symmetric work cierra la migracion DEHA del dominio entero.
- **Decision**: Mirror exacto del PR-E de Sales (ADR-2026-05-06-07) sobre Remarketing: (1) Mover `domain/policies/prompts.py` -> `prompts.py` (top-level) con docstring que cita el ADR. Neuterizar el archivo viejo + sus dos `__init__.py` (`domain/__init__.py`, `domain/policies/__init__.py`) con docstring deprecado pendientes de `git rm -rf domain/`. (2) Convertir `activities.py` (file) -> `activities/` (folder): nuevo `activities/__init__.py` re-exporta los dos simbolos publicos (`bootstrap_remarketing_session_activity`, `build_remarketing_trigger_activity`); el cuerpo se mueve a `activities/bootstrap_session.py`. Neuterizar el viejo `activities.py` con docstring deprecado (Python prioriza el package sobre el file cuando ambos coexisten — funciona pre-`git rm`). (3) Update import en `activities/bootstrap_session.py:33` al nuevo path (`from src.domains.remarketing_whatsapp.prompts import build_remarketing_trigger`). (4) Update tests/test_prompts.py:4 al nuevo path. (5) `tests/test_imports.py:19` no requiere cambio — solo hace `importlib.import_module("src.domains.remarketing_whatsapp.activities")` y el package sigue siendo importable.
- **Consequences**:
  - Layout simetrico con sales_whatsapp: ambos dominios tienen `prompts.py` top-level + `activities/` package. Nuevos contributors descubren el dominio entero de un vistazo.
  - 0 cambios productivos en behavior. R-DET / R-JSON / R-STATELESS / R-HEARTBEAT / R-DIP intactos: ningun workflow / activity body cambio shape, ningun import cruzado entre layers se introduce.
  - Tests esperan **87 passed** (igual que post-PR-D — PR-E es solo cambio de paths de imports, no agrega/quita tests). El unico test que cambia es `test_prompts.py:4` (path del import).
  - El `activities.py` viejo + `domain/` quedan neuterizados pero presentes hasta que el usuario corra `git rm`. El flujo es identico al de PR-E de Sales: el agente no tiene shell access, deja breadcrumbs, el usuario limpia.
  - Trade-off accepted: cuando (si) Remarketing crece a 2+ activities con responsabilidades dispares (e.g. una activity que no es bootstrap), el modulo `bootstrap_session.py` se split en 2+ archivos sin tocar imports publicos. Hoy no hay esa presion — `build_remarketing_trigger_activity` y `bootstrap_remarketing_session_activity` son tan livianas que viven juntas sin friccion.

## ADR-2026-05-06-03 — Per-domain workspace env var, not a global one

- **Date**: 2026-05-06
- **By**: deha-architect
- **Context**: The hubara_agency process bundles two agents (sales + remarketing) with different identity, soul, tools and skills. A single `EXOCLAW_WORKSPACE` env var would force both into the same workspace dir, losing the separation. The plugin's stock `env.py.tpl` assumes one agent per process; we need a deliberate variant.
- **Decision**: Each domain ships its own `config/env.py` with its own env var: `EXOCLAW_WORKSPACE_SALES` (default `<repo>/hubara_agency/src/domains/sales_whatsapp/workspace/`) and (later) `EXOCLAW_WORKSPACE_REMARKETING`. The composition root of each domain reads its own env var and instantiates `WorkspaceConfig` with that path.
- **Consequences**: Slightly more env vars to document. In exchange, the two agents stay independently configurable in production (e.g. each gets its own PVC). No risk of one agent's `MEMORY.md` writes leaking into the other.
