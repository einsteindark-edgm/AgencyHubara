/**
 * Barrel del kit UI compartido. Sólo se exportan primitivas con uso cross-feature.
 * Si algo termina siendo usado por una sola feature, vive dentro de esa feature
 * (regla §11 del manifiesto FSD).
 */

export { Icon, type IconName } from "./Icon";
export { MacButton } from "./Button";
export { Panel, InsBlock } from "./Panel";
export { Avatar } from "./Avatar";
export { MissingData } from "./MissingData";
export { TitleBar, Toolbar, StatusBar, type SectionKey } from "./chrome";
