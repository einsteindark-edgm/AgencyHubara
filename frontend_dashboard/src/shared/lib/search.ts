/**
 * Coincidencia de texto de los buscadores de las sidebars (Chats, Órdenes,
 * Ads): una sola regla para los tres, así "maria" encuentra a María en todos
 * lados.
 *
 * - Sin mayúsculas ni tildes: "bogota" encuentra "Bogotá" y al revés.
 * - Varias palabras: TODAS tienen que aparecer, cada una en cualquier campo
 *   ("maria #31" = la clienta María con el pedido #31).
 * - Un número escrito como teléfono ("+57 300-123 4567") se compara por sus
 *   dígitos contra los campos que también SON un número. Nunca contra texto
 *   libre: juntar los dígitos de "2 velas de 31 cm" inventaría un "231".
 * - El "#" no es puntuación de teléfono: "#31" busca el pedido 31, no
 *   cualquier teléfono con un 31 adentro.
 */

/** Solo dígitos y lo que se usa para escribir un teléfono. */
const NUMBER_LIKE = /^[\d\s+().-]*\d[\d\s+().-]*$/;

function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

/** Los dígitos de un texto que ES un número (teléfono, id); null si no lo es. */
function digitsOf(text: string): string | null {
  return NUMBER_LIKE.test(text) ? text.replace(/\D/g, "") : null;
}

export function matchesSearch(
  query: string,
  fields: ReadonlyArray<string | null | undefined>,
): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;

  const texts = fields.filter((f): f is string => !!f).map(fold);
  const numbers = texts.map(digitsOf);

  return words.every((word) => {
    const digits = digitsOf(word);
    return texts.some((text, i) => {
      if (text.includes(word)) return true;
      const number = numbers[i];
      return digits !== null && number !== null && number.includes(digits);
    });
  });
}
