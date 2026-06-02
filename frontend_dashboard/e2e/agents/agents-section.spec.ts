import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Visual + functional E2E de la sección Agents (datos reales del backend).
 * Prueba end-to-end que AgentsSection renderiza los agentes servidos por
 * GET /api/agents y que al seleccionar otro agente intercambia los prompts
 * (el contenido REAL de sus .md de workspace).
 *
 * Hermético: stubea GET /api/agents vía page.route, así el spec es
 * determinista y no necesita una FastAPI viva. Los screenshots se escriben a
 * $ARTIFACTS_DIR/visual-evidence/ (o playwright-report/visual-evidence/ en
 * local) — donde hubara-evaluate cuenta la evidencia visual.
 */
const EVIDENCE_DIR = path.join(
  process.env.ARTIFACTS_DIR ?? path.join(process.cwd(), "playwright-report"),
  "visual-evidence",
);

function promptSet(tag: string) {
  return [
    { key: "agents", filename: "AGENTS.md", content: `E2E_AGENTS_${tag} · coordino handoff.`, word_count: 4 },
    { key: "identity", filename: "IDENTITY.md", content: `E2E_IDENTITY_${tag} · así me presento.`, word_count: 5 },
    { key: "soul", filename: "SOUL.md", content: `E2E_SOUL_${tag} · mis valores.`, word_count: 4 },
    { key: "tools", filename: "TOOLS.md", content: `E2E_TOOLS_${tag} · mis herramientas.`, word_count: 4 },
    { key: "users", filename: "USER.md", content: `E2E_USERS_${tag} · mi audiencia.`, word_count: 4 },
  ];
}

// Shape EXACTO de GET /api/agents (ver src/entities/agent/contracts.ts).
const AGENTS_RESPONSE = {
  agents: [
    {
      id: "sales",
      name: "Asesor de Ventas",
      role: "Ventas premium por WhatsApp",
      model: "DeepSeek V4 Pro",
      category: "Ventas",
      icon: "bolt",
      color: "blue",
      workspace: "hubara_agency/src/plugins/chats/agent/sales/workspace",
      capabilities: [{ label: "Buscar en el catálogo", icon: "notes" }],
      prompts: promptSet("SALES"),
    },
    {
      id: "remarketing",
      name: "Recuperación Comercial",
      role: "Reactivación de leads INTERESADO",
      model: "DeepSeek V4 Pro",
      category: "Remarketing",
      icon: "refresh",
      color: "orange",
      workspace: "hubara_agency/src/plugins/chats/agent/remarketing/workspace",
      capabilities: [{ label: "Transferir la charla a Ventas", icon: "user" }],
      prompts: promptSet("REMARKETING"),
    },
  ],
};

test.describe("Agents section — datos reales del backend", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/agents", async (route) => {
      await route.fulfill({ json: AGENTS_RESPONSE });
    });
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  });

  test("lista los agentes del backend y muestra sus prompts al seleccionar", async ({
    page,
  }) => {
    await page.goto("/");

    // Navegar a la sección Agents (segmented control del Toolbar, role=tab).
    await page.getByRole("tab", { name: "Agents" }).click();

    const sidebar = page.locator(".sidebar");
    const canvas = page.locator(".ag-canvas");

    // La lista muestra AMBOS agentes provenientes del backend stubbeado.
    await expect(sidebar.getByText("Asesor de Ventas")).toBeVisible();
    await expect(sidebar.getByText("Recuperación Comercial")).toBeVisible();

    // El panel muestra el workspace REAL del primer agente (default: sales).
    await expect(canvas.getByText(/E2E_IDENTITY_SALES/)).toBeVisible();

    await page.screenshot({
      path: path.join(EVIDENCE_DIR, "agents-section-sales.png"),
      fullPage: true,
    });

    // Seleccionar el segundo agente intercambia el workspace renderizado.
    await sidebar.getByText("Recuperación Comercial").click();
    await expect(canvas.getByText(/E2E_IDENTITY_REMARKETING/)).toBeVisible();
    await expect(canvas.getByText(/E2E_IDENTITY_SALES/)).toHaveCount(0);

    await page.screenshot({
      path: path.join(EVIDENCE_DIR, "agents-section-remarketing.png"),
      fullPage: true,
    });
  });
});
