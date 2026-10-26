/**
 * Dónde compran (pedido del operador, 2026-10-23): «saber la ciudad y zonas
 * donde están comprando por campaña, en cada campaña poder cotejar esos datos,
 * y una vista general de todas las compras».
 *
 * - `CampaignPurchasePlaces`: las compras de la fila activa de Ads por ciudad,
 *   cada ciudad con su peso en la campaña Y en todas las compras (el cotejo);
 *   al abrir una ciudad, sus barrios.
 * - `PurchasePlacesOverview`: todas las compras por ciudad y barrio, con las
 *   campañas que las trajeron (clic → abre esa campaña).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, within } from "@testing-library/react";

import type { PurchasePlaces } from "@plugins/ads/frontend/entities/ads-campaign";

const usePurchasePlacesMock = vi.fn();

vi.mock("@plugins/ads/frontend/entities/ads-campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePurchasePlaces: (...a: unknown[]) => usePurchasePlacesMock(...a),
}));

import { CampaignPurchasePlaces } from "./CampaignPurchasePlaces";
import { PurchasePlacesOverview } from "./PurchasePlacesOverview";

const PARAMS = { days: 30, from: null, to: null };

function places(over: Partial<PurchasePlaces> = {}): PurchasePlaces {
  return {
    orders: 4,
    paidOrders: 3,
    valueCop: 200000,
    withoutCity: 0,
    overallOrders: 20,
    ordersAvailable: true,
    truncated: false,
    cities: [
      {
        key: "bogota",
        city: "Bogotá",
        orders: 3,
        paidOrders: 2,
        valueCop: 150000,
        overallOrders: 5,
        zones: [
          { zone: "Chapinero", orders: 2, valueCop: 100000 },
          { zone: null, orders: 1, valueCop: 50000 },
        ],
        campaigns: [
          { id: "CMP_1", name: "Velitas diciembre", orders: 2, valueCop: 100000 },
          { id: "sin_conversacion", name: "Sin conversación · tienda web o pedido a mano", orders: 1, valueCop: 50000 },
        ],
      },
      {
        key: "cali",
        city: "Cali",
        orders: 1,
        paidOrders: 1,
        valueCop: 50000,
        overallOrders: 10,
        zones: [{ zone: "Granada", orders: 1, valueCop: 50000 }],
        campaigns: [{ id: "CMP_1", name: "Velitas diciembre", orders: 1, valueCop: 50000 }],
      },
    ],
    ...over,
  };
}

beforeEach(() => {
  usePurchasePlacesMock.mockReset();
  usePurchasePlacesMock.mockReturnValue({ data: places(), isLoading: false, isError: false });
});

describe("CampaignPurchasePlaces", () => {
  it("pide las compras de la fila activa de Ads (campaña + segmento)", () => {
    render(<CampaignPurchasePlaces params={PARAMS} campaignId="CMP_1" adsetId="AS_1" />);
    expect(usePurchasePlacesMock).toHaveBeenCalledWith(PARAMS, "CMP_1", "AS_1");
  });

  it("cada ciudad se coteja: su peso en la campaña y en todas las compras", () => {
    const { getByTestId } = render(
      <CampaignPurchasePlaces params={PARAMS} campaignId="CMP_1" adsetId={null} />,
    );
    const bogota = within(getByTestId("place-city-bogota"));
    expect(bogota.getByText("Bogotá")).toBeTruthy();
    // 3 de 4 compras de la campaña; 5 de 20 en total.
    expect(bogota.getByText("75 %")).toBeTruthy();
    expect(bogota.getByText("25 %")).toBeTruthy();
    expect(bogota.getByText(/3 compras/)).toBeTruthy();
  });

  it("al abrir una ciudad se ven sus barrios (y los pedidos sin barrio)", () => {
    const { getByRole, getByTestId, queryByText } = render(
      <CampaignPurchasePlaces params={PARAMS} campaignId="CMP_1" adsetId={null} />,
    );
    expect(queryByText("Chapinero")).toBeNull();

    fireEvent.click(getByRole("button", { name: /Bogotá/ }));

    const zones = within(getByTestId("place-zones-bogota"));
    expect(zones.getByText("Chapinero")).toBeTruthy();
    expect(zones.getByText("Sin barrio")).toBeTruthy();
  });

  it("avisa las compras sin ciudad en el pedido", () => {
    usePurchasePlacesMock.mockReturnValue({ data: places({ withoutCity: 2 }) });
    const { getByText } = render(
      <CampaignPurchasePlaces params={PARAMS} campaignId="CMP_1" adsetId={null} />,
    );
    expect(getByText(/Sin ciudad en el pedido: 2 compras/)).toBeTruthy();
  });

  it("si Orders no respondió lo dice, no muestra cero compras", () => {
    usePurchasePlacesMock.mockReturnValue({
      data: places({ ordersAvailable: false, orders: 0, cities: [] }),
    });
    const { getByText, queryByText } = render(
      <CampaignPurchasePlaces params={PARAMS} campaignId="CMP_1" adsetId={null} />,
    );
    expect(getByText(/Orders no respondió/)).toBeTruthy();
    expect(queryByText(/sin compras/i)).toBeNull();
  });

  it("sin compras en la ventana lo dice", () => {
    usePurchasePlacesMock.mockReturnValue({ data: places({ orders: 0, cities: [] }) });
    const { getByText } = render(
      <CampaignPurchasePlaces params={PARAMS} campaignId="CMP_1" adsetId={null} />,
    );
    expect(getByText(/sin compras/i)).toBeTruthy();
  });

  it("el botón de la vista general la abre", () => {
    const onOpenOverview = vi.fn();
    const { getByRole } = render(
      <CampaignPurchasePlaces
        params={PARAMS}
        campaignId="CMP_1"
        adsetId={null}
        onOpenOverview={onOpenOverview}
      />,
    );
    fireEvent.click(getByRole("button", { name: /todas las compras/i }));
    expect(onOpenOverview).toHaveBeenCalled();
  });
});

describe("PurchasePlacesOverview", () => {
  it("pide todas las compras de la ventana", () => {
    usePurchasePlacesMock.mockReturnValue({ data: places({ overallOrders: null }) });
    render(<PurchasePlacesOverview params={PARAMS} />);
    expect(usePurchasePlacesMock).toHaveBeenCalledWith(PARAMS, null);
  });

  it("muestra los totales y cada ciudad con las campañas que la trajeron", () => {
    usePurchasePlacesMock.mockReturnValue({ data: places({ overallOrders: null }) });
    const { getByTestId } = render(<PurchasePlacesOverview params={PARAMS} />);

    expect(within(getByTestId("places-total-orders")).getByText("4")).toBeTruthy();
    const bogota = within(getByTestId("place-city-bogota"));
    expect(bogota.getByText("Velitas diciembre")).toBeTruthy();
    expect(bogota.getByText(/Sin conversación/)).toBeTruthy();
  });

  it("una campaña de la lista de Ads se abre con un clic; las demás no son botón", () => {
    usePurchasePlacesMock.mockReturnValue({ data: places({ overallOrders: null }) });
    const onSelectCampaign = vi.fn();
    const { getByTestId } = render(
      <PurchasePlacesOverview
        params={PARAMS}
        onSelectCampaign={onSelectCampaign}
        isSelectable={(id) => id === "CMP_1"}
      />,
    );
    const bogota = within(getByTestId("place-city-bogota"));

    fireEvent.click(bogota.getByRole("button", { name: /Velitas diciembre/ }));

    expect(onSelectCampaign).toHaveBeenCalledWith("CMP_1");
    expect(bogota.queryByRole("button", { name: /Sin conversación/ })).toBeNull();
  });

  it("al abrir una ciudad se ven sus barrios", () => {
    usePurchasePlacesMock.mockReturnValue({ data: places({ overallOrders: null }) });
    const { getByRole, getByTestId } = render(<PurchasePlacesOverview params={PARAMS} />);

    fireEvent.click(getByRole("button", { name: /^Cali/ }));

    expect(within(getByTestId("place-zones-cali")).getByText("Granada")).toBeTruthy();
  });
});
