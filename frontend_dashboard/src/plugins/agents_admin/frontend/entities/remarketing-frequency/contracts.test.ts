import { describe, expect, it } from "vitest";

import fixture from "./fixtures/frequency.json";
import { frequencySchema } from "./contracts";

/**
 * El shape REAL de `GET /api/agents/remarketing/frequency` (cast al contrato
 * `remarketing-frequency@v1` de chats): el fixture sale de lo que devuelve
 * `chats/api/remarketing_frequency.py`.
 */
describe("frequencySchema", () => {
  it("parsea lo que devuelve el backend", () => {
    const parsed = frequencySchema.parse(fixture);

    expect(parsed.max_touches).toBe(5);
    expect(parsed.ceiling).toBe(5);
    expect(parsed.saved).toBeNull();
    expect(parsed.ladder).toHaveLength(5);
    expect(parsed.ladder[1]).toEqual({ touch: 2, after_ms: 14_400_000 });
  });

  it("conserva quién y cuándo hizo el último cambio", () => {
    const parsed = frequencySchema.parse({
      ...fixture,
      max_touches: 2,
      saved: 2,
      updated_at_ms: 1_790_200_000_000,
      updated_by: "dashboard:operator",
    });

    expect(parsed.saved).toBe(2);
    expect(parsed.updated_by).toBe("dashboard:operator");
  });

  it("tolera lo accesorio (escalera, autoría) pero NO inventa la cantidad", () => {
    const parsed = frequencySchema.parse({ max_touches: 3, ceiling: 5 });
    expect(parsed.ladder).toEqual([]);
    expect(parsed.updated_by).toBeNull();

    // Sin la cantidad no hay panel: mejor error que mostrar un 0 falso.
    expect(() => frequencySchema.parse({ ceiling: 5 })).toThrow();
    expect(() => frequencySchema.parse({ max_touches: "tres", ceiling: 5 })).toThrow();
  });
});
