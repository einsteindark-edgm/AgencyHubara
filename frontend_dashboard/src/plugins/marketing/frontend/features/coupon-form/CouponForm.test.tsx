/**
 * Formulario del cupón (alta y edición):
 *  - código saneado (A-Z0-9, 3–14) editable solo en alta o en borrador
 *  - validación inline ANTES de enviar (código, porcentaje, productos, fechas)
 *  - "el último día cuenta completo (hora de Colombia)"
 *  - "Guardar borrador" / "Crear y activar" en alta; PATCH solo con lo que
 *    cambió en edición; errores del backend pegados a su campo
 *  - solo lectura cuando el cupón no es gestionable (D7)
 * Las mutaciones se stubean: acá se testea la UX, no el HTTP.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import { ApiError } from "@/shared/sdk";

const createMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const updateMock = { mutate: vi.fn(), isPending: false, error: null as Error | null };
const PRODUCTS = [
  { id: "prod_cubo", handle: "cubo-love", title: "Cubo Love", colors: ["Rojo"], aromas: [] },
  { id: "prod_buda", handle: "vela-buda", title: "Vela Buda", colors: [], aromas: ["Lavanda"] },
];
const productsMock = {
  data: PRODUCTS as typeof PRODUCTS | undefined,
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/coupon", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCreateCoupon: () => createMock,
  useUpdateCoupon: () => updateMock,
  useCouponProducts: () => productsMock,
}));

import type { Coupon } from "@plugins/marketing/frontend/entities/coupon";

import { CouponForm } from "./ui/CouponForm";

function makeCoupon(over: Partial<Coupon> = {}): Coupon {
  return {
    promotionId: "promo_01",
    campaignId: "procamp_01",
    code: "AMOR27",
    campaignName: "AMOR Y AMISTAD 2026",
    percentage: 10,
    products: ["prod_cubo"],
    startsOn: "2026-09-22",
    endsOn: "2026-09-27",
    endsOnLabel: "27 de septiembre",
    status: "active",
    state: "active",
    manageable: true,
    unmanageableReason: null,
    acceptsUnits: true,
    units: null,
    ...over,
  };
}

function fill(getByLabelText: (t: string | RegExp) => HTMLElement, values: Record<string, string>) {
  for (const [label, value] of Object.entries(values)) {
    const el = getByLabelText(label);
    fireEvent.change(el, { target: { value } });
    fireEvent.blur(el);
  }
}

const VALID = {
  "Código": "amor27",
  "Nombre de la campaña": "AMOR Y AMISTAD 2026",
  "Descuento (%)": "10",
  "Desde": "2026-09-22",
  "Hasta": "2026-09-27",
};

beforeEach(() => {
  createMock.mutate.mockClear();
  updateMock.mutate.mockClear();
  createMock.error = null;
  updateMock.error = null;
  productsMock.data = PRODUCTS;
  productsMock.error = null;
});

describe("CouponForm — alta", () => {
  it("explica que el último día cuenta completo en hora de Colombia", () => {
    const { getByText } = render(<CouponForm />);
    expect(getByText("El último día cuenta completo (hora de Colombia)")).toBeTruthy();
  });

  it("sanea el código al escribir (mayúsculas, solo letras y números)", () => {
    const { getByLabelText } = render(<CouponForm />);
    const code = getByLabelText("Código") as HTMLInputElement;
    fireEvent.change(code, { target: { value: "amor_27 " } });
    expect(code.value).toBe("AMOR27");
  });

  it("valida inline antes de enviar: código corto, porcentaje fuera de rango, fechas al revés", () => {
    const { getByLabelText } = render(<CouponForm />);
    fill(getByLabelText, {
      "Código": "ab",
      "Descuento (%)": "0",
      "Desde": "2026-09-27",
      "Hasta": "2026-09-22",
    });
    expect(getByLabelText("Código")).toHaveAccessibleDescription(
      "El código lleva de 3 a 14 letras o números.",
    );
    expect(getByLabelText("Descuento (%)")).toHaveAccessibleDescription(
      "El descuento es un número entero entre 1 y 100.",
    );
    expect(getByLabelText("Hasta")).toHaveAccessibleDescription(
      'El "hasta" no puede ser antes del "desde".',
    );
    expect(createMock.mutate).not.toHaveBeenCalled();
  });

  it("un porcentaje con decimales no vale", () => {
    const { getByLabelText } = render(<CouponForm />);
    fill(getByLabelText, { "Descuento (%)": "12.5" });
    expect(getByLabelText("Descuento (%)")).toHaveAccessibleDescription(
      "El descuento es un número entero entre 1 y 100.",
    );
  });

  it("enviar con errores no llama a la API y muestra todos los errores", () => {
    const { getByRole, getByLabelText } = render(<CouponForm />);
    fireEvent.click(getByRole("button", { name: "Crear y activar" }));
    expect(createMock.mutate).not.toHaveBeenCalled();
    expect(getByLabelText("Código")).toHaveAccessibleDescription(
      "El código lleva de 3 a 14 letras o números.",
    );
  });

  it("'Crear y activar' manda el cupón activo a todo el catálogo", () => {
    const { getByRole, getByLabelText } = render(<CouponForm />);
    fill(getByLabelText, VALID);
    fireEvent.click(getByRole("button", { name: "Crear y activar" }));
    expect(createMock.mutate).toHaveBeenCalledTimes(1);
    expect(createMock.mutate.mock.calls[0]?.[0]).toEqual({
      code: "AMOR27",
      campaignName: "AMOR Y AMISTAD 2026",
      percentage: 10,
      products: "all",
      startsOn: "2026-09-22",
      endsOn: "2026-09-27",
      status: "active",
    });
  });

  it("'Guardar borrador' crea en borrador con los productos elegidos", () => {
    const { getByRole, getByLabelText } = render(<CouponForm />);
    fill(getByLabelText, VALID);
    fireEvent.click(getByLabelText("Productos elegidos"));
    fireEvent.click(getByLabelText("Cubo Love"));
    fireEvent.click(getByRole("button", { name: "Guardar borrador" }));
    expect(createMock.mutate.mock.calls[0]?.[0]).toMatchObject({
      products: ["prod_cubo"],
      status: "draft",
    });
  });

  it("productos elegidos sin ninguno marcado es un error", () => {
    const { getByRole, getByLabelText, getByText } = render(<CouponForm />);
    fill(getByLabelText, VALID);
    fireEvent.click(getByLabelText("Productos elegidos"));
    fireEvent.click(getByRole("button", { name: "Crear y activar" }));
    expect(createMock.mutate).not.toHaveBeenCalled();
    expect(getByText("Elige al menos un producto o todo el catálogo.")).toBeTruthy();
  });

  it("el error de campo del backend se muestra en su campo", () => {
    createMock.error = new ApiError(422, {
      detail: { field: "ends_on", message: "La fecha va como AAAA-MM-DD." },
    });
    const { getByLabelText } = render(<CouponForm />);
    expect(getByLabelText("Hasta")).toHaveAccessibleDescription("La fecha va como AAAA-MM-DD.");
  });

  it("si el catálogo no carga, lo dice donde se eligen los productos (D9)", () => {
    productsMock.data = undefined;
    productsMock.error = new ApiError(503, {
      detail: { message: "Medusa no responde ahora mismo; no se hizo ningún cambio." },
    });
    const { getByLabelText, getByText } = render(<CouponForm />);
    fireEvent.click(getByLabelText("Productos elegidos"));
    expect(getByText(/No se pudieron cargar los productos del catálogo/)).toBeTruthy();
    expect(getByText(/Medusa no responde ahora mismo/)).toBeTruthy();
  });

  it("un error sin campo (código ocupado) se muestra con su mensaje", () => {
    createMock.error = new ApiError(409, { detail: { message: "Ese código ya existe." } });
    const { getByText } = render(<CouponForm />);
    expect(getByText("Ese código ya existe.")).toBeTruthy();
  });
});

describe("CouponForm — edición", () => {
  it("fuera de borrador el código no se edita", () => {
    const { getByLabelText } = render(<CouponForm coupon={makeCoupon()} />);
    const code = getByLabelText("Código") as HTMLInputElement;
    expect(code.value).toBe("AMOR27");
    expect(code.readOnly).toBe(true);
  });

  it("en borrador el código sí se edita", () => {
    const { getByLabelText } = render(
      <CouponForm coupon={makeCoupon({ status: "draft", state: "draft" })} />,
    );
    expect((getByLabelText("Código") as HTMLInputElement).readOnly).toBe(false);
  });

  it("'Guardar cambios' manda SOLO lo que cambió", () => {
    const { getByRole, getByLabelText } = render(<CouponForm coupon={makeCoupon()} />);
    expect((getByLabelText("Cubo Love") as HTMLInputElement).checked).toBe(true);
    fill(getByLabelText, { "Descuento (%)": "15" });
    fireEvent.click(getByRole("button", { name: "Guardar cambios" }));
    expect(updateMock.mutate).toHaveBeenCalledTimes(1);
    expect(updateMock.mutate.mock.calls[0]?.[0]).toEqual({ percentage: 15 });
  });

  it("usa la mutación que le pasa el detalle: el error 'a medias' sobrevive al re-sembrado (D6)", () => {
    // El detalle es dueño de la edición: el formulario se re-monta tras el
    // refetch de un 502 parcial y el aviso tiene que seguir ahí.
    const lifted = {
      mutate: vi.fn(),
      isPending: false,
      error: new ApiError(502, {
        detail: { message: "El cambio quedó a medias en Medusa; reintentar es seguro.", step: "campaign" },
      }),
    };
    const { getByRole, getByLabelText, getByText } = render(
      <CouponForm
        coupon={makeCoupon()}
        update={lifted as unknown as Parameters<typeof CouponForm>[0]["update"]}
      />,
    );
    expect(getByText("El cambio quedó a medias en Medusa; reintentar es seguro.")).toBeTruthy();

    fill(getByLabelText, { "Descuento (%)": "15" });
    fireEvent.click(getByRole("button", { name: "Guardar cambios" }));
    expect(lifted.mutate).toHaveBeenCalledWith({ percentage: 15 });
    expect(updateMock.mutate).not.toHaveBeenCalled();
  });

  it("no gestionable: todo deshabilitado y sin botón de guardar", () => {
    const { getByLabelText, queryByRole } = render(
      <CouponForm
        coupon={makeCoupon({
          manageable: false,
          unmanageableReason: "Tiene una condición por etiquetas creada en Medusa; se ve en solo lectura.",
        })}
      />,
    );
    expect((getByLabelText("Descuento (%)") as HTMLInputElement).disabled).toBe(true);
    expect((getByLabelText("Nombre de la campaña") as HTMLInputElement).disabled).toBe(true);
    expect(queryByRole("button", { name: "Guardar cambios" })).toBeNull();
  });
});
