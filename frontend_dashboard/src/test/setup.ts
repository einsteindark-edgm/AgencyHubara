/**
 * Vitest global setup. Activa los matchers de `@testing-library/jest-dom`
 * (toBeInTheDocument, toHaveClass, ...) y limpia el DOM entre tests.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
