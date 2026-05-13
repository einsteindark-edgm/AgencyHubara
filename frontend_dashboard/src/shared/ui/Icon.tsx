/**
 * Iconos SF/Tahoe-style del prototipo Agency Desktop: SVGs stroke-only (1.6 width)
 * que heredan `currentColor`. Centralizados acá para que cada feature consuma el
 * mismo glyph y la sustitución sea homogénea.
 *
 * No usamos `lucide-react` para los chrome icons porque la versión instalada
 * (1.8.0) precede a varios de los nombres que necesitamos — además, el diseño
 * pide rebajas visuales muy específicas (stroke 1.6) que valen un set propio.
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

export const Icon = {
  sidebarL: () => (
    <Ico>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </Ico>
  ),
  sidebarR: () => (
    <Ico>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M15 4v16" />
    </Ico>
  ),
  back: () => <Ico><path d="M15 6l-6 6 6 6" /></Ico>,
  fwd: () => <Ico><path d="M9 6l6 6-6 6" /></Ico>,
  search: () => (
    <Ico>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-3.5-3.5" />
    </Ico>
  ),
  more: () => (
    <Ico fill>
      <circle cx="5" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="19" cy="12" r="1.4" />
    </Ico>
  ),
  plus: () => <Ico><path d="M12 5v14M5 12h14" /></Ico>,
  filter: () => <Ico><path d="M4 5h16l-6 7v6l-4 1v-7L4 5z" /></Ico>,
  refresh: () => (
    <Ico>
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5" />
    </Ico>
  ),
  flag: () => <Ico><path d="M5 21V4l9 3-2 4 2 4-9-3" /></Ico>,
  archive: () => (
    <Ico>
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4" />
    </Ico>
  ),
  bell: () => (
    <Ico>
      <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15zM10 19a2 2 0 0 0 4 0" />
    </Ico>
  ),
  bolt: () => <Ico><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" /></Ico>,
  wand: () => <Ico><path d="m3 21 12-12M14 4l2 2M18 8l2-2M16 2l1.5 1.5M2 18l1.5 1.5" /></Ico>,

  attach: () => (
    <Ico>
      <path d="M19 11l-7.5 7.5a4 4 0 0 1-5.7-5.7L13 5.5a3 3 0 0 1 4.3 4.3L10 17a2 2 0 0 1-3-3l6.5-6.5" />
    </Ico>
  ),
  emoji: () => (
    <Ico>
      <circle cx="12" cy="12" r="9" />
      <path d="M9 10v.01M15 10v.01M9 14c1 1.5 5 1.5 6 0" />
    </Ico>
  ),
  mic: () => <Ico><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3" /></Ico>,
  send: () => <Ico><path d="m4 12 16-8-6 18-3-7-7-3z" /></Ico>,
  template: () => (
    <Ico>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M3 9h18M9 21V9" />
    </Ico>
  ),

  chat: () => (
    <Ico>
      <path d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />
    </Ico>
  ),
  notes: () => <Ico><path d="M6 3h9l5 5v13H6zM15 3v5h5" /></Ico>,
  files: () => <Ico><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></Ico>,
  workflow: () => <Ico><path d="M5 5h6v6H5zM13 13h6v6h-6zM11 8h2a2 2 0 0 1 2 2v3" /></Ico>,
  timeline: () => <Ico><path d="M5 12h14M5 6h14M5 18h14M3 6v.01M3 12v.01M3 18v.01" /></Ico>,
  expand: () => <Ico><path d="M4 14v6h6M20 10V4h-6M4 20l7-7M20 4l-7 7" /></Ico>,

  caret: () => <Ico viewBox="0 0 12 12" size={9}><path d="m3 4 3 4 3-4" /></Ico>,
  chevR: () => <Ico viewBox="0 0 12 12" size={10}><path d="m4 3 4 3-4 3" /></Ico>,

  edit: () => <Ico><path d="M4 20h4l11-11-4-4L4 16zM14 6l4 4" /></Ico>,
  copy: () => (
    <Ico>
      <rect x="8" y="8" width="13" height="13" rx="2" />
      <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
    </Ico>
  ),
  download: () => <Ico><path d="M12 4v12M7 11l5 5 5-5M5 20h14" /></Ico>,
  trash: () => <Ico><path d="M4 6h16M9 6V4h6v2M6 6l1 14h10l1-14M10 11v5M14 11v5" /></Ico>,

  user: () => (
    <Ico>
      <circle cx="12" cy="9" r="4" />
      <path d="M5 21a7 7 0 0 1 14 0" />
    </Ico>
  ),
  tag: () => <Ico><path d="M3 12V4h8l9 9-8 8-9-9zM7 8v.01" /></Ico>,
  shield: () => <Ico><path d="M12 2 4 5v6c0 5 4 9 8 11 4-2 8-6 8-11V5z" /></Ico>,
  clipboard: () => (
    <Ico>
      <rect x="6" y="4" width="12" height="17" rx="2" />
      <path d="M9 4V3h6v1M9 9h6M9 13h6M9 17h4" />
    </Ico>
  ),
  smile: () => (
    <Ico>
      <circle cx="12" cy="12" r="9" />
      <path d="M9 10v.01M15 10v.01M9 14c1 1.5 5 1.5 6 0" />
    </Ico>
  ),
  info: () => (
    <Ico strokeWidth={1.8}>
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </Ico>
  ),
  check: () => <Ico><path d="M20 6L9 17l-5-5" /></Ico>,
  x: () => <Ico><path d="M18 6L6 18M6 6l12 12" /></Ico>,
  alert: () => (
    <Ico>
      <path d="M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <path d="M12 9v4M12 17h.01" />
    </Ico>
  ),
  truck: () => (
    <Ico>
      <rect x="1" y="3" width="15" height="13" />
      <path d="M16 8h4l3 3v5h-7" />
      <circle cx="5.5" cy="18.5" r="2.5" />
      <circle cx="18.5" cy="18.5" r="2.5" />
    </Ico>
  ),
  box: () => (
    <Ico>
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <path d="M3.27 6.96 12 12.01l8.73-5.05M12 22.08V12" />
    </Ico>
  ),
  clock: () => (
    <Ico>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </Ico>
  ),
  cal: () => (
    <Ico>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </Ico>
  ),
  pkg: () => (
    <Ico>
      <path d="M16.5 9.4 7.55 4.24" />
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
    </Ico>
  ),
  ready: () => (
    <Ico>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </Ico>
  ),
  bot: () => (
    <Ico>
      <rect x="4" y="7" width="16" height="12" rx="2" />
      <path d="M12 3v4M9 13h.01M15 13h.01M9 17h6" />
    </Ico>
  ),
  loc: () => (
    <Ico>
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
      <circle cx="12" cy="10" r="3" />
    </Ico>
  ),
  msg: () => (
    <Ico>
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </Ico>
  ),
  phone: () => (
    <Ico>
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13 1 .37 1.96.72 2.88a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.2-1.29a2 2 0 0 1 2.11-.45c.92.35 1.88.59 2.88.72A2 2 0 0 1 22 16.92z" />
    </Ico>
  ),
  doc: () => (
    <Ico>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
    </Ico>
  ),
  pay: () => (
    <Ico>
      <rect x="1" y="4" width="22" height="16" rx="2" />
      <path d="M1 10h22" />
    </Ico>
  ),
  xls: () => (
    <Ico>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6M8 13l4 5M12 13l-4 5" />
    </Ico>
  ),
  folder: () => (
    <Ico>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </Ico>
  ),
  upload: () => (
    <Ico>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
    </Ico>
  ),
  spark: () => (
    <Ico>
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    </Ico>
  ),
  arrow: () => <Ico><path d="M5 12h14M12 5l7 7-7 7" /></Ico>,
  img: () => (
    <Ico>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <path d="M21 15l-5-5L5 21" />
    </Ico>
  ),
};

export type IconName = keyof typeof Icon;
