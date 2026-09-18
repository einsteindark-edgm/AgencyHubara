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

vi.mock("@plugins/chats/frontend/entities/order-intake", () => ({
  useSuggestOrderFromChat: () => ({
    mutateAsync: suggestAsync,
    ...suggestState,
  }),
  useCreateOrderFromChat: () => ({
    mutateAsync: createAsync,
    ...createState,
  }),
}));

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
});
