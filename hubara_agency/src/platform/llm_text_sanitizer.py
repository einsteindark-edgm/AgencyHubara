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
#
# DOS TIERS (bug run eda8d460 — cliente real vio "todas nuestras velas.
# Míralas..." porque "Aquí tienes " se strippeó de una frase legítima):
#
#   * STRONG — frases que casi nunca abren una oración legítima al cliente
#     ("Here's my attempt", "Final answer", "Output"). Delimitador laxo
#     (espacio/coma/da igual).
#   * WEAK — frases que SÍ pueden abrir una oración legítima ("Aquí tienes
#     todas nuestras velas", "Voy a confirmar tu pedido", "Okay, perfecto").
#     Solo son meta cuando las delimita ':' o terminan la línea
#     ("Aquí tienes:\n¡Hola!" / "Okay,\n\n¡Hola!").
#
# Además, `_strip_meta_prefix` aplica un guard universal: si el resto tras
# strippear arranca en minúscula, se cortó a mitad de frase → no strippear.
_META_PREFIX_STRONG_PATTERNS: tuple[str, ...] = (
    # Inglés
    r"^here'?s\s+my\s+(attempt|response|answer|reply|message|take)[\s:.,—!?-]+",
    r"^here'?s\s+(the|a|my)?\s*(message|response|reply|answer|attempt|take)[\s:.,—!?-]+",
    r"^my\s+(response|attempt|message|answer|reply)[\s:.,—!?-]+",
    r"^response[\s:.,—!?-]+",
    r"^(let\s+me|i['']?ll)\s+(try|craft|write|send)[\s:.,—!?-]+",
    r"^(short\s+and\s+)?warm\s+(message|gancho|attempt)[\s:.,—!?-]+",
    # Español (sustantivos meta — no abren oración de venta natural)
    r"^mi\s+(respuesta|mensaje|intento)[\s:.,—!?-]+",
    r"^te\s+respondo[\s:.,—!?-]+",
    r"^respuesta[\s:.,—!?-]+",
    r"^intento[\s:.,—!?-]+",
    # Variantes con marcadores estilo agentic ("Final answer:", "Output:")
    r"^final\s+(answer|response|message|output)[\s:.,—!?-]+",
    r"^output[\s:.,—!?-]+",
    r"^assistant[\s:.,—!?-]+",
)

# Delimitador WEAK: ':' (con espacios), o puntuación opcional seguida de
# fin de línea. "Aquí tienes:" / "Sure!\n" / "Okay,\n" matchean;
# "Aquí tienes todas..." / "Voy a confirmar..." NO.
_WEAK_DELIM = r"(?:\s*:\s*|\s*[.,—!?-]*\s*\n\s*)"

_META_PREFIX_WEAK_PATTERNS: tuple[str, ...] = (
    r"^here\s+(it|you)\s+(is|go)" + _WEAK_DELIM,
    r"^okay" + _WEAK_DELIM,
    r"^sure" + _WEAK_DELIM,
    r"^aqu[ií]\s+(va|est[áa]|tienes|te\s+dejo)" + _WEAK_DELIM,
    r"^ac[áa]\s+(va|est[áa]|tienes)" + _WEAK_DELIM,
    r"^voy\s+a" + _WEAK_DELIM,
)

_META_PREFIX_RE = re.compile(
    "|".join(_META_PREFIX_STRONG_PATTERNS + _META_PREFIX_WEAK_PATTERNS),
    flags=re.IGNORECASE,
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

    # 6.5 Em/en dash -> coma. Regla de estilo de marca ("cero em dash"): el
    # prompt NO basta (DeepSeek-Flash los emite igual, visto en 7/29 escenarios
    # del golden-eval). Acá lo enforzamos de forma determinista. NO tocamos el
    # guion normal (-, U+002D) que sí se usa (1 a 2 días, contra-entrega).
    cleaned, removed = _normalize_dashes(text)
    if removed:
        text = cleaned
        actions.append("em_dash_normalized")

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
    if stripped[0].islower():
        # Guard anti-overstrip (bug run eda8d460): el mensaje real tras un
        # meta-prefijo empieza con mayúscula, ¡/¿, comilla o emoji. Si el
        # resto arranca en minúscula, cortamos a mitad de una frase
        # legítima ("Mi respuesta es que sí...") — no strippear.
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


# Em dash (U+2014) y en dash (U+2013). El guion-menos normal (U+002D) NO se toca.
_DASH_RE = re.compile(r"\s*[—–]\s*")


def _normalize_dashes(text: str) -> tuple[str, bool]:
    """Reemplaza em/en dash por coma (puntuación natural de la marca).

    Idempotente. Limpia comas huérfanas que la sustitución pueda dejar (dobles,
    o al inicio del texto). Conserva el guion normal `-`."""
    if "—" not in text and "–" not in text:
        return text, False
    out = _DASH_RE.sub(", ", text)
    out = re.sub(r",\s*,", ", ", out)        # comas dobles -> una
    out = re.sub(r"^\s*,\s*", "", out)        # coma huérfana al inicio
    out = re.sub(r"\s+,", ",", out)           # espacio antes de coma
    return out, out != text


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


# =============================================================================
# Abstención explícita (NO_MESSAGE)
# =============================================================================

#: Sentinel que el prompt de remarketing instruye emitir cuando el toque
#: proactivo YA NO corresponde (incidente wa_573229041190, run 019f7234: el
#: LLM decidió "no genero un nuevo mensaje" pero el runtime no tenía canal de
#: abstención y la deliberación se envió al cliente).
NO_MESSAGE_SENTINEL: str = "NO_MESSAGE"

#: Variantes aceptadas — DeepSeek code-switchea a español.
_ABSTENTION_TOKENS: frozenset[str] = frozenset({"NO_MESSAGE", "NO_MENSAJE"})

#: Wrappers que el modelo suele agregar alrededor de un token pedido "solo":
#: comillas, backticks, asteriscos de bold y puntuación final.
_ABSTENTION_STRIP_CHARS = "\"'`*“”«»‟„ \t.,:;!¡?¿-—"


# Patrones de texto administrativo. Cada entrada nace de un leak REAL o de
# un envelope interno vivo en el código — NO de especulación. El costo de un
# falso positivo es un cliente sin respuesta, así que cada patrón debe ser
# vocabulario que un mensaje de venta esencialmente nunca usa. Al agregar
# uno nuevo: sumá el caso real a test_admin_leak_detector.py primero.
_ADMIN_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Marker de sistema tal cual ("[SISTEMA]: ...").
    re.compile(r"\[SISTEMA", re.IGNORECASE),
    # Reporte de etiquetado: "La conversación queda etiquetada como
    # `INTERESADO`" / "Interacción etiquetada como 'INTERESADO'". Exigimos
    # el sustantivo administrativo O un tag token en mayúsculas — "lo
    # dejamos etiquetado como regalo" (legítimo) NO matchea.
    re.compile(
        r"(conversaci[oó]n|interacci[oó]n)\s+(queda\s+)?etiquetad",
        re.IGNORECASE,
    ),
    re.compile(r"etiquetad[ao]s?\s+como\s+[`'\"“]?[A-ZÁÉÍÓÚ_]{4,}"),
    # Tag token interno: ALL_CAPS con underscore (COMPRA_EXITOSA,
    # CONFIRMADO_PAGO_PENDIENTE, NO_MESSAGE...).
    re.compile(r"\b[A-ZÁÉÍÓÚ]{2,}(?:_[A-ZÁÉÍÓÚ0-9]{2,})+\b"),
    # Token interno en backticks (`INTERESADO`, `RECHAZO`).
    re.compile(r"`[A-ZÁÉÍÓÚ_]{3,}`"),
    # Envelopes de tools regurgitados (routing L-12, escalación).
    re.compile(r"el\s+control\s+ha\s+sido\s+transferido", re.IGNORECASE),
    re.compile(r"escalaci[oó]n\s+registrada", re.IGNORECASE),
    # Deliberación de declinación (incidente wa_573125671604).
    re.compile(
        r"no\s+genero\s+(un[a]?\s+)?(nuevo\s+|nueva\s+)?(mensaje|respuesta)",
        re.IGNORECASE,
    ),
    # Reporte en tercera persona sobre el propio cliente, al inicio del
    # texto ("El cliente eligió su...", "La conversación queda...").
    re.compile(r"^(el\s+cliente|la\s+clienta|la\s+conversaci[oó]n)\b", re.IGNORECASE),
    # Tercera persona en CUALQUIER posición (run 1c9ef231: "Encontré 10
    # velas religiosas. Las muestro al cliente." — el ancla ^ no la veía).
    # El agente le habla al cliente de tú; "al/del cliente" solo aparece en
    # narración interna. Excepciones fijas: las colocaciones comerciales
    # "servicio al cliente" / "atención al cliente" sí son voz de venta.
    re.compile(
        r"(?<!servicio\s)(?<!atenci[oó]n\s)\b(al|del)\s+client[ea]\b",
        re.IGNORECASE,
    ),
    # Etiquetado en primera persona del pretérito ("Etiqueté como
    # INTERESADO") — run 1c9ef231, cierre por ghosting. El gift-tagging
    # legítimo usa otras formas ("etiquetado", "etiquetemos") que NO caen.
    re.compile(r"\betiquet[eé]\s+como\b", re.IGNORECASE),
    # Vocabulario de infraestructura que jamás va en un mensaje de venta.
    re.compile(r"\bremarketing\b", re.IGNORECASE),
    re.compile(r"\bghost", re.IGNORECASE),
    re.compile(r"\bhandoff\b", re.IGNORECASE),
    re.compile(r"\bworkflow\b", re.IGNORECASE),
)


def looks_like_admin_leak(raw: str | None) -> bool:
    """True si el texto huele a reporte administrativo interno, no a mensaje
    para el cliente (incidente 5f43bcd0: "La conversación queda etiquetada
    como `INTERESADO`..." enviado por WhatsApp).

    Última línea de defensa DETERMINISTA en el path de send — complementa la
    supresión estructural de turnos admin (que cubre los turnos de cierre)
    cubriendo los turnos NORMALES donde el LLM regurgita envelopes de tools
    o narra su proceso. Conservador: solo patrones que un mensaje legítimo
    de venta esencialmente nunca contiene.
    """
    if not raw:
        return False
    text = raw.strip()
    if not text:
        return False
    return any(p.search(text) for p in _ADMIN_LEAK_PATTERNS)


def is_no_message_abstention(raw: str | None) -> bool:
    """True si el output del LLM es una abstención explícita (`NO_MESSAGE`).

    Conservador a propósito: solo cuenta el sentinel al INICIO del texto
    (primera línea), tolerando wrappers cosméticos (comillas, backticks,
    puntuación) y la traducción obvia al español. Prosa de deliberación SIN
    el sentinel NO es abstención — el helper no adivina intención; esa
    responsabilidad es del prompt. Vacío/None tampoco: ya tiene su propio
    manejo (no-send por falsy) y acá solo reportamos abstención explícita.
    """
    if not raw:
        return False
    first_line = raw.strip().splitlines()[0]
    normalized = first_line.strip(_ABSTENTION_STRIP_CHARS).upper()
    return normalized in _ABSTENTION_TOKENS
