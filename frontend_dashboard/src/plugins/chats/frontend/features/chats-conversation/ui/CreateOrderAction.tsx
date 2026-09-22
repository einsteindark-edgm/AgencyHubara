/**
 * Acción "Crear pedido" del composer en modo intervenido.
 *
 * El hueco que cierra (caso 2026-09-17, escalación
 * ORDER_PENDING_SHIPPING_DETAILS): el cliente no completó el
 * Flow de datos de envío, el bot escaló y el humano le sacó los datos a mano
 * por chat… y ahí la conversación se quedaba sin salida — el bot ya no
 * responde (ruta humano) y el operador no tenía ningún botón para registrar el
 * pedido. Los datos estaban EN la conversación y nadie los podía convertir en
 * una orden.
 *
 * Flujo: un click → el backend lee la conversación con DeepSeek
 * (`order-intake@v1`, read-only) → el formulario abre PRE-LLENADO con lo que
 * se pudo extraer → el operador corrige lo que haga falta → "Crear pedido"
 * registra por `session-actions@v1 /order` (el MISMO camino que usa Meta
 * Business Agent: precio server-side, idempotencia por contenido, cierre
 * "pago pendiente" + escalación de verificación de pago).
 *
 * Decisiones de UX:
 *  - **Nada se registra sin el click final del humano.** La extracción es una
 *    sugerencia con la evidencia a la vista (`field_sources` dice si un dato
 *    salió de la conversación, del breadcrumb del bot o del número de
 *    WhatsApp), no una acción.
 *  - **El precio no se edita**: sale del catálogo (misma regla que el agente).
 *    Lo que el operador elige es QUÉ producto y CUÁNTOS.
 *  - **El envío no se muestra como número editable**: la tarifa es mínima y la
 *    transportadora la recalcula; el total real vuelve en la respuesta del
 *    registro.
 *  - F5.3 (CLAUDE.md frontend, regla 3): el flujo multi-paso es un reducer con
 *    unión discriminada — "cargando" y "error" a la vez es irrepresentable.
 */
import { useReducer, useState } from "react";

import {
  useCreateOrderFromChat,
  useSuggestOrderFromChat,
  type CatalogOption,
  type IntakeFieldSource,
  type OrderSuggestion,
  type PaymentMethod,
} from "@plugins/chats/frontend/entities/order-intake";

interface Props {
  chatId: string | null;
  /**
   * Si el composer ofrece la acción (histórico elegible y SIN pedido
   * pendiente todavía). Solo gobierna el DISPARADOR: con el modal abierto el
   * componente sigue montado, así el aviso "Pedido creado #22" sobrevive a
   * que el registro mismo encienda `pending_payment_order_id` y el composer
   * pase esto a `false`.
   */
  available?: boolean;
}

interface FormItem {
  handle: string;
  title: string;
  variantLabel: string;
  quantity: number;
  unitPriceCop: number;
}

interface ShippingForm {
  city: string;
  neighborhood: string;
  address: string;
  phone: string;
  receiverName: string;
  nationalId: string;
}

const EMPTY_SHIPPING: ShippingForm = {
  city: "",
  neighborhood: "",
  address: "",
  phone: "",
  receiverName: "",
  nationalId: "",
};

type Phase =
  | { k: "reading" }
  | { k: "read_failed"; message: string }
  | { k: "form" }
  | { k: "submitting" }
  | { k: "submit_failed"; message: string }
  | { k: "done"; reference: string; paymentInstructionsSent: boolean };

type PhaseAction =
  | { type: "read" }
  | { type: "read_ok" }
  | { type: "read_fail"; message: string }
  | { type: "submit" }
  | { type: "submit_fail"; message: string }
  | { type: "submit_ok"; reference: string; paymentInstructionsSent: boolean };

function phaseReducer(_state: Phase, action: PhaseAction): Phase {
  switch (action.type) {
    case "read":
      return { k: "reading" };
    case "read_ok":
      return { k: "form" };
    case "read_fail":
      return { k: "read_failed", message: action.message };
    case "submit":
      return { k: "submitting" };
    case "submit_fail":
      return { k: "submit_failed", message: action.message };
    case "submit_ok":
      return {
        k: "done",
        reference: action.reference,
        paymentInstructionsSent: action.paymentInstructionsSent,
      };
  }
}

const SOURCE_LABEL: Record<NonNullable<IntakeFieldSource>, string> = {
  conversation: "de la conversación",
  draft: "del bot",
  session: "de WhatsApp",
};

const PAYMENT_LABEL: Record<PaymentMethod, string> = {
  transfer: "Transferencia / Nequi (pago anticipado)",
  payment_link: "Link de pago (con recargo)",
  cash_on_delivery: "Contra entrega",
};

/** `error_detail` del backend → algo que el operador entienda. */
const ERROR_LABEL: Record<string, string> = {
  catalog_unavailable:
    "El catálogo no está disponible en este momento: no se puede precisar el pedido. Reintentá en un minuto.",
  amount_mismatch:
    "Los montos no cuadran con los ítems. Revisá las cantidades y volvé a intentar.",
  missing_receiver_name: "Falta el nombre de quién recibe (lo exige la transportadora).",
  registration_failed:
    "Medusa rechazó el registro. El intento quedó guardado para reconciliar; reintentá o registralo a mano.",
};

const MISSING_LABEL: Record<string, string> = {
  city: "ciudad",
  address: "dirección",
  phone: "teléfono",
  receiver_name: "quién recibe",
  items: "productos",
  payment_method: "método de pago",
};

function formatCop(value: number): string {
  return `$${value.toLocaleString("es-CO")}`;
}

function itemsFrom(suggestion: OrderSuggestion): FormItem[] {
  return suggestion.items.map((item) => ({
    handle: item.handle,
    title: item.title,
    variantLabel: item.variant_label ?? "",
    quantity: item.quantity,
    unitPriceCop: item.unit_price_cop,
  }));
}

function shippingFrom(suggestion: OrderSuggestion): ShippingForm {
  return {
    city: suggestion.shipping.city ?? "",
    neighborhood: suggestion.shipping.neighborhood ?? "",
    address: suggestion.shipping.address ?? "",
    phone: suggestion.shipping.phone ?? "",
    receiverName: suggestion.shipping.receiver_name ?? "",
    nationalId: suggestion.shipping.national_id ?? "",
  };
}

/** Lo que falta para poder registrar — se deriva del FORMULARIO (no del
 *  `missing` del backend, que es una foto de la sugerencia inicial). */
function missingOf(
  shipping: ShippingForm,
  items: FormItem[],
  paymentMethod: PaymentMethod | "",
): string[] {
  const missing: string[] = [];
  if (!shipping.city.trim()) missing.push("city");
  if (!shipping.address.trim()) missing.push("address");
  if (!shipping.phone.trim()) missing.push("phone");
  if (!shipping.receiverName.trim()) missing.push("receiver_name");
  if (items.length === 0) missing.push("items");
  if (!paymentMethod) missing.push("payment_method");
  return missing;
}

export function CreateOrderAction({ chatId, available = true }: Props) {
  const [open, setOpen] = useState(false);
  const [phase, dispatch] = useReducer(phaseReducer, { k: "reading" } as Phase);
  const [suggestion, setSuggestion] = useState<OrderSuggestion | null>(null);
  const [items, setItems] = useState<FormItem[]>([]);
  const [shipping, setShipping] = useState<ShippingForm>(EMPTY_SHIPPING);
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | "">("");
  const [sendInstructions, setSendInstructions] = useState(true);

  const suggest = useSuggestOrderFromChat(chatId);
  const create = useCreateOrderFromChat(chatId);

  const read = async () => {
    dispatch({ type: "read" });
    try {
      const result = (await suggest.mutateAsync()) as OrderSuggestion;
      setSuggestion(result);
      setItems(itemsFrom(result));
      setShipping(shippingFrom(result));
      setPaymentMethod(result.payment_method ?? "");
      setSendInstructions(true);
      dispatch({ type: "read_ok" });
    } catch (error) {
      dispatch({
        type: "read_fail",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const openForm = () => {
    setOpen(true);
    void read();
  };

  const close = () => {
    setOpen(false);
    setSuggestion(null);
    setItems([]);
    setShipping(EMPTY_SHIPPING);
    setPaymentMethod("");
  };

  const missing = missingOf(shipping, items, paymentMethod);
  const subtotal = items.reduce((acc, it) => acc + it.unitPriceCop * it.quantity, 0);
  const busy = phase.k === "submitting";

  const submit = async () => {
    if (missing.length > 0 || busy || !paymentMethod) return;
    dispatch({ type: "submit" });
    try {
      const result = await create.mutateAsync({
        items: items.map((it) => ({
          handle: it.handle,
          ...(it.variantLabel ? { variant_label: it.variantLabel } : {}),
          quantity: it.quantity,
        })),
        shipping: {
          city: shipping.city.trim(),
          ...(shipping.neighborhood.trim()
            ? { neighborhood: shipping.neighborhood.trim() }
            : {}),
          address: shipping.address.trim(),
          phone: shipping.phone.trim(),
          receiver_name: shipping.receiverName.trim(),
          ...(shipping.nationalId.trim()
            ? { national_id: shipping.nationalId.trim() }
            : {}),
        },
        payment_method: paymentMethod,
        send_payment_instructions: sendInstructions,
      });
      if (!result.registered) {
        const detail = result.error_detail ?? "";
        dispatch({
          type: "submit_fail",
          message:
            ERROR_LABEL[detail] ??
            (result.problems?.length
              ? `No se pudo registrar: ${result.problems.join("; ")}`
              : `No se pudo registrar el pedido${detail ? ` (${detail})` : ""}.`),
        });
        return;
      }
      dispatch({
        type: "submit_ok",
        reference: result.order_reference ?? result.order_id ?? "",
        paymentInstructionsSent: Boolean(result.payment_instructions_sent),
      });
    } catch (error) {
      dispatch({
        type: "submit_fail",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

  if (!available && !open) return null;

  return (
    <>
      {available && (
        <button
          type="button"
          className="create-order-btn"
          style={triggerStyle}
          onClick={openForm}
          disabled={!chatId}
          title="Crear el pedido con los datos que aparecen en la conversación"
        >
          🧾 Crear pedido
        </button>
      )}

      {open && (
        <div style={overlayStyle} onClick={close}>
          <div
            role="dialog"
            aria-label="Crear pedido"
            style={modalStyle}
            onClick={(e) => e.stopPropagation()}
          >
            <header style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <h2 style={{ margin: 0, fontSize: "0.95rem" }}>Crear pedido</h2>
              <span style={mutedStyle}>
                {phase.k === "reading"
                  ? "Leyendo la conversación…"
                  : suggestion
                    ? `${suggestion.messages_considered} mensajes leídos`
                    : ""}
              </span>
            </header>

            {phase.k === "reading" && (
              <p style={mutedStyle}>
                Leyendo la conversación para armar el pedido…
              </p>
            )}

            {phase.k === "read_failed" && (
              <>
                <div role="alert" style={errStyle}>
                  No se pudo leer la conversación: {phase.message}
                </div>
                <div style={footerStyle}>
                  <button type="button" style={ghostStyle} onClick={close}>
                    Cancelar
                  </button>
                  <button type="button" style={primaryStyle} onClick={() => void read()}>
                    Reintentar
                  </button>
                </div>
              </>
            )}

            {phase.k === "done" && (
              <>
                <div style={okStyle}>
                  Pedido creado: <b>{phase.reference}</b>. La conversación queda
                  con el pago pendiente de verificación — cuando el cliente
                  pague, usá "Confirmar pago".
                  {phase.paymentInstructionsSent
                    ? " Ya le enviamos las instrucciones de pago."
                    : ""}
                </div>
                <div style={footerStyle}>
                  <button type="button" style={primaryStyle} onClick={close}>
                    Listo
                  </button>
                </div>
              </>
            )}

            {suggestion && (phase.k === "form" || phase.k === "submitting" || phase.k === "submit_failed") && (
              <>
                {suggestion.degraded && (
                  <div style={warnStyle}>
                    No se pudo leer la conversación automáticamente
                    {suggestion.error_detail ? ` (${suggestion.error_detail})` : ""}.
                    El formulario abre con lo que el bot había anotado —
                    completá lo que falte a mano.
                  </div>
                )}
                {suggestion.already_registered_order_id && (
                  <div style={warnStyle}>
                    Ojo: esta conversación ya tiene un pedido registrado (
                    {suggestion.already_registered_order_id}). Crear otro suma
                    un pedido nuevo.
                  </div>
                )}
                {suggestion.warnings.map((warning) => (
                  <div key={warning} style={warnStyle}>
                    {warning}
                  </div>
                ))}
                {suggestion.notes && (
                  <div style={mutedStyle}>Nota del modelo: {suggestion.notes}</div>
                )}

                <section>
                  <h3 style={sectionTitleStyle}>Productos</h3>
                  {items.length === 0 && (
                    <p style={mutedStyle}>
                      Sin productos: elegilos abajo.
                    </p>
                  )}
                  {items.map((item, index) => (
                    <div key={`${item.handle}-${item.variantLabel}-${index}`} style={itemRowStyle}>
                      <span style={{ flex: 1 }}>
                        {item.title}
                        {item.variantLabel ? ` · ${item.variantLabel}` : ""}
                        <span style={mutedStyle}> {formatCop(item.unitPriceCop)} c/u</span>
                      </span>
                      <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                        <span style={lblStyle}>Cantidad de {item.title}</span>
                        <input
                          type="number"
                          min={1}
                          max={500}
                          value={item.quantity}
                          style={{ ...inputStyle, width: 64 }}
                          onChange={(e) =>
                            setItems((prev) =>
                              prev.map((it, i) =>
                                i === index
                                  ? { ...it, quantity: Math.max(1, Number(e.target.value) || 1) }
                                  : it,
                              ),
                            )
                          }
                        />
                      </label>
                      <button
                        type="button"
                        aria-label={`Quitar ${item.title}`}
                        style={ghostStyle}
                        onClick={() => setItems((prev) => prev.filter((_, i) => i !== index))}
                      >
                        ✕
                      </button>
                    </div>
                  ))}

                  <AddItem
                    catalog={suggestion.catalog}
                    onAdd={(item) => setItems((prev) => [...prev, item])}
                  />
                </section>

                <section style={gridStyle}>
                  <Field
                    label="Ciudad"
                    value={shipping.city}
                    source={suggestion.field_sources.city ?? null}
                    onChange={(v) => setShipping((s) => ({ ...s, city: v }))}
                  />
                  <Field
                    label="Barrio"
                    value={shipping.neighborhood}
                    source={suggestion.field_sources.neighborhood ?? null}
                    onChange={(v) => setShipping((s) => ({ ...s, neighborhood: v }))}
                  />
                  <Field
                    label="Dirección"
                    value={shipping.address}
                    source={suggestion.field_sources.address ?? null}
                    onChange={(v) => setShipping((s) => ({ ...s, address: v }))}
                  />
                  <Field
                    label="Teléfono"
                    value={shipping.phone}
                    source={suggestion.field_sources.phone ?? null}
                    onChange={(v) => setShipping((s) => ({ ...s, phone: v }))}
                  />
                  <Field
                    label="Recibe"
                    value={shipping.receiverName}
                    source={suggestion.field_sources.receiver_name ?? null}
                    onChange={(v) => setShipping((s) => ({ ...s, receiverName: v }))}
                  />
                  <Field
                    label="Cédula (opcional)"
                    value={shipping.nationalId}
                    source={suggestion.field_sources.national_id ?? null}
                    onChange={(v) => setShipping((s) => ({ ...s, nationalId: v }))}
                  />
                </section>

                <label style={fieldStyle}>
                  <span style={lblStyle}>
                    Método de pago
                    <SourceBadge source={suggestion.field_sources.payment_method ?? null} />
                  </span>
                  <select
                    value={paymentMethod}
                    style={inputStyle}
                    onChange={(e) => setPaymentMethod(e.target.value as PaymentMethod | "")}
                  >
                    <option value="">— elegir —</option>
                    {(Object.keys(PAYMENT_LABEL) as PaymentMethod[]).map((method) => (
                      <option key={method} value={method}>
                        {PAYMENT_LABEL[method]}
                      </option>
                    ))}
                  </select>
                </label>

                {(paymentMethod === "transfer" || paymentMethod === "payment_link") && (
                  <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input
                      type="checkbox"
                      checked={sendInstructions}
                      onChange={(e) => setSendInstructions(e.target.checked)}
                    />
                    <span style={{ fontSize: "0.72rem" }}>
                      Enviarle las instrucciones de pago al cliente (datos
                      bancarios / link). Desactivalo si ya se los pasaste vos.
                    </span>
                  </label>
                )}

                <div style={totalsStyle}>
                  <span>Productos</span>
                  <span>{formatCop(subtotal)}</span>
                </div>
                {suggestion && suggestion.discount_cop > 0 && (
                  <div style={totalsStyle}>
                    <span>
                      Descuento cupón {suggestion.coupon_code ?? ""} (aplicado en el chat)
                    </span>
                    <span>−{formatCop(suggestion.discount_cop)}</span>
                  </div>
                )}
                <p style={mutedStyle}>
                  El envío se calcula al crear el pedido (tarifa mínima según la
                  ciudad) y la transportadora lo confirma al despachar.
                </p>

                {missing.length > 0 && (
                  <div style={warnStyle}>
                    Falta completar: {missing.map((m) => MISSING_LABEL[m] ?? m).join(", ")}.
                  </div>
                )}
                {phase.k === "submit_failed" && (
                  <div role="alert" style={errStyle}>
                    {phase.message}
                  </div>
                )}

                <div style={footerStyle}>
                  <button type="button" style={ghostStyle} onClick={close} disabled={busy}>
                    Cancelar
                  </button>
                  <button
                    type="button"
                    style={{ ...primaryStyle, opacity: missing.length || busy ? 0.6 : 1 }}
                    disabled={missing.length > 0 || busy}
                    onClick={() => void submit()}
                  >
                    {busy ? "Creando…" : "Crear pedido"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function SourceBadge({ source }: { source: IntakeFieldSource }) {
  if (!source) return null;
  return <span style={badgeStyle}>{SOURCE_LABEL[source]}</span>;
}

interface FieldProps {
  label: string;
  value: string;
  source: IntakeFieldSource;
  onChange: (value: string) => void;
}

function Field({ label, value, source, onChange }: FieldProps) {
  return (
    <label style={fieldStyle}>
      <span style={lblStyle}>
        {label}
        <SourceBadge source={source} />
      </span>
      <input
        type="text"
        value={value}
        style={inputStyle}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/** Selector de la lista CERRADA del catálogo (producto × variante): el
 *  operador corrige lo que eligió el modelo sin poder inventar un producto. */
function AddItem({
  catalog,
  onAdd,
}: {
  catalog: CatalogOption[];
  onAdd: (item: FormItem) => void;
}) {
  return (
    <label style={fieldStyle}>
      <span style={lblStyle}>Agregar producto</span>
      <select
        value=""
        style={inputStyle}
        disabled={catalog.length === 0}
        onChange={(e) => {
          const [handle, label] = e.target.value.split("||");
          const product = catalog.find((p) => p.handle === handle);
          const variant = product?.variants.find((v) => v.label === label);
          if (!product || !variant) return;
          onAdd({
            handle: product.handle,
            title: product.title,
            variantLabel: variant.label,
            quantity: 1,
            unitPriceCop: variant.unit_price_cop,
          });
        }}
      >
        <option value="">
          {catalog.length === 0 ? "catálogo no disponible" : "— elegir producto —"}
        </option>
        {catalog.flatMap((product) =>
          product.variants.map((variant) => (
            <option
              key={`${product.handle}||${variant.label}`}
              value={`${product.handle}||${variant.label}`}
            >
              {product.title}
              {variant.label ? ` · ${variant.label}` : ""} — {formatCop(variant.unit_price_cop)}
            </option>
          )),
        )}
      </select>
    </label>
  );
}

/* ── styles ──────────────────────────────────────────────────────────────
   Inline (el composer ya usa ese criterio para no tocar el `index.css`
   spinal) pero con los TOKENS REALES del tema — que es oscuro:
   `--fg/--fg-soft/--fg-faint`, `--line/--line-strong`, `--accent`,
   `--statusbar`, `--color-ok/warn/danger(-soft)` (ver `:root` de
   `src/index.css`). La primera versión inventó nombres (`--bg-elev`,
   `--stroke`, `--bg-soft`) cuyos FALLBACKS eran blancos: el botón salía
   blanco con texto gris clarito y el modal, blanco con texto claro —
   ilegible. Regla: si la var no existe en `:root`, el fallback manda; usar
   solo tokens del tema y fallbacks oscuros.
   Los `background` de inputs/selects van OPACOS (no rgba) porque el popup
   nativo del `<select>` no hereda el fondo del control. */

/** Mismo lenguaje visual que los otros dos botones del composer
 *  (📅 Asignar fecha azul, 💳 Confirmar pago verde): pastilla sólida con
 *  texto blanco y halo. Violeta para que se distinga de un vistazo. */
const triggerStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  marginRight: "0.5rem",
  padding: "0.35rem 0.7rem",
  borderRadius: "8px",
  border: "none",
  fontSize: "0.78rem",
  fontWeight: 700,
  color: "#fff",
  background: "#7c3aed",
  boxShadow: "0 0 0 2px rgba(124,58,237,0.35)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 60,
  padding: 16,
};

const modalStyle: React.CSSProperties = {
  width: "min(560px, 100%)",
  maxHeight: "90vh",
  overflowY: "auto",
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 16,
  borderRadius: 12,
  background: "var(--statusbar, #1c1c1e)",
  color: "var(--fg, #ebebeb)",
  border: "1px solid var(--line-strong, rgba(255,255,255,0.14))",
  boxShadow: "var(--shadow-pop, 0 10px 30px rgba(0,0,0,0.55))",
  fontSize: "0.78rem",
};

const sectionTitleStyle: React.CSSProperties = {
  margin: "4px 0",
  fontSize: "0.78rem",
  fontWeight: 700,
  color: "var(--fg, #ebebeb)",
};

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
  gap: 8,
};

const fieldStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 3,
};

const lblStyle: React.CSSProperties = {
  fontSize: "0.68rem",
  fontWeight: 600,
  color: "var(--fg-mute, rgba(235,235,235,0.55))",
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
};

const inputStyle: React.CSSProperties = {
  padding: "5px 7px",
  borderRadius: 6,
  border: "1px solid var(--line-strong, rgba(255,255,255,0.14))",
  fontSize: "0.76rem",
  background: "#242427",
  color: "var(--fg, #ebebeb)",
};

const badgeStyle: React.CSSProperties = {
  fontSize: "0.6rem",
  fontWeight: 500,
  padding: "1px 5px",
  borderRadius: 999,
  background: "rgba(255,255,255,0.08)",
  color: "var(--fg-faint, rgba(235,235,235,0.38))",
};

const itemRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "4px 0",
  borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
};

const totalsStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  fontWeight: 700,
  color: "var(--fg, #ebebeb)",
};

const mutedStyle: React.CSSProperties = {
  fontSize: "0.68rem",
  color: "var(--fg-faint, rgba(235,235,235,0.38))",
};

const warnStyle: React.CSSProperties = {
  fontSize: "0.7rem",
  padding: "6px 8px",
  borderRadius: 6,
  background: "var(--color-warn-soft, rgba(255,180,74,0.18))",
  color: "var(--color-warn, #ffb44a)",
};

const errStyle: React.CSSProperties = {
  fontSize: "0.7rem",
  padding: "6px 8px",
  borderRadius: 6,
  background: "var(--color-danger-soft, rgba(255,114,105,0.18))",
  color: "var(--color-danger, #ff7269)",
};

const okStyle: React.CSSProperties = {
  fontSize: "0.74rem",
  padding: "8px 10px",
  borderRadius: 6,
  background: "var(--color-ok-soft, rgba(91,224,123,0.18))",
  color: "var(--color-ok, #5be07b)",
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
  marginTop: 4,
};

const ghostStyle: React.CSSProperties = {
  padding: "5px 10px",
  borderRadius: 6,
  border: "1px solid var(--line-strong, rgba(255,255,255,0.14))",
  background: "rgba(255,255,255,0.06)",
  fontSize: "0.72rem",
  cursor: "pointer",
  color: "var(--fg-soft, rgba(235,235,235,0.78))",
};

const primaryStyle: React.CSSProperties = {
  padding: "5px 12px",
  borderRadius: 6,
  border: "none",
  background: "var(--accent, #0a84ff)",
  color: "#fff",
  fontSize: "0.72rem",
  fontWeight: 600,
  cursor: "pointer",
};


export default CreateOrderAction;
