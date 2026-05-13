/**
 * Example E2E spec — the canonical template for feature E2E tests.
 *
 * Acts as both:
 *   (a) the "first ever working playwright test" so the pipeline gate has
 *       something to run while feature-1 is in flight, and
 *   (b) the canonical example for future feature tasks. Future tasks copy
 *       this file structure into `e2e/<feature>/<slice>.spec.ts`.
 *
 * What this spec verifies:
 *   - The app loads at the root path without crashing.
 *   - The page renders SOME content (not a blank screen).
 *   - The FastAPI backend at localhost:8000 is reachable (the dev server
 *     proxies API calls, so if /health returns 200 we know the integration
 *     point is wired).
 *
 * This is INTENTIONALLY minimal. Real feature specs assert:
 *   - User interaction (`page.click`, `page.fill`, `page.getByRole`)
 *   - Expected visible content after the interaction
 *   - Optionally: backend side-effect was triggered (call the FastAPI
 *     endpoint directly and assert the side-effect persisted).
 */
import { expect, test } from "@playwright/test";

test.describe("app smoke", () => {
  test("loads the root page without crashing", async ({ page }) => {
    await page.goto("/");
    // The root must produce SOMETHING — body shouldn't be empty.
    const bodyText = await page.locator("body").innerText();
    expect(bodyText.length).toBeGreaterThan(0);
  });

  test("backend liveness probe is reachable from the test environment", async ({ request }) => {
    // The pipeline starts FastAPI in background on a RANDOM port (to avoid
    // collisions when multiple pipelines run in parallel) and exports the
    // resolved URL as `API_URL`. The liveness probe is `GET /` (defined in
    // hubara_agency/src/main.py). Fallback to localhost:8000 for local dev
    // where the operator runs the backend manually on the default port.
    const apiUrl = process.env.API_URL ?? "http://localhost:8000";
    const response = await request.get(`${apiUrl}/`);
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ status: "ok" });
  });
});
