"""Sanitiza la salida textual del LLM antes de enviarla al cliente.

Bug origen (sesión 579d34e7): el LLM (DeepSeek) emitió:

    Here's my attempt:

    "¡Hola de nuevo! 🌿 Quedó pendiente lo de la *Plegaria de Luz* — …"¡Hola de nuevo! 🌿 Quedó pendiente lo de la *Plegaria de Luz* — …

Dos patologías combinadas:

1. **Meta-prefijo en inglés** ("Here's my attempt:") — el modelo se trató
   a sí mismo como en un test de creative writing. El cliente ve el
   "thinking" del bot — destruye la ilusión de humano.
2. **Duplicación**: el mismo párrafo emitido dos veces back-to-back, como
   si el modelo "se decidiera" entre dos versiones y se quedara con ambas.

Este sanitizador es la **última línea de defensa** entre `response.content`
y `send_whatsapp_message_activity`. Es defensivo a propósito — no
queremos depender solo del prompt para que el LLM se comporte. Es
idempotente; correrlo dos veces da el mismo resultado.

Decisiones de diseño:

* **Conservador**: solo strip-eamos prefijos que matchean patrones
  CONOCIDOS de meta-thinking. NO removemos cualquier oración terminada en
  `:`. Si el modelo legít escribe "Mirá: aroma lavanda" eso queda intacto.
* **Idiomas**: matcheamos español + inglés (DeepSeek/Gemini code-switch a
  veces).
* **Logging**: cuando alguno de los pasos modifica el texto, retornamos
  qué pasos se activaron — el caller puede emitir warning/analytics.
* **Sin dependencias**: stdlib-only para que se pueda llamar también
  desde activities sin imports de Temporal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# =============================================================================
# Patterns
# =============================================================================

# Prefijos meta más comunes que vemos en outputs LLM. Match al inicio de
# todo el texto (posiblemente seguido por newlines y la respuesta real).
# Insensible a case y tildes.
_META_PREFIX_PATTERNS: tuple[str, ...] = (
    # Inglés
    r"^here'?s\s+my\s+(attempt|response|answer|reply|message|take)[\s:.,—!?-]+",
    r"^here'?s\s+(the|a|my)?\s*(message|response|reply|answer|attempt|take)[\s:.,—!?-]+",
    r"^here\s+(it|you)\s+(is|go)[\s:.,—!?-]+",
    r"^my\s+(response|attempt|message|answer|reply)[\s:.,—!?-]+",
    r"^response[\s:.,—!?-]+",
    r"^(let\s+me|i['']?ll)\s+(try|craft|write|send)[\s:.,—!?-]+",
    r"^okay[\s:.,—!?-]+",
    r"^sure[\s:.,—!?-]+",
    r"^(short\s+and\s+)?warm\s+(message|gancho|attempt)[\s:.,—!?-]+",
    # Español
    r"^aqu[ií]\s+(va|est[áa]|tienes|te\s+dejo)[\s:.,—!?-]+",
    r"^ac[áa]\s+(va|est[áa]|tienes)[\s:.,—!?-]+",
    r"^mi\s+(respuesta|mensaje|intento)[\s:.,—!?-]+",
    r"^te\s+respondo[\s:.,—!?-]+",
    r"^respuesta[\s:.,—!?-]+",
    r"^intento[\s:.,—!?-]+",
    r"^voy\s+a[\s:.,—!?-]+",
    # Variantes con marcadores estilo agentic ("Final answer:", "Output:")
    r"^final\s+(answer|response|message|output)[\s:.,—!?-]+",
    r"^output[\s:.,—!?-]+",
    r"^assistant[\s:.,—!?-]+",
)

_META_PREFIX_RE = re.compile(
    "|".join(_META_PREFIX_PATTERNS), flags=re.IGNORECASE
)

# Comillas envolventes — el modelo a veces wrappea la respuesta entera
# en comillas como si estuviera "citando" su propio mensaje.
# Cubrimos comillas rectas, "smart quotes" y guillemots.
_OPEN_QUOTES = ("\"", "“", "«", "‟", "„")
_CLOSE_QUOTES = ("\"", "”", "»", "‟", "‟")


# =============================================================================
# Public API
# =============================================================================


@dataclass(frozen=True)
class SanitizeResult:
    """Resultado del sanitizado. `text` es el output limpio.

    `actions` lista los pasos que realmente modificaron el contenido —
    útil para emitir warnings y métricas de "qué tan seguido el LLM
    falla".
    """

    text: str
    actions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.actions)


def sanitize_llm_text(raw: str) -> SanitizeResult:
    """Sanitiza el texto antes de enviarlo al cliente.

    Pipeline (en orden):
      1. Strip leading/trailing whitespace.
      2. Remove meta-prefijos conocidos ("Here's my attempt:", etc.).
      3. Strip comillas envolventes globales si abarcan todo el texto.
      4. Detectar duplicación back-to-back y colapsar a una sola copia.
      5. Strip whitespace de nuevo (los pasos anteriores pueden dejar
         espacios huérfanos).

    Idempotente: `sanitize(sanitize(x).text).text == sanitize(x).text`.
    """
    if not raw:
        return SanitizeResult("", ())

    text = raw
    actions: list[str] = []

    # 1. Trim.
    stripped = text.strip()
    if stripped != text:
        text = stripped
        actions.append("trim")

    # 2. Meta-prefijo.
    cleaned, removed = _strip_meta_prefix(text)
    if removed:
        text = cleaned
        actions.append("meta_prefix_stripped")

    # 3. Comillas envolventes globales.
    cleaned, removed = _strip_wrapping_quotes(text)
    if removed:
        text = cleaned
        actions.append("wrapping_quotes_stripped")

    # 4. Duplicación back-to-back.
    cleaned, removed = _dedupe_back_to_back(text)
    if removed:
        text = cleaned
        actions.append("back_to_back_dedup")

    # 5. Cleanup pos-dedupe: si quedó una comilla abierta sin cerrar
    # (caso típico del bug 579d34e7: `"X"X` → tras dedupe queda `"X`),
    # la stripeamos. Idem si quedó cerrada sin abrir.
    cleaned, removed = _strip_unbalanced_outer_quotes(text)
    if removed:
        text = cleaned
        actions.append("unbalanced_quote_stripped")

    # 6. Segundo pase de strip de comillas envolventes — algunos casos
    # solo se vuelven obvios tras dedupe.
    cleaned, removed = _strip_wrapping_quotes(text)
    if removed:
        text = cleaned
        actions.append("wrapping_quotes_stripped")

    # 7. Trim final.
    text = text.strip()

    return SanitizeResult(text, tuple(actions))


# =============================================================================
# Helpers
# =============================================================================


def _strip_meta_prefix(text: str) -> tuple[str, bool]:
    """Elimina prefijos meta conocidos. Si tras strip queda vacío,
    devuelve el original (defensivo — no destruyas todo el mensaje)."""
    match = _META_PREFIX_RE.match(text)
    if not match:
        return text, False
    stripped = text[match.end():].lstrip()
    if not stripped:
        # El "mensaje" entero era el meta-prefijo — preferimos no
        # devolver vacío (downstream tiene fallback humano para vacío).
        return text, False
    return stripped, True


def _strip_wrapping_quotes(text: str) -> tuple[str, bool]:
    """Si todo el texto está wrappeado en comillas (la primera y última
    matchean un par), las removemos. Conservador: solo si NO hay comillas
    intermedias que sugieren una cita legítima dentro."""
    if len(text) < 2:
        return text, False
    first = text[0]
    last = text[-1]
    if first not in _OPEN_QUOTES or last not in _CLOSE_QUOTES:
        return text, False
    # Si en el medio hay comillas, asumimos que es texto con cita interna
    # legítima — no tocamos.
    inner = text[1:-1]
    if any(q in inner for q in _OPEN_QUOTES + _CLOSE_QUOTES):
        return text, False
    return inner.strip(), True


def _strip_unbalanced_outer_quotes(text: str) -> tuple[str, bool]:
    """Si el texto empieza con una comilla pero no cierra con la pareja
    correcta (y no hay otras comillas en el medio), removemos la inicial.
    Idem viceversa. Conservador: solo cuando hay 1 sola comilla en el
    extremo y ninguna otra en el cuerpo."""
    if len(text) < 2:
        return text, False
    first = text[0]
    last = text[-1]
    # Opening quote pero no closing.
    if first in _OPEN_QUOTES and last not in _CLOSE_QUOTES:
        body = text[1:]
        # Solo strip si el cuerpo no tiene más comillas (que podrían ser
        # citas legítimas internas).
        if not any(q in body for q in _OPEN_QUOTES + _CLOSE_QUOTES):
            return body.lstrip(), True
    # Closing quote pero no opening.
    if last in _CLOSE_QUOTES and first not in _OPEN_QUOTES:
        body = text[:-1]
        if not any(q in body for q in _OPEN_QUOTES + _CLOSE_QUOTES):
            return body.rstrip(), True
    return text, False


def _dedupe_back_to_back(text: str) -> tuple[str, bool]:
    """Detecta cuando el mismo bloque de texto aparece dos veces consecutivas.

    Caso del bug 579d34e7:

        "¡Hola de nuevo!...🤍"¡Hola de nuevo!...🤍

    El modelo emitió la oración entre comillas y luego sin comillas. Tras
    quitar las comillas envolventes (paso anterior), queda el mismo texto
    repetido. Detectamos vía split midpoint.

    Estrategia:
      * Si la longitud del texto es par y la mitad superior == la mitad
        inferior, devolvemos la mitad.
      * Idem si la primera mitad termina exactamente donde la segunda
        empieza, considerando tolerancia a 1-2 whitespace.
    """
    n = len(text)
    if n < 20:  # mensajes cortos rara vez se duplican
        return text, False

    # Estrategia 1: split exacto en el medio.
    mid = n // 2
    first_half = text[:mid].rstrip()
    second_half = text[mid:].lstrip()
    if first_half and first_half == second_half:
        return first_half, True

    # Estrategia 2: buscar el sufijo más largo que sea prefijo del resto.
    # Si la primera mitad aparece exactamente al inicio de la segunda,
    # asumimos duplicación.
    for split in range(n // 2 - 5, n // 2 + 5):
        if split < 5 or split > n - 5:
            continue
        first = text[:split].strip()
        rest = text[split:].strip()
        if first and (rest == first or rest.startswith(first)):
            return first, True

    return text, False
