"""Veracidad de producto frente al catálogo (puro, sin I/O).

Compartido por los dos agentes que le hablan al cliente (acá y no en uno de
ellos: Ventas y remarketing son independientes por contrato de import-linter):
Ventas nombra en la nota del turno lo que el cliente pidió y no existe
(`sales/use_cases/catalog_gap.py`); el remarketing lo usa en el trigger y en
la guarda de salida del gancho.

Incidente 2026-09-23/25: el cliente mandó la foto de una vela de dragón y
preguntó «¿y en vaso también?» — nada de eso existe en el catálogo y Ventas
nunca lo aclaró. El agente de remarketing (sin catálogo) lo convirtió en hechos:
«las de vaso son las más pedidas», «la del dragón es de las más lindas», «el
Cubo Love también viene en vaso». El A/B con el modelo de prod mostró que la
regla en el prompt sola no alcanza (el LLM copia sus ganchos anteriores), así
que la veracidad se cierra en dos lados deterministas:

  * `unavailable_terms`: lo que el cliente pidió o mostró y NO aparece en el
    catálogo — el trigger se lo nombra al LLM como inexistente.
  * `invented_product_claim`: la guarda de salida — el gancho que lo menciona
    igual, o que inventa popularidad sin datos de ventas, no se envía.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from rapidfuzz.distance import OSA

from src.sdk.catalogkit import normalize_label, stem_word

#: «en vaso», «las de vaso», «con diseño de dragón», «tipo difusor»: lo que el
#: cliente pide va pegado a una preposición. Solo la palabra INMEDIATA (sin
#: saltar artículos): «en el catálogo» no es un pedido de producto.
_ASKED_AFTER = re.compile(r"\b(?:en|de|con|tipo)\s+([^\W\d_]{4,})", re.UNICODE)

#: anotaciones del sistema en el transcript que NO son pedidos del cliente
#: («[el cliente vino desde un anuncio …]»). La de la foto sí lo es: describe
#: lo que el cliente mostró.
_ANNOTATION = re.compile(r"\[([^\]]*)\]")
_PHOTO_ANNOTATION = "el cliente envio una foto"
#: La foto que ya se reconoció como un producto NUESTRO (por lo que se lee en
#: ella o por la imagen; `sales/use_cases/photo_product.photo_reentry_text`):
#: lo que la visión describió con otras palabras («rostro de Jesús» = Sacrificio
#: de Amor) existe, así que esa foto no propone nada como inexistente.
OUR_PRODUCT_PHOTO_MARKS = ("(es nuestro producto «", "(es el mismo diseño de nuestro producto «")

#: vocabulario de la tienda/charla que no es un producto aunque no esté en las
#: descripciones del catálogo (evita bloquear ganchos legítimos).
_SHOP_WORDS = frozenset({
    "catalogo", "precio", "precios", "envio", "envios", "pedido", "pedidos",
    "pago", "pagos", "regalo", "regalos", "casa", "sala", "cuarto", "habitacion",
    "aroma", "aromas", "color", "colores", "vela", "velas", "foto", "fotos",
    "tienda", "anuncio", "mensaje", "whatsapp", "transferencia", "efectivo",
    "entrega", "domicilio", "ciudad", "bogota", "verdad", "nuevo", "nueva",
    "todas", "todos", "esas", "esos", "estas", "estos", "otra", "otro", "otras",
    "otros", "cual", "cuales", "alguna", "alguno", "ella", "ellas", "ellos",
    "nada", "mucho", "mucha", "algo", "forma", "diseno", "texto", "tamano",
    # frases de la charla de Ventas («en cuanto pueda», «con nequi»): la nota
    # de Ventas no puede acusar de inexistente lo que no es un producto.
    "cuanto", "acuerdo", "serio", "pronto", "momento", "gusto", "total",
    "tarjeta", "nequi", "daviplata", "bancolombia", "cuenta", "dinero", "plata",
    "contraentrega", "tarde", "manana", "noche", "semana", "cumpleanos",
})

#: popularidad/valoración sin datos de ventas («las más pedidas», «de las más
#: lindas», «son los favoritos»). «¿cuál es tu favorito?» es pregunta, no claim.
_POPULARITY = (
    re.compile(r"\bmas (?:pedid|vendid|popular|buscad|solicitad)\w*"),
    re.compile(r"\bde l[oa]s mas (?:lind|bonit|hermos)\w*"),
    re.compile(r"\b(?:son|es) (?:l[oa]s?|el) (?:favorit|preferid)\w*"),
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _singular(word: str) -> str:
    if word.endswith("es") and len(word) > 5:
        return word[:-2]
    if word.endswith("s") and len(word) > 4:
        return word[:-1]
    return word


def _customer_asks(text: str) -> str:
    def keep_photo(match: re.Match[str]) -> str:
        inner = match.group(1)
        if any(mark in inner for mark in OUR_PRODUCT_PHOTO_MARKS):
            return " "
        return inner if _fold(inner).startswith(_PHOTO_ANNOTATION) else " "

    return _ANNOTATION.sub(keep_photo, text)


def _catalog_haystack(products: list[Any]) -> set[str]:
    words: set[str] = set()
    for product in products:
        parts = [product.title or "", product.description or "", *(product.tags or [])]
        for axis, values in (getattr(product, "options", None) or {}).items():
            parts.extend([axis, *(values or [])])
        for word in re.findall(r"[^\W\d_]+", _fold(" ".join(parts)), re.UNICODE):
            words.add(_singular(word))
    return words


def unavailable_terms(customer_text: str, products: list[Any]) -> list[str]:
    """Lo que el cliente pidió o mostró y NO existe en el catálogo.

    `customer_text` = lo que escribió el cliente en el episodio (+ el motivo
    que anotó Ventas). Sin catálogo → [] (no se puede saber qué no existe).
    Devuelve la palabra como la escribió el cliente (minúsculas), sin repetir.
    """
    if not products:
        return []
    haystack = _catalog_haystack(products)
    out: list[str] = []
    seen: set[str] = set()
    for match in _ASKED_AFTER.finditer(_customer_asks(customer_text)):
        word = match.group(1).lower()
        key = _singular(_fold(word))
        if key in seen or key in haystack or _fold(word) in _SHOP_WORDS or key in _SHOP_WORDS:
            continue
        seen.add(key)
        out.append(word)
    return out


#: Un número con separadores de miles/decimales («$16.940», «2,5»).
_NUMBER = re.compile(r"\d(?:[\d.,]*\d)?")


def numbers_in(text: str) -> list[str]:
    """Los números de `text`, sin separadores (`$16.940` → `16940`), sin
    repetir. Son las fuentes contra las que se mira un mensaje."""
    out: list[str] = []
    for raw in _NUMBER.findall(text or ""):
        number = re.sub(r"[.,]", "", raw)
        if number not in out:
            out.append(number)
    return out


def invented_numbers(text: str, sources: list[str]) -> list[str]:
    """Los números del mensaje que no están en ninguna fuente (ficha, charla,
    datos de la tienda), ordenados. Análisis 2026-10-13: el toque que explica
    el producto es justo el que puede inventar «dura 60 horas» o «mide 12 cm»
    (como inventó «viene en vaso», 2026-09-25)."""
    allowed = set(sources)
    return sorted(n for n in numbers_in(text) if n not in allowed)


def invented_product_claim(text: str, terms: list[str]) -> str | None:
    """El dato inventado que trae el gancho, o None si es veraz.

    Palabra completa, sin tildes y tolerante a plural: «vasos» ≡ «vaso»,
    «dragon» ≡ «dragón»; «envaso» no es «vaso».
    """
    folded = _fold(text)
    words = {_singular(w) for w in re.findall(r"[^\W\d_]+", folded, re.UNICODE)}
    for term in terms:
        if _singular(_fold(term)) in words:
            return term
    for pattern in _POPULARITY:
        match = pattern.search(folded)
        if match:
            return match.group(0)
    return None


# ── Aromas de otro producto (incidente 2026-10-13) ─────────────────────────
#
# El bot escribió «las de deseos cuadradas vienen en Chanel, Drakar y Ylang
# Ylang»: Drakar e Ylang Ylang eran de la Plegaria de Luz (misma búsqueda) y
# el producto tenía un solo aroma. Lo de abajo encuentra los aromas del
# catálogo que un texto nombra, tolerando cómo los escribe un modelo
# («Drakkar», «Ylang Ylang» contra el «Ylan ylang» del catálogo, «moradas» por
# «Morado»); qué hacer con ellos lo deciden las capacidades `aroma_ajeno` y
# `color_ajeno` del paquete de decisión. Las distancias son de RapidFuzz; la
# normalización y el género/número, los del catálogo (`catalogkit`).

#: Cuántos errores de escritura admite un nombre según sus letras (ya sin
#: género, número ni letras dobles): corto, ninguno («lila» no es «lela»);
#: mediano, uno («ylanylang» = «ylangylang»); largo, dos.
_EDITS_BY_LENGTH = ((12, 2), (6, 1), (0, 0))
#: Lo que corta una cláusula: una negación vale dentro de la suya («no vienen
#: en Chanel ni en Drakar»), no en la siguiente («No te preocupes, te las
#: dejo en Drakar»).
#: (El punto y la coma de un número —«$45.000», «1,5 %»— no cortan.)
_CLAUSE = re.compile(r"(?:(?<!\d)[.,]|[.,](?!\d)|[;:!?¿¡()\n])+")
#: Lo mismo sin los paréntesis: lo que va entre paréntesis es de su cláusula
#: («Contra entrega (compras desde $45.000)» enuncia la regla general).
_CLAUSE_WITH_PARENS = re.compile(r"(?:(?<!\d)[.,]|[.,](?!\d)|[;:!?¿¡\n])+")
#: Antes del aroma en su cláusula, lo niega; justo después, «no» también
#: («Lavanda no la manejamos»).
_DENIES_BEFORE = frozenset({"no", "ni", "tampoco"})
_DENIES_AFTER = frozenset({"no"})


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", normalize_label(text or ""))


def _squash(words: list[str]) -> str:
    """Las palabras sin género ni número, juntas y sin letras dobles:
    «Drakkar» = «Drakar», «Rojas» = «rojo», «Ylang Ylang» = «ylangylang»."""
    return re.sub(r"(.)\1+", r"\1", "".join(stem_word(w) for w in words))


def _same_name(said: str, label: str, *, exact: bool = False) -> bool:
    """¿Lo escrito es el nombre del catálogo, con los errores que admite la
    MÁS CORTA de las dos palabras? (premortem del PR #426: «canela», sin
    género «canel», no es un «Chanel» mal escrito)."""
    if exact:
        return said == label
    shorter = min(len(said), len(label))
    allowed = next(edits for length, edits in _EDITS_BY_LENGTH if shorter >= length)
    return OSA.distance(said, label, score_cutoff=allowed) <= allowed


def _spans(tokens: list[str], labels: tuple[str, ...] | list[str]) -> list[tuple[int, int, str]]:
    """`(inicio, fin, etiqueta)` de cada etiqueta que nombran las palabras,
    en orden (cada una una vez; la de más palabras gana el lugar). Entre las de
    las mismas palabras, primero los nombres tal cual y después los parecidos:
    «canela» es Canela aunque Chanel esté antes en el catálogo."""
    taken: set[int] = set()
    hits: list[tuple[int, int, str]] = []
    ordered = sorted(labels, key=lambda lb: (len(_words(lb)), len(lb)), reverse=True)
    found: set[str] = set()
    passes = [(size, exact) for size in sorted({len(_words(lb)) for lb in ordered}, reverse=True) for exact in (True, False)]
    for size_now, exact in passes:
        for label in ordered:
            words = _words(label)
            if not words or label in found or len(words) != size_now:
                continue
            key, size = _squash(words), len(words)
            for start in range(len(tokens) - size + 1):
                span = range(start, start + size)
                if taken.intersection(span) or not _same_name(_squash(tokens[start:start + size]), key, exact=exact):
                    continue
                taken.update(span)
                hits.append((start, start + size, label))
                found.add(label)
                break
    return sorted(hits)


def mentioned_labels(text: str | None, labels: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Las etiquetas del catálogo que el texto nombra, en el orden en que
    aparecen (cada una una vez). Sin tildes ni mayúsculas, con letras dobles
    de más o de menos y, desde 6 letras, con una letra distinta. El nombre más
    largo gana: «Noche de Navidad» no cuenta también como «Navidad»."""
    return tuple(label for _start, _end, label in _spans(_words(text or ""), labels))


def _offered_labels(text: str | None, labels: list[str]) -> list[str]:
    """Las etiquetas que el texto nombra sin negarlas en su cláusula."""
    out: list[str] = []
    for clause in _CLAUSE.split(text or ""):
        tokens = _words(clause)
        for start, end, label in _spans(tokens, labels):
            denied = _DENIES_BEFORE.intersection(tokens[:start]) or _DENIES_AFTER.intersection(tokens[end:end + 2])
            if not denied and label not in out:
                out.append(label)
    return out


def foreign_labels(
    text: str | None, *, own: tuple[str, ...] | list[str], labels: tuple[str, ...] | list[str],
    others: tuple[str, ...] | list[str] = (),
) -> tuple[str, ...]:
    """Las etiquetas del catálogo de una dimensión (`labels`: los aromas, o
    los colores) que el texto nombra y no son del producto del que habla
    (`own`). Sin las del producto no se juzga (es un dato que falta, no una
    ajena). Un nombre que el catálogo usa también en la otra dimensión
    (`others`: «Café» es aroma y color) queda fuera: «trae velas café» no
    dice cuál de las dos. Lo que su cláusula niega tampoco («no vienen en
    Chanel ni en Drakar»): la regla decide sola mientras la decisión no tiene a Jev."""
    if not own:
        return ()
    others_keys = {_squash(_words(o)) for o in others}
    # Las del producto también se buscan: la más larga gana el lugar («Rojo y
    # verde», presentación de las velas de deseos, no es también «verde»).
    candidates = [label for label in dict.fromkeys([*labels, *own]) if _squash(_words(label)) not in others_keys]
    own_keys = [_squash(_words(o)) for o in own]
    return tuple(
        label for label in _offered_labels(text, candidates)
        if not any(_same_name(_squash(_words(label)), key) for key in own_keys)
    )


#: La cabeza de una negación de TENER algo (no cualquier «no»: «¿No te
#: gustaría…?» no niega nada): «no», «ya no», «tampoco» o «ni» y quizá un
#: pronombre («no la manejamos»). Los verbos que siguen son de la tienda y
#: vienen del paquete (`fact_rules.DenialWords`).
DENIAL_HEAD = r"\b(?:no|ya no|tampoco|ni)\s+(?:(?:lo|la|los|las|le|les|nos|te)\s+)?"


#: Lo que corta una oración (no el punto de un número: «$45.000»).
_SENTENCE = re.compile(r"(?:(?<!\d)\.|\.(?!\d)|[!?¡¿…\n])+")


def sentences(text: str | None) -> list[str]:
    """Las oraciones del texto, tal cual (sin tocar tildes ni mayúsculas)."""
    return [s.strip() for s in _SENTENCE.split(text or "") if s.strip()]


def without_names(text: str | None, names: tuple[str, ...] | list[str], *, placeholder: str = "cliente") -> str:
    """El texto con las palabras de `names` (el nombre del cliente) cambiadas
    por `placeholder`: «¡Hola Rosa!» no nombra el color Rosa."""
    keys = {normalize_label(n) for n in names if n and n.strip()}
    if not keys:
        return text or ""
    return re.sub(r"[^\W_]+", lambda m: placeholder if normalize_label(m.group(0)) in keys else m.group(0), text or "")


def clauses(text: str | None, *, keep_parens: bool = False) -> list[str]:
    """Las cláusulas del texto, sin tildes ni mayúsculas (`_CLAUSE`; con
    `keep_parens`, lo que va entre paréntesis queda en su cláusula)."""
    splitter = _CLAUSE_WITH_PARENS if keep_parens else _CLAUSE
    return [normalize_label(c) for c in splitter.split(text or "") if c.strip()]


def affirms(text: str | None, pattern: str, *, unless: str | None = None) -> bool:
    """¿El texto dice `pattern` (regex sobre el texto sin tildes ni
    mayúsculas) en una cláusula que no lo niega antes ni justo después?
    «puedes pagar contra entrega» sí; «este pedido no aplica para contra
    entrega» y «contra entrega no aplica» no. `unless`: lo que, dicho en la
    cláusula ANTES o DENTRO de lo que coincide, lo vuelve otra cosa («apenas
    recibamos tu pago»; no «tu pago quedó confirmado y te avisamos cuando…»)."""
    regex = re.compile(pattern)
    not_now = re.compile(unless) if unless else None
    for clause in clauses(text):
        for match in regex.finditer(clause):
            if not_now is not None and not_now.search(clause[: match.end()]):
                continue
            before, after = _words(clause[: match.start()]), _words(clause[match.end():])[:3]
            if not _DENIES_BEFORE.intersection(before) and not _DENIES_AFTER.intersection(after):
                return True
    return False


def denied_labels(
    text: str | None, labels: tuple[str, ...] | list[str], *, verbs: str, ignore: str | None = None
) -> tuple[str, ...]:
    """Las etiquetas (títulos, aromas, colores) que el texto niega tener: las
    que nombra en una cláusula con un verbo que niega tenerlas («no tenemos
    la Plegaria de Luz», «Lavanda no la manejamos»), en orden. `verbs`: el
    regex de los verbos que niegan detrás de `DENIAL_HEAD` (para un producto,
    solo los de tenerlo: «la Plegaria no viene en Chanel» niega el aroma, no
    el producto). `ignore`: el de lo que parece negación y no lo es («no hay
    problema»), que se quita de la cláusula antes de buscar."""
    denials = re.compile(f"{DENIAL_HEAD}(?:{verbs})")
    skip = re.compile(ignore) if ignore else None
    out: list[str] = []
    for clause in clauses(text):
        if skip is not None:
            clause = skip.sub(" ", clause)
        if not denials.search(clause):
            continue
        for _start, _end, label in _spans(_words(clause), labels):
            if label not in out:
                out.append(label)
    return tuple(out)
