/**
 * Plugin `orders` — barrel del frontend.
 *
 * Exporta `default` (OrdersSection) que el shell consume via lazy import
 * desde el registry. Plus named re-exports para tests y back-compat.
 */
export { default, OrdersSection } from "./OrdersSection";
export type { OrdersSectionProps } from "./OrdersSection";

export {
  OrdersFilters,
  filterLabel,
  useOrderFilters,
} from "@plugins/orders/frontend/features/orders-filters";
export {
  OrdersBoard,
  OrdersHeader,
} from "@plugins/orders/frontend/features/orders-board";
export { OrdersInspector } from "@plugins/orders/frontend/features/orders-inspector";
