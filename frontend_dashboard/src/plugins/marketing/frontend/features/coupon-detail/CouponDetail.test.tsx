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
import { useState } from "react";

import { ApiError } from "@/shared/sdk";

import type {
  Coupon,
  CouponDetail as CouponDetailData,
  CouponUpdateMutation,
} from "@plugins/marketing/frontend/entities/coupon";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";

const statusMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const deleteMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const unitsMock = {
  mutate: vi.fn(),
  reset: vi.fn(),
  isPending: false,
  error: null as Error | null,
};
const updateMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const detailMock = {
  data: undefined as CouponDetailData | undefined,
  isPending: false,
  error: null as Error | null,
  refetch: vi.fn(),
};

const PRODUCTS = [
  { id: "prod_cubo", handle: "cubo-love", title: "Cubo Love", colors: ["Rojo", "Blanco"], aromas: [] },
  { id: "prod_buda", handle: "vela-buda", title: "Vela Buda", colors: [], aromas: ["Lavanda", "Vainilla"] },
  { id: "prod_otro", handle: "otro", title: "Otro producto", colors: [], aromas: [] },
];
const productsMock = {
  data: PRODUCTS as typeof PRODUCTS | undefined,
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/coupon", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCoupon: () => detailMock,
  useSetCouponStatus: () => statusMock,
  useDeleteCoupon: () => deleteMock,
  useUpdateCoupon: () => updateMock,
  usePutCouponUnits: () => unitsMock,
  useCouponProducts: () => productsMock,
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
      updatedAt: "2026-09-23T15:00:00Z",
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
  const utils = render(<CouponDetail couponId="promo_01" renderForm={renderForm} onDeleted={onDeleted} />);
  /** Re-render con lo que tengan ahora los mocks (refetch, error nuevo…). */
  const rerender = () =>
    utils.rerender(<CouponDetail couponId="promo_01" renderForm={renderForm} onDeleted={onDeleted} />);
  return { ...utils, onDeleted, rerender };
}

type UnitRow = CouponDetailData["units"]["rows"][number];

function unitRow(over: Partial<UnitRow> = {}): UnitRow {
  return {
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
    ...over,
  };
}

function withUnits(rows: UnitRow[], updatedAt: string | null = "2026-09-23T15:00:00Z") {
  return makeDetail({ units: { rows, showUnitsLeft: true, unavailable: false, updatedAt } });
}

const unitsCell = (row: HTMLElement, n: number) =>
  (within(row).getByLabelText(`Unidades de la fila ${n}`) as HTMLInputElement).value;

beforeEach(() => {
  for (const m of [statusMock, deleteMock, unitsMock, updateMock]) {
    m.mutate.mockReset();
    m.error = null;
  }
  renderForm.mockClear();
  unitsMock.reset.mockClear();
  detailMock.refetch.mockClear();
  detailMock.data = makeDetail();
  detailMock.error = null;
  productsMock.data = PRODUCTS;
  productsMock.error = null;
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

/** Formulario con estado local: si el detalle lo re-monta, lo escrito se pierde. */
function StatefulForm({ update }: { update?: CouponUpdateMutation }) {
  const [draft, setDraft] = useState("");
  return (
    <div>
      <input aria-label="Borrador del formulario" value={draft} onChange={(e) => setDraft(e.target.value)} />
      {update?.error ? <p>{apiErrorDetail(update.error)}</p> : null}
    </div>
  );
}

function renderWithStatefulForm() {
  const form = (_c: Coupon, update?: CouponUpdateMutation) => <StatefulForm update={update} />;
  const utils = render(<CouponDetail couponId="promo_01" renderForm={form} onDeleted={vi.fn()} />);
  const rerender = () =>
    utils.rerender(<CouponDetail couponId="promo_01" renderForm={form} onDeleted={vi.fn()} />);
  return { ...utils, rerender };
}

describe("CouponDetail — un refetch fallido no borra el panel (D5)", () => {
  it("con datos, avisa el error sin desmontar el cupón ni lo que el operador escribió", () => {
    const { getByLabelText, getByRole, getByText, rerender } = renderWithStatefulForm();
    fireEvent.change(getByLabelText("Borrador del formulario"), { target: { value: "sin guardar" } });

    detailMock.error = new ApiError(503, {
      detail: { message: "Medusa no responde ahora mismo; no se hizo ningún cambio." },
    });
    rerender();

    expect(getByRole("heading", { name: "AMOR27" })).toBeTruthy();
    expect((getByLabelText("Borrador del formulario") as HTMLInputElement).value).toBe("sin guardar");
    expect(getByText(/No se pudo actualizar el cupón/)).toBeTruthy();
    expect(getByText(/Medusa no responde ahora mismo/)).toBeTruthy();
  });

  it("sin datos, el error ocupa el panel", () => {
    detailMock.data = undefined;
    detailMock.error = new ApiError(404, { detail: { message: "Ese cupón no existe en Medusa." } });
    const { getByText, queryByTestId } = renderDetail();
    expect(getByText("Ese cupón no existe en Medusa.")).toBeTruthy();
    expect(queryByTestId("coupon-form")).toBeNull();
  });
});

describe("CouponDetail — el formulario no se re-siembra por cualquier cosa (D6)", () => {
  it("pausar o activar (cambia el estado) no borra lo que el operador escribió", () => {
    const { getByLabelText, rerender } = renderWithStatefulForm();
    fireEvent.change(getByLabelText("Borrador del formulario"), { target: { value: "sin guardar" } });

    detailMock.data = makeDetail({}, { status: "inactive", state: "paused" });
    rerender();

    expect((getByLabelText("Borrador del formulario") as HTMLInputElement).value).toBe("sin guardar");
  });

  it("el error de una edición a medias sigue a la vista después del refetch que re-siembra el formulario", () => {
    updateMock.error = new ApiError(502, {
      detail: { message: "El cambio quedó a medias en Medusa; reintentar es seguro.", step: "campaign" },
    });
    const { getByText, rerender } = renderWithStatefulForm();
    expect(getByText("El cambio quedó a medias en Medusa; reintentar es seguro.")).toBeTruthy();

    // Medusa aplicó una parte (el código): el refetch trae datos nuevos y el
    // formulario se re-siembra con lo que quedó.
    detailMock.data = makeDetail({}, { code: "AMOR28" });
    rerender();

    expect(getByText("El cambio quedó a medias en Medusa; reintentar es seguro.")).toBeTruthy();
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
        updatedAt: "2026-09-23T15:00:00Z",
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
      // C-5: la versión del cupo que se editó.
      expectedUpdatedAt: "2026-09-23T15:00:00Z",
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
    expect(unitsMock.mutate.mock.calls[0]?.[0]).toEqual({
      rows: [],
      showUnitsLeft: true,
      expectedUpdatedAt: "2026-09-23T15:00:00Z",
    });
  });

  it("los errores por fila del 422 se muestran en su fila", () => {
    const { getByRole, getByText, rerender } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));
    unitsMock.error = new ApiError(422, {
      detail: {
        message: "Revisa las filas marcadas.",
        rows: [{ row: 0, field: "color", message: "Ese color no existe para Cubo Love." }],
      },
    });
    rerender();
    expect(getByText("Revisa las filas marcadas.")).toBeTruthy();
    expect(within(getByRole("row", { name: /fila 1/ })).getByText("Ese color no existe para Cubo Love.")).toBeTruthy();
  });

  it("si el catálogo no carga lo dice en las unidades (sin él no se eligen producto, color y aroma) (D9)", () => {
    productsMock.data = undefined;
    productsMock.error = new ApiError(503, {
      detail: { message: "Medusa no responde ahora mismo; no se hizo ningún cambio." },
    });
    const { getByText } = renderDetail();
    expect(getByText(/No se pudieron cargar los productos del catálogo/)).toBeTruthy();
    expect(getByText(/Medusa no responde ahora mismo/)).toBeTruthy();
  });

  it("editar una fila guardada en producto, color o aroma deja de mostrar sus vendidas y quedan (D13)", () => {
    const { getByRole, getByLabelText } = renderDetail();
    const row = () => getByRole("row", { name: /fila 1/ });
    expect(within(row()).getByText("2")).toBeTruthy();

    fireEvent.change(getByLabelText("Color de la fila 1"), { target: { value: "Blanco" } });

    // Ya es otra combinación: lo vendido del "Rojo" no es de esta fila.
    expect(within(row()).queryByText("2")).toBeNull();
    expect(within(row()).queryByText("3")).toBeNull();
    expect(within(row()).getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("los errores del 422 siguen a SU fila aunque se quite otra, y se borran al editarla (D13)", () => {
    detailMock.data = withUnits([
      unitRow({ id: "q_a", units: 1 }),
      unitRow({ id: "q_b", units: 2, color: "Blanco" }),
      unitRow({ id: "q_c", units: 3, productId: "prod_otro", title: "Otro producto", color: null }),
    ]);
    const { getByRole, rerender, queryByText } = renderDetail();
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));
    unitsMock.error = new ApiError(422, {
      detail: {
        message: "Revisa las filas marcadas.",
        rows: [{ row: 1, field: "color", message: "Ese color no existe para Cubo Love." }],
      },
    });
    rerender();
    const errorRow = () => queryByText("Ese color no existe para Cubo Love.")?.closest("tr");
    expect(unitsCell(errorRow()!, 2)).toBe("2");

    // Se quita la fila de arriba: el error sigue en la fila de 2 unidades
    // (ahora la 1), no salta a la de 3.
    fireEvent.click(getByRole("button", { name: "Quitar la fila 1" }));
    expect(unitsCell(errorRow()!, 1)).toBe("2");

    // Al corregir esa fila, su error del servidor ya no aplica.
    fireEvent.change(getByRole("row", { name: /fila 1/ }).querySelector("input")!, {
      target: { value: "4" },
    });
    expect(queryByText("Ese color no existe para Cubo Love.")).toBeNull();
  });

  it("no deja pasar de 200 filas (el backend no acepta más) (D13)", () => {
    detailMock.data = withUnits(
      Array.from({ length: 199 }, (_, i) =>
        unitRow({ id: `q_${i}`, productId: "prod_otro", title: "Otro producto", color: null }),
      ),
    );
    const { getByRole, getByText } = renderDetail();
    const add = getByRole("button", { name: "Agregar fila" }) as HTMLButtonElement;
    fireEvent.click(add);

    expect(add.disabled).toBe(true);
    expect(getByText("Un cupón lleva hasta 200 filas.")).toBeTruthy();
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

  it("el paso que falló se dice en español y los productos con su nombre (D14)", () => {
    detailMock.data = makeDetail({
      changes: [
        {
          ts: "2026-09-23T15:00:00Z",
          actor: "operadora@tienda.co",
          action: "update_partial",
          detail: {
            products: [["prod_cubo"], ["prod_cubo", "prod_retirado"]],
            failed_step: "campaign",
          },
        },
        {
          ts: "2026-09-22T15:00:00Z",
          actor: "operadora@tienda.co",
          action: "update",
          detail: { products: ["all", ["prod_buda"]] },
        },
      ],
    });
    const { getByText } = renderDetail();
    // Un producto que ya no está en el catálogo queda con su id.
    expect(getByText("productos: Cubo Love → Cubo Love, prod_retirado")).toBeTruthy();
    expect(getByText("paso que falló: campaña y fechas")).toBeTruthy();
    expect(getByText("productos: todo el catálogo → Vela Buda")).toBeTruthy();
  });
});

describe("CouponDetail — otra persona cambió las unidades (D7, C-5)", () => {
  const CONFLICT = "Otra persona cambió las unidades de este cupón. Recarga para ver lo nuevo antes de guardar.";
  /** Lo que guardó la otra persona: otra fila y otra versión. */
  const theirs = () =>
    withUnits([unitRow({ id: "q_otra", units: 8, color: "Blanco" })], "2026-09-24T10:00:00Z");

  it("el 409 muestra el mensaje y 'Recargar'; el borrador queda hasta recargar", () => {
    const { getByRole, getByText, getByLabelText, rerender } = renderDetail();
    fireEvent.change(getByLabelText("Unidades de la fila 1"), { target: { value: "9" } });
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));
    expect(unitsMock.mutate.mock.calls[0]?.[0].expectedUpdatedAt).toBe("2026-09-23T15:00:00Z");

    unitsMock.error = new ApiError(409, { detail: { code: "units_changed", message: CONFLICT } });
    rerender();
    expect(getByText(CONFLICT)).toBeTruthy();
    expect((getByLabelText("Unidades de la fila 1") as HTMLInputElement).value).toBe("9");

    fireEvent.click(getByRole("button", { name: "Recargar" }));
    expect(detailMock.refetch).toHaveBeenCalledTimes(1);
    expect(unitsMock.reset).toHaveBeenCalledTimes(1);

    // Llega lo nuevo: ahora sí se ve lo que guardó la otra persona.
    unitsMock.error = null;
    detailMock.data = theirs();
    rerender();
    expect((getByLabelText("Unidades de la fila 1") as HTMLInputElement).value).toBe("8");
    expect((getByLabelText("Color de la fila 1") as HTMLSelectElement).value).toBe("Blanco");
  });

  it("un refetch con lo de otra persona NO pisa el borrador: avisa y deja recargar", () => {
    const { getByRole, getByText, getByLabelText, rerender } = renderDetail();
    fireEvent.change(getByLabelText("Unidades de la fila 1"), { target: { value: "9" } });

    detailMock.data = theirs();
    rerender();

    expect((getByLabelText("Unidades de la fila 1") as HTMLInputElement).value).toBe("9");
    expect(getByText(/Otra persona cambió las unidades de este cupón/)).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Recargar" }));
    expect((getByLabelText("Unidades de la fila 1") as HTMLInputElement).value).toBe("8");
  });

  it("sin cambios propios, lo nuevo del servidor se ve solo", () => {
    const { getByLabelText, rerender } = renderDetail();

    detailMock.data = theirs();
    rerender();

    expect((getByLabelText("Unidades de la fila 1") as HTMLInputElement).value).toBe("8");
  });

  it("tras guardar, la próxima vez manda la versión recién guardada", () => {
    unitsMock.mutate.mockImplementation(
      (_input: unknown, opts?: { onSuccess?: (saved: CouponDetailData["units"]) => void }) =>
        opts?.onSuccess?.({
          rows: [unitRow({ id: "q_nueva", units: 9 })],
          showUnitsLeft: true,
          unavailable: false,
          updatedAt: "2026-09-24T12:00:00Z",
        }),
    );
    const { getByRole, getByLabelText } = renderDetail();
    fireEvent.change(getByLabelText("Unidades de la fila 1"), { target: { value: "9" } });
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));

    fireEvent.change(getByLabelText("Unidades de la fila 1"), { target: { value: "7" } });
    fireEvent.click(getByRole("button", { name: "Guardar unidades" }));

    expect(unitsMock.mutate.mock.calls[1]?.[0].expectedUpdatedAt).toBe("2026-09-24T12:00:00Z");
  });
});
