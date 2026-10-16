import type { ChatDeliveryItem } from "@plugins/chats/frontend/entities/chat";

const STATUS_LABEL = { sent: "Enviado", delivered: "Entregado", read: "Leído" } as const;

/** ✓ o ✓✓ al estilo de WhatsApp (glifo local de la burbuja, no del set base). */
function Ticks({ double }: { double: boolean }) {
  return (
    <svg
      viewBox="0 0 18 12"
      width="16"
      height="11"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M1.5 6.5l3 3 6.5-7" />
      {double && <path d="M7 9.5l1 0M8 9.5l6.5-7" />}
    </svg>
  );
}

/** El doble check de WhatsApp junto a la hora de un mensaje saliente. Sale de
 *  lo que WhatsApp informó por webhook; sin dato dice «sin confirmación» y no
 *  pinta ningún check. Fallido y «sin confirmación» se LEEN (en la app de
 *  escritorio táctil no hay hover); el tooltip da la hora y el error. */
export function DeliveryCheck({ delivery }: { delivery?: ChatDeliveryItem }) {
  if (!delivery) return null;
  const at = delivery.time ? ` ${delivery.time}` : "";
  if (delivery.status === "unconfirmed") {
    return <span title="WhatsApp todavía no confirmó este mensaje">sin confirmación</span>;
  }
  if (delivery.status === "failed") {
    const code = delivery.errorCode !== undefined ? ` · ${delivery.errorCode}` : "";
    const detail =
      (delivery.errorCode !== undefined ? ` · error ${delivery.errorCode}` : "") +
      (delivery.errorTitle ? ` (${delivery.errorTitle})` : "");
    return (
      <span
        className="rounded px-1 font-semibold bg-danger text-win-bg"
        title={`WhatsApp no lo entregó${at}${detail}`}
      >
        {`No llegó${code}`}
      </span>
    );
  }
  const label = `${STATUS_LABEL[delivery.status]} por WhatsApp${at}`;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-status={delivery.status}
      className={delivery.status === "read" ? "inline-flex text-cyan" : "inline-flex"}
    >
      <Ticks double={delivery.status !== "sent"} />
    </span>
  );
}
