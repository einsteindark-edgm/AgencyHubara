/**
 * API pública del feature. Sólo `SessionList` se exporta — todo lo demás
 * (SearchBar, TagFilterBar, SessionListItem, useSessionFilters) es interno.
 *
 * Ningún consumer fuera de este folder debe importar desde subpaths.
 */
export { SessionList } from "./ui/SessionList";
