/**
 * Iconos plugin-locales del módulo Ads. NO se promueven a `shared/ui/Icon` —
 * son específicos del dominio publicitario (Meta logo, click hand, trophy
 * de ventas) y no tienen uso fuera de este plugin. La regla §11 del manifiesto
 * FSD pide mover a `shared/ui/` solo si más de un consumidor los necesita.
 *
 * Mismo trato visual que `shared/ui/Icon`: stroke-only 1.6 / 16px y heredan
 * `currentColor`. Para que sean drop-in compatibles con la forma de uso
 * `{Ads.play}` que respeta el diseño original.
 */

import type { CSSProperties, ReactNode } from "react";

interface IcoProps {
  size?: number;
  viewBox?: string;
  fill?: boolean;
  strokeWidth?: number;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}

function Ico({
  size = 16,
  viewBox = "0 0 24 24",
  fill = false,
  strokeWidth = 1.6,
  className,
  style,
  children,
}: IcoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox={viewBox}
      fill={fill ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const AdsIcon = {
  ad: () => (
    <Ico>
      <path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1z" />
      <path d="M15 8.5a3.5 3.5 0 0 1 0 7" />
      <path d="M18 6a7 7 0 0 1 0 12" />
    </Ico>
  ),
  meta: () => (
    <Ico fill>
      <path d="M12 3c-2.4 0-4 1.6-5.5 4.2C5 9.7 3.5 13 2.5 13c-.6 0-1-.5-1-1.3 0-1.5 1-3.5 2-3.5.4 0 .7.2 1 .5l1-1.6C5 6.4 4.2 6 3.4 6 1.5 6 0 8.7 0 11.8 0 14 1.1 16 3 16c2 0 3.7-2.6 5-5 .8-1.5 1.4-2.5 2-3l.8 1.2c-1.1 1.5-2.5 4-3.4 5.6C6.5 16.4 7.7 18 9.8 18c2.6 0 4-3 5.7-6 .6-1 1-1.8 1.5-2.5l.8 1.2C16.7 12 15 15 15 16.5c0 .9.6 1.5 1.5 1.5 2.5 0 5.5-3.5 5.5-7.6C22 6.2 19.6 3 16.5 3c-1.5 0-2.7.8-3.7 2 .4-.7.7-1.4 1.2-2z" />
    </Ico>
  ),
  wa: () => (
    <Ico>
      <path d="M21 11.5a8.4 8.4 0 0 1-12.4 7.3L3 20l1.2-5.4A8.4 8.4 0 1 1 21 11.5z" />
    </Ico>
  ),
  play: () => (
    <Ico fill>
      <path d="M8 5v14l11-7z" />
    </Ico>
  ),
  pause: () => (
    <Ico fill>
      <rect x="6" y="5" width="4" height="14" rx="1" />
      <rect x="14" y="5" width="4" height="14" rx="1" />
    </Ico>
  ),
  trend: () => (
    <Ico>
      <polyline points="3 17 9 11 13 15 21 7" />
      <polyline points="15 7 21 7 21 13" />
    </Ico>
  ),
  users: () => (
    <Ico>
      <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="8.5" cy="7" r="4" />
      <path d="M22 11l-3 3-2-2" />
    </Ico>
  ),
  click: () => (
    <Ico>
      <path d="M9 9l5 12 1.7-5.4L21 14z" />
      <path d="M5 5v.01M3 9v.01M3 13v.01M5 17v.01M9 5v.01M13 3v.01M17 5v.01" />
    </Ico>
  ),
  eye: () => (
    <Ico>
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
      <circle cx="12" cy="12" r="3" />
    </Ico>
  ),
  msg: () => (
    <Ico>
      <path d="M21 11.5a8.4 8.4 0 0 1-12.4 7.3L3 20l1.2-5.4A8.4 8.4 0 1 1 21 11.5z" />
    </Ico>
  ),
  trophy: () => (
    <Ico>
      <path d="M8 4h8v4a4 4 0 1 1-8 0z" />
      <path d="M16 6h3a2 2 0 0 1 0 4h-3M8 6H5a2 2 0 0 0 0 4h3" />
      <path d="M9 14h6v3H9zM7 21h10" />
    </Ico>
  ),
  dollar: () => (
    <Ico>
      <circle cx="12" cy="12" r="10" />
      <path d="M15 9.5c-.6-1-1.8-1.5-3-1.5-2 0-3 1-3 2.2 0 3 6 1.6 6 4.6 0 1.4-1.4 2.2-3 2.2-1.5 0-2.7-.6-3.3-1.7M12 6v12" />
    </Ico>
  ),
  info: () => (
    <Ico>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v.01M11 12h1v4h1" />
    </Ico>
  ),
  cal: () => (
    <Ico>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </Ico>
  ),
  loc: () => (
    <Ico>
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
      <circle cx="12" cy="10" r="3" />
    </Ico>
  ),
  ext: () => (
    <Ico>
      <path d="M14 4h6v6" />
      <path d="M20 4 10 14" />
      <path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5" />
    </Ico>
  ),
};
