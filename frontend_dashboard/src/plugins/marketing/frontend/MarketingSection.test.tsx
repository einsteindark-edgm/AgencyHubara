/**
 * Composición de la Page de marketing (las features se stubean — acá se
 * testea el layout de 3 paneles + el empty state + el fallback de selección,
 * no los internals):
 *  - sin campañas → CTA de crear (la conexión POST se stubea)
 *  - con campañas y sin selección → cae a la primera (sidebar + builder +
 *    inspector montados)
 *  - selección via useSelection("marketing") — Pages sin props (F5).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render } from "@testing-library/react";

vi.mock("@/shared/sdk", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePluginHost: () => ({ showSidebar: true, showInspector: true }),
  useSelection: () => [null, selectionSet],
}));

const selectionSet = vi.fn();
const createMutate = vi.fn();
const useCampaignsMock = vi.fn(() => ({ data: [] as unknown[] }));

vi.mock("@plugins/marketing/frontend/entities/campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCampaigns: () => useCampaignsMock(),
  useCreateCampaign: () => ({ mutate: createMutate, isPending: false, error: null }),
}));

vi.mock("@plugins/marketing/frontend/features/campaigns-list", () => ({
  CampaignsList: (props: { selectedId: string | null }) => (
    <div data-testid="campaigns-list" data-selected={props.selectedId ?? ""} />
  ),
}));
vi.mock("@plugins/marketing/frontend/features/campaign-builder", () => ({
  CampaignBuilder: (props: { campaign: { id: string } }) => (
    <div data-testid="campaign-builder" data-campaign={props.campaign.id} />
  ),
}));
vi.mock("@plugins/marketing/frontend/features/campaign-inspector", () => ({
  CampaignInspector: () => <div data-testid="campaign-inspector" />,
}));

import { MarketingSection } from "./MarketingSection";

function makeCampaign(id: string) {
  return {
    id,
    name: "C",
    status: "draft",
    goal: "",
    percent: 0,
    couponCode: "",
    validUntil: "",
    productHandle: null,
    segments: [],
    message: { header: "", body: "", footer: "", cta: "" },
    templateName: "t",
    scheduleAtMs: null,
    createdAtMs: 1,
    updatedAtMs: 1,
    sentAtMs: null,
    sendResult: null,
    testSends: [],
  };
}

beforeEach(() => {
  createMutate.mockClear();
  useCampaignsMock.mockReturnValue({ data: [] });
});

describe("MarketingSection — empty state", () => {
  it("sin campañas: CTA de crear la primera", () => {
    const { getByRole, queryByTestId } = render(<MarketingSection />);
    const cta = getByRole("button", { name: /Crear la primera campaña/ });
    expect(queryByTestId("campaign-builder")).toBeNull();

    fireEvent.click(cta);
    expect(createMutate).toHaveBeenCalledTimes(1);
  });
});

describe("MarketingSection — composición de 3 paneles", () => {
  it("con campañas y sin selección cae a la primera", () => {
    useCampaignsMock.mockReturnValue({
      data: [makeCampaign("mkt-a"), makeCampaign("mkt-b")],
    });
    const { getByTestId } = render(<MarketingSection />);
    expect(getByTestId("campaigns-list").dataset.selected).toBe("mkt-a");
    expect(getByTestId("campaign-builder").dataset.campaign).toBe("mkt-a");
    expect(getByTestId("campaign-inspector")).toBeTruthy();
  });
});
