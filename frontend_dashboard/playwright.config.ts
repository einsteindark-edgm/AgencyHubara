/**
 * Playwright config — E2E browser tests for the dashboard.
 *
 * The pipeline (hu-frontend-pipeline.yaml) runs `npx playwright test` as part
 * of the gate. It expects:
 *   1. FastAPI backend running on a RANDOM free port (the pipeline picks one
 *      to avoid collisions when concurrent pipelines run in parallel) and
 *      exports it as `process.env.API_URL` and `VITE_API_URL`. The dev server
 *      below inherits `VITE_API_URL` from the shell, so the frontend's
 *      apiClient transparently points to the right backend. Tests that need
 *      to assert directly against the backend should use `process.env.API_URL`
 *      (with a localhost:8000 fallback for local dev).
 *   2. Vite dev server on localhost:5173 (Playwright's `webServer` config
 *      below starts/stops it automatically).
 *
 * The captured output of each task's playwright run lands in
 * `$ARTIFACTS_DIR/playwright-evidence-<TASK_ID>.log` and gets embedded in the
 * PR comment as functional-test evidence.
 *
 * Test files live in `e2e/<feature>/<slice>.spec.ts`. The implementer skill
 * writes at least one spec per task (per its DoD). See `e2e/example.spec.ts`
 * as the canonical template.
 *
 * Why we don't run against MSW-mocked backend by default:
 *   The operator chose "real FastAPI" so the E2E tests catch contract drift
 *   between frontend and backend (Zod schemas vs FastAPI Pydantic models). If
 *   a future feature needs hermetic isolation (e.g. failure-injection tests),
 *   set `process.env.MOCK_BACKEND=1` and use MSW inside the spec.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PORT ?? 5173);
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // dev server is single-user; parallel can race.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 1,
  workers: 1,
  // `--reporter=line` from the CLI overrides this; here we set HTML for local
  // dev so the operator gets a clickable report at `playwright-report/`.
  reporter: process.env.CI
    ? [["line"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE_URL,
    // Trace on retry — saves a `.zip` per failed test in `test-results/` that
    // playwright show-trace can replay. Useful evidence in failed PRs.
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Auto-start/stop the Vite dev server for the test run. The pipeline relies
  // on this — it does NOT pre-launch `npm run dev` separately.
  // The dev server itself proxies API calls to localhost:8000 (FastAPI), which
  // the pipeline starts in background BEFORE invoking playwright.
  webServer: {
    command: "npm run dev",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
