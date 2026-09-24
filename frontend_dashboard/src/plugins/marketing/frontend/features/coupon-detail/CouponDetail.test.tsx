/**
 * Detalle del cupón (panel central):
 *  - cabecera con estado + pausar/activar + borrar (solo borrador, dos pasos)
 *  - solo lectura con el motivo cuando no es gestionable (D7): sin pausar
 *  - "Unidades con descuento": filas producto → color → aroma filtradas por
 *    las listas del producto, vendidas/quedan, aviso de sobreventa,
 *    "Mostrar al cliente cuántas quedan", guardar y errores por fila (422)
 *  - registro de cambios (quién / qué / cuándo)
 * Las queries y mutaciones de la entity se stubean.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, within } from "@testing-library/react";

import { ApiError } from "@/shared/sdk";

import type { Coupon, CouponDetail as CouponDetailData } from "@plugins/marketing/frontend/entities/coupon";

const statusMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const deleteMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const unitsMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const detailMock = {
  data: undefined as CouponDetailData | undefined,
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/coupon", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCoupon: () => detailMock,
  useSetCouponStatus: () => statusMock,
  useDeleteCoupon: () => deleteMock,
  usePutCouponUnits: () => unitsMock,
  useCouponProducts: () => ({
    data: [
      { id: "prod_cubo", handle: "cubo-love", title: "Cubo Love", colors: ["Rojo", "Blanco"], aromas: [] },
      { id: "prod_buda", handle: "vela-buda", title: "Vela Buda", colors: [], aromas: ["Lavanda", "Vainilla"] },
      { id: "prod_otro", handle: "otro", title: "Otro producto", colors: [], aromas: [] },
    ],
    isPending: false,
  }),
}));

import { CouponDetail } from "./ui/CouponDetail";

function makeCoupon(over: Partial<Coupon> = {}): Coupon {
  return {
    promotionId: "promo_01",
    campaignId: "procamp_01",
    code: "AMOR27",
    campaignName: "AMOR Y AMISTAD 2026",
    percentage: 10,
    products: "all",
    startsOn: "2026-09-22",
    endsOn: "2026-09-27",
    endsOnLabel: "27 de septiembre",
    status: "active",
    state: "active",
    manageable: true,
    unmanageableReason: null,
    acceptsUnits: true,
    units: { total: 5, left: 3 },
    ...over,
  };
}

function makeDetail(over: Partial<CouponDetailData> = {}, coupon: Partial<Coupon> = {}): CouponDetailData {
  return {
    coupon: makeCoupon(coupon),
    units: {
      rows: [
        {
          id: "quota_01",
          productId: "prod_cubo",
          handle: "cubo-love",
          title: "Cubo Love",
          color: "Rojo",
          aroma: null,
          units: 5,
          sold: 2,
          unitsLeft: 3,
          oversold: false,
          createdBy: "operadora",
        },
      ],
      showUnitsLeft: true,
      unavailable: false,
    },
    changes: [
      {
        ts: "2026-09-23T15:00:00Z",
        actor: "operadora",
        action: "update",
        detail: { percentage: [10, 15] },
      },
    ],
    ...over,
  };
}

const renderForm = vi.fn((c: Coupon) => <div data-testid="coupon-form" data-code={c.code} />);

function renderDetail() {
  const onDeleted = vi.fn();
  return { ...render(<CouponDetail couponId="promo_01" renderForm={renderForm} onDeleted={onDeleted} />), onDeleted };
}

beforeEach(() => {
  for (const m of [statusMock, deleteMock, unitsMock]) {
    m.mutate.mockClear();
    m.error = null;
  }
  renderForm.mockClear();
  detailMock.data = makeDetail();
  detailMock.error = null;
});

describe("CouponDetail — cabecera", () => {
  it("muestra el código, el estado y el formulario del cupón", () => {
    const { getByRole, getByText, getByTestId } = renderDetail();
    expect(getByRole("heading", { name: "AMOR27" })).toBeTruthy();
    expect(getByText("Activo")).toBeTruthy();
    expect(getByTestId("coupon-form").dataset.code).toBe("AMOR27");
  });

  it("un cupón activo se pausa", () => {
    const { getByRole } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Pausar" }));
    expect(statusMock.mutate).toHaveBeenCalledWith("inactive");
  });

  it("un cupón pausado se activa", () => {
    detailMock.data = makeDetail({}, { status: "inactive", state: "paused" });
    const { getByRole } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Activar" }));
    expect(statusMock.mutate).toHaveBeenCalledWith("active");
  });

  it("solo un borrador se borra, con confirmación de dos pasos", () => {
    const { queryByRole, unmount } = renderDetail();
    expect(queryByRole("button", { name: "Borrar" })).toBeNull();
    unmount();

    detailMock.data = makeDetail({}, { status: "draft", state: "draft" });
    const { getByRole, getByText } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Borrar" }));
    expect(deleteMock.mutate).not.toHaveBeenCalled();
    expect(getByText(/¿Borrar el cupón AMOR27\?/)).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Confirmar borrado" }));
    expect(deleteMock.mutate).toHaveBeenCalledTimes(1);
  });

  it("el 409 de borrar (tiene ventas) se muestra con su mensaje", () => {
    detailMock.data = makeDetail({}, { status: "draft", state: "draft" });
    deleteMock.error = new ApiError(409, {
      detail: { message: "Este cupón ya tiene ventas: no se borra, páusalo." },
    });
    const { getByText } = renderDetail();
    expect(getByText("Este cupón ya tiene ventas: no se borra, páusalo.")).toBeTruthy();
  });
});

describe("CouponDetail — solo lectura (D7)", () => {
  it("muestra el motivo y no ofrece pausar ni borrar", () => {
    detailMock.data = makeDetail(
      {},
      {
        manageable: false,
        unmanageableReason: "Tiene una condición por etiquetas creada en Medusa; se ve en solo lectura.",
      },
    );
    const { getByText, queryByRole } = renderDetail();
    expect(
      getByText("Tiene una condición por etiquetas creada en Medusa; se ve en solo lectura."),
    ).toBeTruthy();
    expect(queryByRole("button", { name: "Pausar" })).toBeNull();
    expect(queryByRole("button", { name: "Activar" })).toBeNull();
    expect(queryByRole("button", { name: "Borrar" })).toBeNull();
    // El cupo vive en Hubara: se sigue pudiendo editar.
    expect(queryByRole("button", { name: "Guardar unidades" })).toBeTruthy();
  });
});

describe("CouponDetail — unidades con descuento", () => {
  it("lista las filas con vendidas y quedan", () => {
    const { getByRole } = renderDetail();
    const row = getByRole("row", { name: /fila 1/ });
    expect((within(row).getByLabelText("Producto de la fila 1") as HTMLSelectElement).value).toBe("prod_cubo");
    expect((within(row).getByLabelText("Color de la fila 1") as HTMLSelectElement).value).toBe("Rojo");
    expect(within(row).getByText("2")).toBeTruthy(); // vendidas
    expect(within(row).getByText("3")).toBeTruthy(); // quedan
    // Cubo Love no tiene aromas: no hay selector de aroma.
    expect(within(row).queryByLabelText("Aroma de la fila 1")).toBeNull();
  });

  it("avisa la sobreventa de una fila", () => {
    detailMock.data = makeDetail({
      units: {
        rows: [{ ...makeDetail().units.rows[0]!, units: 1, sold: 2, unitsLeft: 0, oversold: true }],
        showUnitsLeft: true,
        unavailable: false,
      },
    });
    const { getByText } = renderDetail();
    expect(getByText(/Se vendieron más de las que hay en el cupo/)).toBeTruthy();
  });

  it("una fila nueva muestra solo los selectores que el producto tiene", () => {
    const { getByRole, getByLabelText, queryByLabelText } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Agregar fila" }));
    fireEvent.change(getByLabelText("Producto de la fila 2"), { target: { value: "prod_buda" } });
    expect(queryByLabelText("Color de la fila 2")).toBeNull();
    const aroma = getByLabelText("Aroma de la fila 2") as HTMLSelectElement;
    expect([...aroma.options].map((o) => o.value)).toEqual(["", "Lavanda", "Vainilla"]);

    fireEvent.change(getByLabelText("Producto de la fila 2"), { target: { value: "prod_cubo" } });
    expect(queryByLabelText("Aroma de la fila 2")).toBeNull();
    const color = getByLabelText("Color de la fila 2") as HTMLSelectElement;
    expect([...color.options].map((o) => o.value)).toEqual(["", "Rojo", "Blanco"]);
  });

  it("el producto se elige solo entre los del cupón", () => {
    detailMock.data = makeDetail({}, { products: ["prod_cubo", "prod_buda"] });
    const { getByLabelText } = renderDetail();
    const select = getByLabelText("Producto de la fila 1") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["", "prod_cubo", "prod_buda"]);
  });

  it("guardar manda todas las filas (null sin atributo) y la preferencia de mostrar", () => {
    const { getByRole, getByLabelText } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Agregar fila" }));
    fireEvent.change(getByLabelText("Producto de la fila 2"), { target: { value: "prod_otro" } });
    fireEvent.change(getByLabelText("Unidades de la fila 2"), { target: { value: "4" } });
    fireEvent.click(getByLabelText("Mostrar al cliente cuántas quedan"));
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));
    expect(unitsMock.mutate).toHaveBeenCalledTimes(1);
    expect(unitsMock.mutate.mock.calls[0]?.[0]).toEqual({
      rows: [
        { productId: "prod_cubo", color: "Rojo", aroma: null, units: 5 },
        { productId: "prod_otro", color: null, aroma: null, units: 4 },
      ],
      showUnitsLeft: false,
    });
  });

  it("una fila incompleta no se envía y dice qué falta", () => {
    const { getByRole, getByLabelText, getByText } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Agregar fila" }));
    fireEvent.change(getByLabelText("Producto de la fila 2"), { target: { value: "prod_cubo" } });
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));
    expect(unitsMock.mutate).not.toHaveBeenCalled();
    expect(getByText("Elige el color.")).toBeTruthy();
  });

  it("quitar una fila la saca del envío", () => {
    const { getByRole } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Quitar la fila 1" }));
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));
    expect(unitsMock.mutate.mock.calls[0]?.[0]).toEqual({ rows: [], showUnitsLeft: true });
  });

  it("los errores por fila del 422 se muestran en su fila", () => {
    unitsMock.error = new ApiError(422, {
      detail: {
        message: "Revisa las filas marcadas.",
        rows: [{ row: 0, field: "color", message: "Ese color no existe para Cubo Love." }],
      },
    });
    const { getByRole, getByText } = renderDetail();
    expect(getByText("Revisa las filas marcadas.")).toBeTruthy();
    expect(within(getByRole("row", { name: /fila 1/ })).getByText("Ese color no existe para Cubo Love.")).toBeTruthy();
  });

  it("un cupón que no es de porcentaje no acepta cupo", () => {
    detailMock.data = makeDetail({}, { acceptsUnits: false, percentage: null, manageable: false });
    const { getByText, queryByRole } = renderDetail();
    expect(getByText(/El cupo por unidad es solo para cupones de porcentaje/)).toBeTruthy();
    expect(queryByRole("button", { name: "Guardar unidades" })).toBeNull();
  });
});

describe("CouponDetail — registro de cambios", () => {
  it("muestra quién cambió qué", () => {
    const { getByText } = renderDetail();
    expect(getByText("operadora")).toBeTruthy();
    expect(getByText(/Editó el cupón/)).toBeTruthy();
    expect(getByText(/descuento: 10 → 15/)).toBeTruthy();
  });
});
