import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Visual + functional E2E for the Agents section (HU-194116: dashboard → datos
 * reales del backend). Proves end-to-end that AgentsSection renders the agents
 * served by GET /api/agents_admin and swaps the workspace prompts on select.
 *
 * Hermetic by design: stubs /api/agents_admin via page.route so the spec is
 * deterministic and needs no live FastAPI (playwright.config documents this as
 * the in-spec isolation path). Screenshots are written to
 * $ARTIFACTS_DIR/visual-evidence/ — exactly where hubara-evaluate counts them —
 * so final-validation's playwright run produces the visual evidence the
 * evaluator's visual_verification gate requires (local runs fall back to
 * playwright-report/visual-evidence/).
 */
const EVIDENCE_DIR = path.join(
  process.env.ARTIFACTS_DIR ?? path.join(process.cwd(), "playwright-report"),
  "visual-evidence",
);

const AGENTS_FIXTURE = [
  {
    id: "chats:sales",
    plugin_id: "chats",
    worker_name: "sales",
    name: "Ventas Velas",
    role: "Asesor de ventas",
    workspace: {
      identity: "E2E_IDENTITY_SALES · asesor de ventas de velas artesanales.",
      soul: "Valoro la calidez y la honestidad.",
      tools: "Catálogo, base de conocimiento y links de pago.",
      agents: "Coordino vía handoff con el agente de triage.",
      users: "Clientes B2C que valoran el trato cercano.",
      skills: [],
    },
  },
  {
    id: "chats:remarketing",
    plugin_id: "chats",
    worker_name: "remarketing",
    name: "Reactivación Leads",
    role: "Reactivación de leads tibios",
    workspace: {
      identity: "E2E_IDENTITY_REMARKETING · reactivo leads tibios y abandonos.",
      soul: "Persistente pero siempre respetuoso.",
      tools: "Plantillas de mensaje y links de reactivación.",
      agents: "Coordino con el agente de ventas.",
      users: "Leads que abandonaron el carrito.",
      skills: [],
    },
  },
];

test.describe("Agents section — datos reales del backend", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/agents_admin", async (route) => {
      await route.fulfill({ json: AGENTS_FIXTURE });
    });
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  });

  test("lista agentes del backend y muestra sus prompts al seleccionar", async ({
    page,
  }) => {
    await page.goto("/");

    // Navegar a la sección Agents (segmented control del Toolbar, role=tab).
    await page.getByRole("tab", { name: "Agents" }).click();

    const sidebar = page.locator(".sidebar");
    const canvas = page.locator(".ag-canvas");

    // La lista muestra AMBOS agentes provenientes del backend stubbeado.
    await expect(sidebar.getByText("Ventas Velas")).toBeVisible();
    await expect(sidebar.getByText("Reactivación Leads")).toBeVisible();

    // El panel de prompts muestra el workspace REAL del primer agente.
    await expect(canvas.getByText(/E2E_IDENTITY_SALES/)).toBeVisible();

    await page.screenshot({
      path: path.join(EVIDENCE_DIR, "agents-section-sales.png"),
      fullPage: true,
    });

    // Seleccionar el segundo agente intercambia el workspace renderizado.
    await sidebar.getByText("Reactivación Leads").click();
    await expect(canvas.getByText(/E2E_IDENTITY_REMARKETING/)).toBeVisible();
    await expect(canvas.getByText(/E2E_IDENTITY_SALES/)).toHaveCount(0);

    await page.screenshot({
      path: path.join(EVIDENCE_DIR, "agents-section-remarketing.png"),
      fullPage: true,
    });
  });
});
