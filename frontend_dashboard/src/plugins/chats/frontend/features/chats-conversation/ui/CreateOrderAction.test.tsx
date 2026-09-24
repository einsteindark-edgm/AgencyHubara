/**
 * Botón "Crear pedido" del chat intervenido.
 *
 * El caso que arregla: el cliente no completó el Flow, el bot escaló y el
 * humano sacó los datos a mano — y no había forma de registrar el pedido.
 * Acá verificamos el COMPORTAMIENTO del formulario, no su markup:
 *   - al abrirlo se lee la conversación y los campos llegan LLENOS;
 *   - lo que el operador edita es lo que se manda a registrar;
 *   - sin los datos que exige la transportadora, no se puede enviar;
 *   - un fallo del registro se ve (y no cierra el formulario);
 *   - con el LLM caído el formulario abre igual.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CreateOrderAction } from "./CreateOrderAction";

const suggestAsync = vi.fn();
const createAsync = vi.fn();
let suggestState = { isPending: false };
let createState = { isPending: false };

// Los hooks se reemplazan, pero la respuesta pasa por los schemas REALES del
// entity (L-10): el componente recibe la forma parseada, con sus defaults,
// igual que en producción.
vi.mock("@plugins/chats/frontend/entities/order-intake", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@plugins/chats/frontend/entities/order-intake")>();
  return {
    ...actual,
    useSuggestOrderFromChat: () => ({
      mutateAsync: async () => actual.orderSuggestionSchema.parse(await suggestAsync()),
      ...suggestState,
    }),
    useCreateOrderFromChat: () => ({
      mutateAsync: async (body: unknown) =>
        actual.createOrderResultSchema.parse(await createAsync(body)),
      ...createState,
    }),
  };
});

const SUGGESTION = {
  session_key: "wa_573001234567",
  phone_number: "573001234567",
  handoff_at_ms: 1758000000000,
  messages_considered: 8,
  items: [
    {
      handle: "duo-zodiacal",
      title: "Dúo Zodiacal",
      variant_label: "Leo",
      quantity: 2,
      unit_price_cop: 52000,
      line_total_cop: 104000,
      variant_resolved: true,
      evidence: "quiero el Dúo Zodiacal de Leo",
    },
  ],
  shipping: {
    city: "Bogotá",
    neighborhood: "Chapinero",
    address: "Calle 45 #12-30",
    phone: "3001234567",
    receiver_name: "Ana Pérez",
    national_id: null,
  },
  field_sources: {
    city: "conversation",
    neighborhood: "draft",
    address: "conversation",
    phone: "session",
    receiver_name: "conversation",
    national_id: null,
    payment_method: "conversation",
  },
  payment_method: "transfer",
  subtotal_cop: 104000,
  shipping_cop: 7900,
  total_cop: 111900,
  missing: [],
  warnings: [],
  notes: null,
  catalog: [
    {
      handle: "duo-zodiacal",
      title: "Dúo Zodiacal",
      variants: [
        { label: "Leo", unit_price_cop: 52000 },
        { label: "Aries", unit_price_cop: 52000 },
      ],
    },
    {
      handle: "luz-serena",
      title: "Luz Serena",
      variants: [{ label: "Lavanda / Blanco", unit_price_cop: 29000 }],
    },
  ],
  already_registered_order_id: null,
  model: "litellm_proxy/deepseek-v4-flash",
  degraded: false,
  error_detail: null,
};

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function renderAction() {
  return render(<CreateOrderAction chatId="wa_573001234567" />, { wrapper: wrapper() });
}

async function openForm(suggestion: unknown = SUGGESTION) {
  suggestAsync.mockResolvedValue(suggestion);
  renderAction();
  fireEvent.click(screen.getByRole("button", { name: /crear pedido/i }));
  return screen.findByRole("dialog", { name: /crear pedido/i });
}

beforeEach(() => {
  suggestAsync.mockReset();
  createAsync.mockReset();
  suggestState = { isPending: false };
  createState = { isPending: false };
});

describe("CreateOrderAction", () => {
  it("al abrirlo lee la conversación y llega con los campos llenos", async () => {
    await openForm();

    expect(suggestAsync).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText(/ciudad/i)).toHaveValue("Bogotá");
    expect(screen.getByLabelText(/dirección/i)).toHaveValue("Calle 45 #12-30");
    expect(screen.getByLabelText(/barrio/i)).toHaveValue("Chapinero");
    expect(screen.getByLabelText(/teléfono/i)).toHaveValue("3001234567");
    expect(screen.getByLabelText(/recibe/i)).toHaveValue("Ana Pérez");
    expect(screen.getByLabelText(/método de pago/i)).toHaveValue("transfer");
    // El ítem en la lista del pedido (el título también aparece en el selector
    // del catálogo, así que nos anclamos a lo que es único de la fila).
    expect(screen.getByLabelText(/cantidad de dúo zodiacal/i)).toHaveValue(2);
    expect(screen.getByText(/\$52\.000 c\/u/)).toBeInTheDocument();
  });

  it("dice de dónde salió cada dato (conversación / bot / WhatsApp)", async () => {
    await openForm();

    const address = screen.getByLabelText(/dirección/i).closest("label")!;
    expect(within(address).getByText(/conversación/i)).toBeInTheDocument();
    const neighborhood = screen.getByLabelText(/barrio/i).closest("label")!;
    expect(within(neighborhood).getByText(/bot/i)).toBeInTheDocument();
    const phone = screen.getByLabelText(/teléfono/i).closest("label")!;
    expect(within(phone).getByText(/whatsapp/i)).toBeInTheDocument();
  });

  it("registra el pedido con lo que el operador dejó en el formulario", async () => {
    createAsync.mockResolvedValue({
      registered: true,
      already_registered: false,
      order_id: "order_1",
      order_reference: "#22 (Dúo Zodiacal)",
      error_detail: null,
      problems: [],
      subtotal_cop: 104000,
      shipping_cop: 7900,
      total_cop: 111900,
      payment_instructions_sent: true,
    });
    await openForm();

    fireEvent.change(screen.getByLabelText(/dirección/i), {
      target: { value: "Calle 45 #12-30 apto 201" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));

    await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(1));
    expect(createAsync).toHaveBeenCalledWith({
      items: [{ handle: "duo-zodiacal", variant_label: "Leo", quantity: 2 }],
      shipping: {
        city: "Bogotá",
        neighborhood: "Chapinero",
        address: "Calle 45 #12-30 apto 201",
        phone: "3001234567",
        receiver_name: "Ana Pérez",
      },
      payment_method: "transfer",
      send_payment_instructions: true,
    });
    expect(await screen.findByText(/#22 \(Dúo Zodiacal\)/)).toBeInTheDocument();
  });

  it("al crear el pedido el botón se va, pero el aviso con el número queda a la vista", async () => {
    // Registrar invalida la sesión → aparece `pending_payment_order_id` → el
    // composer pasa `available=false`. Si eso desmontara el modal, el
    // operador perdería "#22 (Dúo Zodiacal)" antes de poder leerlo.
    createAsync.mockResolvedValue({
      registered: true,
      order_id: "order_1",
      order_reference: "#22 (Dúo Zodiacal)",
    });
    suggestAsync.mockResolvedValue(SUGGESTION);
    const { rerender } = render(
      <CreateOrderAction chatId="wa_573001234567" available />,
      { wrapper: wrapper() },
    );
    fireEvent.click(screen.getByRole("button", { name: /crear pedido/i }));
    await screen.findByLabelText(/dirección/i);
    fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));
    expect(await screen.findByText(/#22 \(Dúo Zodiacal\)/)).toBeInTheDocument();

    rerender(<CreateOrderAction chatId="wa_573001234567" available={false} />);

    expect(screen.getByText(/#22 \(Dúo Zodiacal\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /listo/i }));
    // Cerrado el modal, ya no queda el disparador: el pedido existe.
    expect(screen.queryByRole("button", { name: /crear pedido/i })).not.toBeInTheDocument();
  });

  it("al crear el pedido muestra el total registrado", async () => {
    createAsync.mockResolvedValue({
      registered: true,
      order_id: "order_1",
      order_reference: "#22 (Dúo Zodiacal)",
      subtotal_cop: 104000,
      shipping_cop: 7900,
      total_cop: 111900,
    });
    await openForm();

    fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));

    expect(await screen.findByText(/total registrado: \$111\.900/i)).toBeInTheDocument();
  });

  it("el operador puede NO mandarle las instrucciones de pago al cliente", async () => {
    createAsync.mockResolvedValue({ registered: true, order_id: "order_1" });
    await openForm();

    fireEvent.click(screen.getByLabelText(/instrucciones de pago/i));
    fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));

    await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(1));
    expect(createAsync.mock.calls[0][0].send_payment_instructions).toBe(false);
  });

  it("sin los datos que exige la transportadora no deja registrar", async () => {
    await openForm();
    const submit = screen.getByRole("button", { name: /^crear pedido$/i });
    expect(submit).toBeEnabled();

    fireEvent.change(screen.getByLabelText(/recibe/i), { target: { value: "  " } });

    expect(submit).toBeDisabled();
    expect(screen.getByText(/falta.*recibe/i)).toBeInTheDocument();
    expect(createAsync).not.toHaveBeenCalled();
  });

  it("sin ítems tampoco: el pedido vacío no se manda", async () => {
    await openForm();

    fireEvent.click(screen.getByRole("button", { name: /quitar Dúo Zodiacal/i }));

    expect(screen.getByRole("button", { name: /^crear pedido$/i })).toBeDisabled();
  });

  it("el operador puede corregir el producto que eligió el modelo", async () => {
    createAsync.mockResolvedValue({ registered: true, order_id: "order_1" });
    await openForm();

    fireEvent.click(screen.getByRole("button", { name: /quitar Dúo Zodiacal/i }));
    fireEvent.change(screen.getByLabelText(/agregar producto/i), {
      target: { value: "luz-serena||Lavanda / Blanco" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));

    await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(1));
    expect(createAsync.mock.calls[0][0].items).toEqual([
      { handle: "luz-serena", variant_label: "Lavanda / Blanco", quantity: 1 },
    ]);
    // Sin cupón no hay nada que recalcular: registra al primer clic.
    expect(createAsync.mock.calls[0][0]).not.toHaveProperty("dry_run");
  });

  it("muestra el total de productos y lo recalcula al cambiar la cantidad", async () => {
    await openForm();
    expect(screen.getByText("$104.000")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/cantidad/i), { target: { value: "3" } });

    expect(screen.getByText("$156.000")).toBeInTheDocument();
  });

  it("si el registro falla lo dice y no cierra el formulario", async () => {
    createAsync.mockResolvedValue({
      registered: false,
      order_id: null,
      error_detail: "catalog_unavailable",
      problems: [],
    });
    await openForm();

    fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/catálogo/i);
    expect(screen.getByRole("dialog", { name: /crear pedido/i })).toBeInTheDocument();
  });

  it("con el LLM caído el formulario abre igual y lo avisa", async () => {
    await openForm({
      ...SUGGESTION,
      items: [],
      payment_method: null,
      degraded: true,
      error_detail: "RuntimeError: litellm caído",
      field_sources: { ...SUGGESTION.field_sources, city: "draft" },
    });

    expect(screen.getByText(/no se pudo leer la conversación/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/ciudad/i)).toHaveValue("Bogotá");
  });

  it("avisa si la sesión ya tiene un pedido registrado", async () => {
    await openForm({ ...SUGGESTION, already_registered_order_id: "order_9" });

    expect(screen.getByText(/ya tiene un pedido registrado/i)).toBeInTheDocument();
  });

  it("si la lectura de la conversación falla, deja reintentar", async () => {
    suggestAsync.mockRejectedValue(new Error("boom"));
    renderAction();

    fireEvent.click(screen.getByRole("button", { name: /crear pedido/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
    suggestAsync.mockResolvedValue(SUGGESTION);
    fireEvent.click(screen.getByRole("button", { name: /reintentar/i }));
    expect(await screen.findByLabelText(/dirección/i)).toHaveValue("Calle 45 #12-30");
  });
  describe("cupo por unidad: color, aroma y descuento por línea", () => {
    const CUBO = {
      handle: "cubo-love",
      title: "Cubo Love",
      variant_label: null,
      quantity: 2,
      unit_price_cop: 21000,
      line_total_cop: 42000,
      variant_resolved: true,
      evidence: null,
      color: "Rosado",
      aroma: "Café",
      colors: ["Rosado", "Azul"],
      aromas: ["Café", "Lavanda"],
      coupon_units: 1,
      coupon_discount_cop: 2100,
    };
    // Como lo arma el backend: total = productos + envío − descuento.
    const WITH_COUPON = {
      ...SUGGESTION,
      items: [CUBO],
      subtotal_cop: 42000,
      shipping_cop: 7900,
      discount_cop: 2100,
      total_cop: 47800,
      coupon_code: "AMOR26",
      coupon_reason: null,
    };
    // Cupón aplicado a $0 (C-2): el código viene igual, con el motivo.
    const CUBO_SIN_COLOR = { ...CUBO, color: null, coupon_units: 0, coupon_discount_cop: 0 };
    const COUPON_AT_ZERO = {
      ...WITH_COUPON,
      items: [CUBO_SIN_COLOR],
      discount_cop: 0,
      total_cop: 49900,
      coupon_reason: "missing_attributes",
    };
    /** Respuesta de un cálculo sin registrar (`dry_run`, C-1). */
    const QUOTE_3_CUBOS = {
      registered: false,
      dry_run: true,
      order_id: null,
      error_detail: null,
      subtotal_cop: 63000,
      shipping_cop: 7900,
      discount_cop: 2100,
      total_cop: 68800,
      coupon_code: "AMOR26",
    };
    const REGISTERED = { registered: true, order_id: "order_1", order_reference: "#31" };

    /** El backend real: con `dry_run` calcula sin registrar; sin él, registra. */
    function backendReplies(quote: unknown, registered: unknown = REGISTERED) {
      createAsync.mockImplementation(async (body: { dry_run?: boolean }) =>
        body.dry_run ? quote : registered,
      );
    }

    const clickCreate = () =>
      fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));

    /** Fila del resumen (etiqueta + valor) que contiene `label`. */
    const summaryRow = (label: RegExp) => screen.getByText(label).parentElement?.textContent ?? "";

    it("pide color y aroma de las listas del producto, preseleccionados, y los manda en el pedido", async () => {
      backendReplies({ ...QUOTE_3_CUBOS, subtotal_cop: 42000, total_cop: 47800 });
      await openForm(WITH_COUPON);

      const color = screen.getByLabelText(/color de cubo love/i);
      const aroma = screen.getByLabelText(/aroma de cubo love/i);
      expect(color).toHaveValue("Rosado");
      expect(aroma).toHaveValue("Café");
      expect(
        within(color).getAllByRole("option").map((o) => o.textContent),
      ).toEqual(expect.arrayContaining(["Rosado", "Azul"]));

      fireEvent.change(aroma, { target: { value: "Lavanda" } });
      clickCreate();
      await screen.findByRole("status");
      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(2));
      for (const [body] of createAsync.mock.calls) {
        expect(body.items).toEqual([
          { handle: "cubo-love", quantity: 2, color: "Rosado", aroma: "Lavanda" },
        ]);
      }
    });

    it("si el producto no tiene listas no pide color ni aroma (y no los manda)", async () => {
      createAsync.mockResolvedValue(REGISTERED);
      await openForm({
        ...SUGGESTION,
        items: [
          { ...CUBO, color: null, aroma: null, colors: [], aromas: ["Café"], coupon_units: 0, coupon_discount_cop: 0 },
        ],
      });

      expect(screen.queryByLabelText(/color de cubo love/i)).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText(/aroma de cubo love/i), {
        target: { value: "Café" },
      });
      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(1));
      expect(createAsync.mock.calls[0][0].items).toEqual([
        { handle: "cubo-love", quantity: 2, aroma: "Café" },
      ]);
    });

    it("muestra cuántas unidades de la línea llevan el descuento del cupón", async () => {
      await openForm(WITH_COUPON);

      expect(screen.getByText("1 de 2 con AMOR26 (−$2.100)")).toBeInTheDocument();
    });

    it("muestra el cupón y su descuento en el resumen", async () => {
      await openForm(WITH_COUPON);

      expect(summaryRow(/^Cupón AMOR26$/)).toContain("−$2.100");
    });

    it("con cupón y sin color o aroma avisa que el descuento los necesita", async () => {
      await openForm(COUPON_AT_ZERO);

      expect(screen.getByLabelText(/color de cubo love/i)).toHaveValue("");
      expect(
        screen.getByText(/el descuento del cupón necesita el color y el aroma/i),
      ).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText(/color de cubo love/i), {
        target: { value: "Azul" },
      });

      expect(
        screen.queryByText(/el descuento del cupón necesita el color y el aroma/i),
      ).not.toBeInTheDocument();
    });

    it("sin cupón no hay aviso aunque falte el color", async () => {
      await openForm({
        ...SUGGESTION,
        items: [{ ...CUBO, color: null, coupon_units: 0, coupon_discount_cop: 0 }],
      });

      expect(
        screen.queryByText(/el descuento del cupón necesita/i),
      ).not.toBeInTheDocument();
    });

    it.each([
      ["missing_attributes", "falta elegir color y aroma"],
      ["quota_exhausted", "se agotaron las unidades con descuento"],
      ["quota_unavailable", "no se pudieron leer las unidades (intenta en un minuto)"],
      ["min_subtotal", "el pedido no alcanza el mínimo del cupón"],
      ["no_applicable_items", "ningún producto del pedido aplica para el cupón"],
      ["unsupported", "este cupón no se puede aplicar desde aquí"],
    ])("el cupón sin descuento (%s) se ve en el resumen con el motivo", async (reason, label) => {
      await openForm({ ...COUPON_AT_ZERO, coupon_reason: reason });

      expect(screen.getByText(`Cupón AMOR26: sin descuento — ${label}`)).toBeInTheDocument();
    });

    it("un descuento parcial dice por qué no es completo", async () => {
      await openForm({ ...WITH_COUPON, coupon_reason: "quota_exhausted" });

      expect(summaryRow(/^Cupón AMOR26$/)).toContain("−$2.100");
      expect(screen.getByText(/descuento parcial: se agotaron las unidades con descuento/i)).toBeInTheDocument();
    });

    it("con el cupón a $0 manda el descuento esperado en 0 (el que vio el operador)", async () => {
      createAsync.mockResolvedValue(REGISTERED);
      await openForm(COUPON_AT_ZERO);

      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(1));
      expect(createAsync.mock.calls[0][0].expected_discount_cop).toBe(0);
      expect(createAsync.mock.calls[0][0]).not.toHaveProperty("dry_run");
    });

    it.each([
      [
        "un color que el producto no tiene",
        {
          error_detail: "invalid_variant_attribute",
          problems: ['Cubo Love no tiene el color "Verde" (opciones: Rosado, Azul)'],
        },
        /Cubo Love no tiene el color "Verde" \(opciones: Rosado, Azul\)/,
      ],
      [
        "cambiaron las unidades con descuento (sin montos)",
        { error_detail: "quota_changed" },
        /Cambiaron las unidades con descuento del cupón: revisa el total y vuelve a crear el pedido\./,
      ],
      [
        "otro pedido con el mismo cupón se está registrando",
        { error_detail: "quota_busy" },
        /Otro pedido con el mismo cupón se está registrando; intenta de nuevo en unos segundos\./,
      ],
      [
        "no se pueden leer las unidades del cupón",
        { error_detail: "quota_unavailable" },
        /No se pudieron leer las unidades con descuento del cupón.*No se registró nada/,
      ],
      [
        "Medusa lo rechazó y quedó guardado para reintentar",
        { error_detail: "medusa_api_error: HTTP 503 /admin/draft-orders: down", saved_for_retry: true },
        /quedó guardado y el sistema lo reintenta solo: no lo crees de nuevo/,
      ],
    ])("si el registro rechaza por %s lo explica", async (_case, rejection, message) => {
      createAsync.mockResolvedValue({ registered: false, order_id: null, ...rejection });
      await openForm(WITH_COUPON);

      clickCreate();

      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(screen.getByRole("dialog", { name: /crear pedido/i })).toBeInTheDocument();
    });

    it("sin editar ninguna línea manda el descuento que vio el operador", async () => {
      createAsync.mockResolvedValue(REGISTERED);
      await openForm(WITH_COUPON);

      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(1));
      expect(createAsync.mock.calls[0][0].expected_discount_cop).toBe(2100);
      expect(createAsync.mock.calls[0][0]).not.toHaveProperty("dry_run");
    });

    it("sin cupón no manda descuento esperado", async () => {
      createAsync.mockResolvedValue(REGISTERED);
      await openForm();

      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(1));
      expect(createAsync.mock.calls[0][0]).not.toHaveProperty("expected_discount_cop");
    });

    it.each([
      ["la cantidad", () =>
        fireEvent.change(screen.getByLabelText(/cantidad de cubo love/i), { target: { value: "3" } })],
      ["el color", () =>
        fireEvent.change(screen.getByLabelText(/color de cubo love/i), { target: { value: "Azul" } })],
      ["el aroma", () =>
        fireEvent.change(screen.getByLabelText(/aroma de cubo love/i), { target: { value: "Lavanda" } })],
      ["un producto agregado", () =>
        fireEvent.change(screen.getByLabelText(/agregar producto/i), {
          target: { value: "luz-serena||Lavanda / Blanco" },
        })],
      ["una línea quitada", () =>
        fireEvent.click(screen.getByRole("button", { name: /quitar cubo dos/i }))],
    ])(
      "al editar %s, el primer clic recalcula SIN registrar y el segundo registra con el descuento que vio",
      async (_what, edit) => {
        backendReplies(QUOTE_3_CUBOS);
        await openForm({ ...WITH_COUPON, items: [CUBO, { ...CUBO, handle: "cubo-dos", title: "Cubo Dos", coupon_units: 0, coupon_discount_cop: 0 }] });
        expect(screen.getByText("−$2.100")).toBeInTheDocument();

        edit();

        // El descuento viejo ya no vale: el resumen dice que se recalcula.
        expect(screen.queryByText("−$2.100")).not.toBeInTheDocument();
        expect(
          screen.getByText(/descuento del cupón: se recalcula al crear el pedido/i),
        ).toBeInTheDocument();

        clickCreate();

        // 1er clic: cálculo sin registrar (nada de instrucciones de pago).
        const notice = await screen.findByRole("status");
        expect(notice).toHaveTextContent(/el total es \$68\.800 \(descuento \$2\.100\)/);
        expect(createAsync).toHaveBeenCalledTimes(1);
        expect(createAsync.mock.calls[0][0].dry_run).toBe(true);
        expect(createAsync.mock.calls[0][0]).not.toHaveProperty("expected_discount_cop");
        expect(summaryRow(/^Total$/)).toContain("$68.800");
        expect(screen.queryByText(/pedido creado/i)).not.toBeInTheDocument();

        // 2º clic: registra contra el descuento que el operador acaba de ver.
        clickCreate();

        await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(2));
        expect(createAsync.mock.calls[1][0]).not.toHaveProperty("dry_run");
        expect(createAsync.mock.calls[1][0].expected_discount_cop).toBe(2100);
        expect(await screen.findByText(/pedido creado/i)).toBeInTheDocument();
      },
    );

    it("si después del cálculo vuelve a editar, recalcula otra vez antes de registrar", async () => {
      backendReplies(QUOTE_3_CUBOS);
      await openForm(WITH_COUPON);

      fireEvent.change(screen.getByLabelText(/cantidad de cubo love/i), { target: { value: "3" } });
      clickCreate();
      await screen.findByRole("status");
      fireEvent.change(screen.getByLabelText(/cantidad de cubo love/i), { target: { value: "4" } });

      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(2));
      expect(createAsync.mock.calls[1][0].dry_run).toBe(true);
    });

    it("si después del cálculo cambia la ciudad, recalcula el total (el envío depende de ella)", async () => {
      backendReplies(QUOTE_3_CUBOS);
      await openForm(WITH_COUPON);

      fireEvent.change(screen.getByLabelText(/cantidad de cubo love/i), { target: { value: "3" } });
      clickCreate();
      await screen.findByRole("status");
      fireEvent.change(screen.getByLabelText(/ciudad/i), { target: { value: "Medellín" } });
      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(2));
      expect(createAsync.mock.calls[1][0].dry_run).toBe(true);
    });

    it("si el cálculo falla lo explica y no registra", async () => {
      backendReplies({ registered: false, order_id: null, error_detail: "catalog_unavailable" });
      await openForm(WITH_COUPON);

      fireEvent.change(screen.getByLabelText(/cantidad de cubo love/i), { target: { value: "3" } });
      clickCreate();

      expect(await screen.findByRole("alert")).toHaveTextContent(/catálogo/i);
      expect(createAsync).toHaveBeenCalledTimes(1);
      expect(createAsync.mock.calls[0][0].dry_run).toBe(true);
    });

    it("si cambió el descuento muestra el total nuevo, lo adopta y el siguiente clic registra contra él", async () => {
      createAsync
        .mockResolvedValueOnce({
          registered: false,
          order_id: null,
          error_detail: "quota_changed",
          subtotal_cop: 42000,
          shipping_cop: 7900,
          discount_cop: 4200,
          total_cop: 45700,
        })
        .mockResolvedValueOnce(REGISTERED);
      await openForm(WITH_COUPON);

      clickCreate();

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Cambiaron las unidades con descuento del cupón: el total ahora es $45.700 (descuento $4.200). Si el cliente está de acuerdo, vuelve a crear el pedido.",
      );
      // El resumen muestra lo NUEVO (el descuento viejo y su reparto ya no valen).
      expect(summaryRow(/^Cupón AMOR26$/)).toContain("−$4.200");
      expect(summaryRow(/^Total$/)).toContain("$45.700");
      expect(screen.queryByText("−$2.100")).not.toBeInTheDocument();
      expect(screen.queryByText("1 de 2 con AMOR26 (−$2.100)")).not.toBeInTheDocument();

      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(2));
      expect(createAsync.mock.calls[1][0].expected_discount_cop).toBe(4200);
      expect(createAsync.mock.calls[1][0]).not.toHaveProperty("dry_run");
      expect(await screen.findByText(/pedido creado/i)).toBeInTheDocument();
    });

    it("si cambió el descuento pero no vinieron los montos, el siguiente clic recalcula antes de registrar", async () => {
      createAsync
        .mockResolvedValueOnce({ registered: false, order_id: null, error_detail: "quota_changed" })
        .mockResolvedValueOnce({ ...QUOTE_3_CUBOS, subtotal_cop: 42000, discount_cop: 0, total_cop: 49900 });
      await openForm(WITH_COUPON);

      clickCreate();
      await screen.findByRole("alert");
      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(2));
      expect(createAsync.mock.calls[1][0].dry_run).toBe(true);
      expect(await screen.findByRole("status")).toHaveTextContent(/el total es \$49\.900/);
    });

    it("un producto agregado a mano pide color y aroma de su lista del catálogo", async () => {
      backendReplies({ ...QUOTE_3_CUBOS, subtotal_cop: 21000, discount_cop: 0, total_cop: 28900 });
      await openForm({
        ...WITH_COUPON,
        items: [],
        subtotal_cop: 0,
        discount_cop: 0,
        total_cop: 7900,
        coupon_reason: "no_applicable_items",
        catalog: [
          {
            handle: "cubo-love",
            title: "Cubo Love",
            variants: [{ label: "", unit_price_cop: 21000 }],
            colors: ["Rosado", "Azul"],
            aromas: ["Café", "Lavanda"],
          },
        ],
      });

      fireEvent.change(screen.getByLabelText(/agregar producto/i), {
        target: { value: "cubo-love||" },
      });

      expect(screen.getByLabelText(/color de cubo love/i)).toHaveValue("");
      expect(
        screen.getByText(/el descuento del cupón necesita el color y el aroma/i),
      ).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText(/color de cubo love/i), { target: { value: "Azul" } });
      fireEvent.change(screen.getByLabelText(/aroma de cubo love/i), { target: { value: "Café" } });
      expect(screen.queryByText(/el descuento del cupón necesita/i)).not.toBeInTheDocument();

      clickCreate();
      await screen.findByRole("status");
      clickCreate();

      await waitFor(() => expect(createAsync).toHaveBeenCalledTimes(2));
      expect(createAsync.mock.calls[1][0].items).toEqual([
        { handle: "cubo-love", quantity: 1, color: "Azul", aroma: "Café" },
      ]);
      expect(createAsync.mock.calls[1][0].expected_discount_cop).toBe(0);
    });
  });

  it("si el pedido ya estaba registrado lo dice en vez de 'Pedido creado'", async () => {
    createAsync.mockResolvedValue({
      registered: true,
      already_registered: true,
      order_id: "order_1",
      order_reference: "#22 (Dúo Zodiacal)",
      total_cop: 111900,
    });
    await openForm();

    fireEvent.click(screen.getByRole("button", { name: /^crear pedido$/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      /este pedido ya estaba registrado: #22 \(dúo zodiacal\)/i,
    );
    expect(screen.queryByText(/pedido creado/i)).not.toBeInTheDocument();
  });
});
