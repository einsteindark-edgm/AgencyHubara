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
 *  - **Cupo por unidad** (CUPONES_PLAN fase 6): cada línea pide color y aroma
 *    de las listas CERRADAS del producto (solo si las tiene) y muestra
 *    cuántas unidades llevan el descuento del cupón; el registro relee el
 *    reparto bajo el candado del código (`quota_changed` / `quota_busy`).
 *  - **Se registra contra lo que el operador VIO** (`CouponQuote`): el
 *    descuento de la sugerencia; tras editar líneas, un primer clic calcula
 *    sin registrar (`dry_run`) y muestra el total, y el segundo registra; un
 *    `quota_changed` con montos se adopta como lo nuevo que vio.
 *  - F5.3 (CLAUDE.md frontend, regla 3): el flujo multi-paso es un reducer con
 *    unión discriminada — "cargando" y "error" a la vez es irrepresentable.
 */
import { useReducer, useState } from "react";

import {
  useCreateOrderFromChat,
  useSuggestOrderFromChat,
  type CatalogOption,
  type CreateOrderResult,
  type CreateOrderVariables,
  type IntakeFieldSource,
  type OrderSuggestion,
  type PaymentMethod,
} from "@plugins/chats/frontend/entities/order-intake";

import {
  couponReasonLabel,
  quoteFromResult,
  quoteFromSuggestion,
  type CouponQuote,
} from "../model/orderQuote";

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
  /** Color/aroma elegido de la lista del producto ("" = sin elegir). */
  color: string;
  aroma: string;
  /** Listas CERRADAS del producto (vacía = el producto no tiene el atributo). */
  colors: string[];
  aromas: string[];
  /** Reparto del cupón que calculó la sugerencia para ESTA línea (solo vale
   *  mientras la cotización sea la de la sugerencia y no esté vieja). */
  couponUnits: number;
  couponDiscountCop: number;
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
  /** Calculando el pedido editado SIN registrar (`dry_run`). */
  | { k: "quoting" }
  /** Cálculo listo: el operador revisa el total y vuelve a hacer clic. */
  | { k: "quoted" }
  | { k: "submitting" }
  | { k: "submit_failed"; message: string }
  | {
      k: "done";
      reference: string;
      paymentInstructionsSent: boolean;
      totalCop: number | null;
      /** El backend reconoció el mismo pedido ya registrado (C4). */
      alreadyRegistered: boolean;
    };

type PhaseAction =
  | { type: "read" }
  | { type: "read_ok" }
  | { type: "read_fail"; message: string }
  | { type: "quote" }
  | { type: "quote_ok" }
  | { type: "submit" }
  | { type: "submit_fail"; message: string }
  | {
      type: "submit_ok";
      reference: string;
      paymentInstructionsSent: boolean;
      totalCop: number | null;
      alreadyRegistered: boolean;
    };

function phaseReducer(_state: Phase, action: PhaseAction): Phase {
  switch (action.type) {
    case "read":
      return { k: "reading" };
    case "read_ok":
      return { k: "form" };
    case "read_fail":
      return { k: "read_failed", message: action.message };
    case "quote":
      return { k: "quoting" };
    case "quote_ok":
      return { k: "quoted" };
    case "submit":
      return { k: "submitting" };
    case "submit_fail":
      return { k: "submit_failed", message: action.message };
    case "submit_ok":
      return {
        k: "done",
        reference: action.reference,
        paymentInstructionsSent: action.paymentInstructionsSent,
        totalCop: action.totalCop,
        alreadyRegistered: action.alreadyRegistered,
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
    "El catálogo no está disponible en este momento: no se puede precisar el pedido. Intenta de nuevo en un minuto.",
  amount_mismatch:
    "Los montos no cuadran con los ítems. Revisa las cantidades y vuelve a intentar.",
  missing_receiver_name: "Falta el nombre de quién recibe (lo exige la transportadora).",
  registration_failed:
    "Medusa rechazó el registro. El intento quedó guardado para reconciliar; intenta de nuevo o regístralo a mano.",
  // Cupo por unidad: el reparto del cupón se relee bajo candado al registrar.
  quota_changed:
    "Cambiaron las unidades con descuento del cupón: revisa el total y vuelve a crear el pedido.",
  quota_busy:
    "Otro pedido con el mismo cupón se está registrando; intenta de nuevo en unos segundos.",
  quota_unavailable:
    "No se pudieron leer las unidades con descuento del cupón (Medusa no respondió). No se registró nada: intenta de nuevo en un minuto.",
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

/** Mensaje de un registro rechazado. `quota_changed` con montos dice el total
 *  nuevo: el operador tiene que verlo antes de volver a crear el pedido. */
function rejectionMessage(result: CreateOrderResult): string {
  const detail = result.error_detail ?? "";
  if (result.saved_for_retry) {
    return (
      `No se pudo registrar ahora${detail ? ` (${detail})` : ""}. El intento quedó guardado y el ` +
      "sistema lo reintenta solo: no lo crees de nuevo; míralo en Pedidos."
    );
  }
  if (detail === "quota_changed" && result.total_cop !== null && result.discount_cop !== null) {
    // El formulario ADOPTA estos montos: el siguiente clic registra contra ellos.
    return (
      "Cambiaron las unidades con descuento del cupón: el total ahora es " +
      `${formatCop(result.total_cop)} (descuento ${formatCop(result.discount_cop)}). ` +
      "Si el cliente está de acuerdo, vuelve a crear el pedido."
    );
  }
  return (
    ERROR_LABEL[detail] ??
    (result.problems.length
      ? `No se pudo registrar: ${result.problems.join("; ")}`
      : `No se pudo registrar el pedido${detail ? ` (${detail})` : ""}.`)
  );
}

function itemsFrom(suggestion: OrderSuggestion): FormItem[] {
  return suggestion.items.map((item) => ({
    handle: item.handle,
    title: item.title,
    variantLabel: item.variant_label ?? "",
    quantity: item.quantity,
    unitPriceCop: item.unit_price_cop,
    color: item.color ?? "",
    aroma: item.aroma ?? "",
    colors: item.colors,
    aromas: item.aromas,
    couponUnits: item.coupon_units,
    couponDiscountCop: item.coupon_discount_cop,
  }));
}

/** Color/aroma para el cuerpo del registro: solo los atributos que el
 *  producto TIENE (lista no vacía) y que el operador eligió. */
function variantAttrsOf(item: FormItem): { color?: string; aroma?: string } {
  return {
    ...(item.colors.length > 0 && item.color ? { color: item.color } : {}),
    ...(item.aromas.length > 0 && item.aroma ? { aroma: item.aroma } : {}),
  };
}

/** Aviso de la línea cuando hay cupón y falta el color o el aroma que el
 *  cupo por unidad necesita para contarla; `null` si no falta nada. */
function couponHintOf(item: FormItem): string | null {
  const missingColor = item.colors.length > 0 && !item.color;
  const missingAroma = item.aromas.length > 0 && !item.aroma;
  if (!missingColor && !missingAroma) return null;
  const needs = [
    item.colors.length > 0 ? "el color" : null,
    item.aromas.length > 0 ? "el aroma" : null,
  ].filter(Boolean);
  const pick = needs.length > 1 ? "elígelos" : "elígelo";
  return `El descuento del cupón necesita ${needs.join(" y ")} de este producto: ${pick} para que se aplique.`;
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
  /** Lo que el operador VIO del cupón (null = sin cupón): contra ese
   *  descuento se registra. */
  const [quote, setQuote] = useState<CouponQuote | null>(null);
  /** Cambió algo que mueve el descuento o el total después de verlo (una
   *  línea; la ciudad si ya se mostró el total): el próximo clic recalcula
   *  sin registrar antes de registrar. */
  const [quoteStale, setQuoteStale] = useState(false);

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
      setQuote(quoteFromSuggestion(result));
      setQuoteStale(false);
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
    setQuote(null);
    setQuoteStale(false);
  };

  /** Cambia las líneas. El descuento y el reparto que vio el operador dejan
   *  de valer: el próximo clic los recalcula (sin registrar) y se los muestra. */
  const changeItems = (update: (prev: FormItem[]) => FormItem[]) => {
    setItems(update);
    setQuoteStale(true);
  };
  const editItem = (index: number, patch: Partial<Pick<FormItem, "quantity" | "color" | "aroma">>) =>
    changeItems((prev) => prev.map((it, i) => (i === index ? { ...it, ...patch } : it)));
  const changeCity = (city: string) => {
    setShipping((s) => ({ ...s, city }));
    // El envío sale de la ciudad: un total ya mostrado deja de valer.
    if (quote?.totals) setQuoteStale(true);
  };
  const hasCoupon = quote !== null;
  /** La cotización vigente (la que el operador tiene a la vista). */
  const shownQuote = quote && !quoteStale ? quote : null;
  const shownTotals = shownQuote?.totals ?? null;

  const missing = missingOf(shipping, items, paymentMethod);
  const subtotal = items.reduce((acc, it) => acc + it.unitPriceCop * it.quantity, 0);
  const busy = phase.k === "submitting" || phase.k === "quoting";

  const orderBody = (method: PaymentMethod): CreateOrderVariables => ({
    items: items.map((it) => ({
      handle: it.handle,
      ...(it.variantLabel ? { variant_label: it.variantLabel } : {}),
      quantity: it.quantity,
      ...variantAttrsOf(it),
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
    payment_method: method,
    send_payment_instructions: sendInstructions,
  });

  const submit = async () => {
    if (missing.length > 0 || busy || !paymentMethod) return;
    // Con cupón y algo cambiado desde que el operador vio el descuento: este
    // clic SOLO calcula (nada se registra ni se le manda al cliente).
    const recalc = quote !== null && quoteStale;
    dispatch({ type: recalc ? "quote" : "submit" });
    try {
      const result = await create.mutateAsync({
        ...orderBody(paymentMethod),
        ...(recalc
          ? { dry_run: true }
          : quote
            ? // El descuento que el operador VIO (0 incluido): si el backend
              // calcula otro, responde `quota_changed` y no registra.
              { expected_discount_cop: quote.discountCop }
            : {}),
      });
      if (result.dry_run) {
        const fresh = quote ? quoteFromResult(quote, result) : null;
        if (!fresh) {
          dispatch({ type: "submit_fail", message: "No se pudo calcular el total. Intenta de nuevo." });
          return;
        }
        setQuote(fresh);
        setQuoteStale(false);
        dispatch({ type: "quote_ok" });
        return;
      }
      if (!result.registered) {
        if (result.error_detail === "quota_changed" && quote) {
          // Lo nuevo es lo que el operador ve ahora: el siguiente clic
          // registra contra ESO (sin montos, recalcula antes de registrar).
          const fresh = quoteFromResult(quote, result);
          if (fresh) setQuote(fresh);
          setQuoteStale(fresh === null);
        }
        dispatch({ type: "submit_fail", message: rejectionMessage(result) });
        return;
      }
      dispatch({
        type: "submit_ok",
        reference: result.order_reference ?? result.order_id ?? "",
        paymentInstructionsSent: Boolean(result.payment_instructions_sent),
        totalCop: result.total_cop,
        alreadyRegistered: result.already_registered,
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
                <div role="status" style={okStyle}>
                  {phase.alreadyRegistered ? (
                    <>
                      Este pedido ya estaba registrado: <b>{phase.reference}</b>.
                      {phase.totalCop !== null
                        ? ` Total registrado: ${formatCop(phase.totalCop)} (con envío).`
                        : ""}{" "}
                      No se creó otro ni se reenviaron las instrucciones de pago.
                    </>
                  ) : (
                    <>
                      Pedido creado: <b>{phase.reference}</b>.
                      {phase.totalCop !== null
                        ? ` Total registrado: ${formatCop(phase.totalCop)} (con envío).`
                        : ""}{" "}
                      La conversación queda
                      con el pago pendiente de verificación — cuando el cliente
                      pague, usa "Confirmar pago".
                      {phase.paymentInstructionsSent
                        ? " Ya le enviamos las instrucciones de pago."
                        : ""}
                    </>
                  )}
                </div>
                <div style={footerStyle}>
                  <button type="button" style={primaryStyle} onClick={close}>
                    Listo
                  </button>
                </div>
              </>
            )}

            {suggestion &&
              (phase.k === "form" ||
                phase.k === "quoting" ||
                phase.k === "quoted" ||
                phase.k === "submitting" ||
                phase.k === "submit_failed") && (
              <>
                {suggestion.degraded && (
                  <div style={warnStyle}>
                    No se pudo leer la conversación automáticamente
                    {suggestion.error_detail ? ` (${suggestion.error_detail})` : ""}.
                    El formulario abre con lo que el bot había anotado —
                    completa lo que falte a mano.
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
                      Sin productos: elígelos abajo.
                    </p>
                  )}
                  {items.map((item, index) => {
                    const couponHint = hasCoupon ? couponHintOf(item) : null;
                    return (
                      <div key={`${item.handle}-${item.variantLabel}-${index}`} style={itemBlockStyle}>
                        <div style={itemRowStyle}>
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
                                editItem(index, {
                                  quantity: Math.max(1, Number(e.target.value) || 1),
                                })
                              }
                            />
                          </label>
                          <button
                            type="button"
                            aria-label={`Quitar ${item.title}`}
                            style={ghostStyle}
                            onClick={() => changeItems((prev) => prev.filter((_, i) => i !== index))}
                          >
                            ✕
                          </button>
                        </div>
                        {(item.colors.length > 0 || item.aromas.length > 0) && (
                          <div style={attrsRowStyle}>
                            {item.colors.length > 0 && (
                              <AttrSelect
                                label={`Color de ${item.title}`}
                                value={item.color}
                                options={item.colors}
                                onChange={(color) => editItem(index, { color })}
                              />
                            )}
                            {item.aromas.length > 0 && (
                              <AttrSelect
                                label={`Aroma de ${item.title}`}
                                value={item.aroma}
                                options={item.aromas}
                                onChange={(aroma) => editItem(index, { aroma })}
                              />
                            )}
                          </div>
                        )}
                        {shownQuote?.source === "suggestion" && item.couponUnits > 0 && (
                          <span style={couponLineStyle}>
                            {`${item.couponUnits} de ${item.quantity} con ${shownQuote.code} (−${formatCop(item.couponDiscountCop)})`}
                          </span>
                        )}
                        {couponHint && <span style={hintStyle}>{couponHint}</span>}
                      </div>
                    );
                  })}

                  <AddItem
                    catalog={suggestion.catalog}
                    onAdd={(item) => changeItems((prev) => [...prev, item])}
                  />
                </section>

                <section style={gridStyle}>
                  <Field
                    label="Ciudad"
                    value={shipping.city}
                    source={suggestion.field_sources.city ?? null}
                    onChange={changeCity}
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
                      bancarios / link). Desactívalo si ya se los pasaste tú.
                    </span>
                  </label>
                )}

                <div style={totalsStyle}>
                  <span>Productos</span>
                  <span>{formatCop(shownTotals ? shownTotals.subtotalCop : subtotal)}</span>
                </div>
                {shownQuote && <CouponSummary quote={shownQuote} />}
                {quote && quoteStale && (
                  <div style={totalsStyle}>
                    <span>Descuento del cupón: se recalcula al crear el pedido</span>
                  </div>
                )}
                {shownTotals ? (
                  <>
                    <div style={totalsStyle}>
                      <span>Envío</span>
                      <span>{formatCop(shownTotals.shippingCop)}</span>
                    </div>
                    <div style={totalsStyle}>
                      <span>Total</span>
                      <span>{formatCop(shownTotals.totalCop)}</span>
                    </div>
                  </>
                ) : (
                  <p style={mutedStyle}>
                    El envío se calcula al crear el pedido (tarifa mínima según la
                    ciudad) y la transportadora lo confirma al despachar.
                  </p>
                )}

                {phase.k === "quoted" && shownTotals && (
                  <div role="status" style={infoStyle}>
                    Recalculamos el pedido: el total es {formatCop(shownTotals.totalCop)} (
                    {shownQuote && shownQuote.discountCop > 0
                      ? `descuento ${formatCop(shownQuote.discountCop)}`
                      : "sin descuento del cupón"}
                    ). Si el cliente está de acuerdo, haz clic otra vez en «Crear pedido»
                    para registrarlo.
                  </div>
                )}
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
                    {phase.k === "quoting"
                      ? "Calculando…"
                      : phase.k === "submitting"
                        ? "Creando…"
                        : "Crear pedido"}
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

/** Fila del cupón en el resumen — también a $0 (C-2), con el motivo: que el
 *  cupón no desaparezca justo cuando el operador tiene que corregir algo. */
function CouponSummary({ quote }: { quote: CouponQuote }) {
  const reason = couponReasonLabel(quote.reason);
  if (quote.discountCop <= 0) {
    return (
      <div style={{ ...totalsStyle, color: "var(--color-warn, #ffb44a)" }}>
        <span>{`Cupón ${quote.code}: sin descuento${reason ? ` — ${reason}` : ""}`}</span>
      </div>
    );
  }
  return (
    <>
      <div style={totalsStyle}>
        <span>{`Cupón ${quote.code}`}</span>
        <span>−{formatCop(quote.discountCop)}</span>
      </div>
      {reason && <span style={hintStyle}>{`Descuento parcial: ${reason}.`}</span>}
    </>
  );
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

/** Selector de color/aroma: solo los valores de la lista CERRADA del
 *  producto (un valor fuera de la lista no registra nada en el backend). */
function AttrSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={lblStyle}>{label}</span>
      <select value={value} style={inputStyle} onChange={(e) => onChange(e.target.value)}>
        <option value="">— elegir —</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
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
            // Las listas vienen del catálogo: la línea manual pide color y
            // aroma igual que una sugerida.
            color: "",
            aroma: "",
            colors: product.colors,
            aromas: product.aromas,
            couponUnits: 0,
            couponDiscountCop: 0,
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

const itemBlockStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  padding: "4px 0",
  borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
};

const itemRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const attrsRowStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
};

const couponLineStyle: React.CSSProperties = {
  fontSize: "0.68rem",
  fontWeight: 600,
  color: "var(--color-ok, #5be07b)",
};

const hintStyle: React.CSSProperties = {
  fontSize: "0.68rem",
  color: "var(--color-warn, #ffb44a)",
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

const infoStyle: React.CSSProperties = {
  fontSize: "0.72rem",
  padding: "6px 8px",
  borderRadius: 6,
  background: "var(--color-info-soft, rgba(95,169,255,0.18))",
  color: "var(--color-info, #5fa9ff)",
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
