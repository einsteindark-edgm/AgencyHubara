"""Categorías del catálogo como closed-list + resolver determinístico.

El cliente casi nunca escribe el nombre exacto de una categoría ("religiosas",
"difusor", "velas religosas"). El filtro NO puede depender de que el LLM
adivine el término: acá vive el choke point determinista que traduce el texto
del cliente a UNA categoría real del snapshot — o admite que no la reconoce y
devuelve la lista cerrada de las que existen (mismo patrón que el variant
picker con aromas/colores).

Solo stdlib (R-DIP de `platform/catalog`): funciones puras, testeables sin IO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from src.platform.catalog.dtos import CatalogProductDTO
from src.platform.catalog.variant_attrs import normalize_label

# Ratio mínimo de similitud para aceptar un match difuso. 0.80 acepta
# "religosas"→"religiosas" y "aromaticaz"→"aromaticas" y rechaza
# "zapatos"→cualquiera. Subirlo deja typos afuera; bajarlo inventa categorías.
_FUZZY_THRESHOLD = 0.80
# Distancia mínima entre el 1º y el 2º para NO considerarlo ambiguo.
_AMBIGUITY_MARGIN = 0.05
# Ratio mínimo para considerar que un token de la query habla DE las
# categorías. Los que no llegan ("porfa", "quiero") son ruido y se descartan
# antes de puntuar — si no, hunden el promedio y matan un match legítimo.
_RELEVANCE_FLOOR = 0.60
# Muletillas del español que nunca son parte del nombre de una categoría.
_STOPWORDS = frozenset(
    """a al algo con de del el ella en eso esos esta estas este estos hay la
    las lo los me mas más muestra muestras muestrame nos o para pero por
    porfa porfavor que qué quiero se si su sus te tenes tenés tiene tienen
    todo todos un una unas uno unos ver y""".split()
)


@dataclass(frozen=True)
class CatalogCategoryDTO:
    slug: str
    label: str
    product_count: int = 0


@dataclass(frozen=True)
class CategoryResolution:
    """Resultado del resolver. `matched` None + `candidates` no vacío = el
    cliente fue ambiguo y hay que repreguntar con esas opciones."""

    query: str
    matched: CatalogCategoryDTO | None = None
    confidence: str = "none"  # exact | partial | fuzzy | ambiguous | none
    candidates: list[CatalogCategoryDTO] = field(default_factory=list)


def _canonical(raw: str) -> str:
    """Sin acentos, sin case, guiones/underscores → espacio."""
    return normalize_label(str(raw).replace("-", " ").replace("_", " "))


def _singular(token: str) -> str:
    """Plural español mínimo: velas→vela, difusores→difusor."""
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _tokens(raw: str) -> tuple[str, ...]:
    """Tokens comparables: singularizados, sin muletillas ni tokens de 1-2
    letras. `"quiero ver las religiosas"` → `("religiosa",)`."""
    return tuple(
        _singular(t)
        for t in _canonical(raw).split()
        if t not in _STOPWORDS and len(t) > 2
    )


def _fuzzy_score(q_tokens: tuple[str, ...], c_tokens: tuple[str, ...]) -> float:
    """Promedio del mejor parecido de cada token de la query contra la
    categoría. Token-level (no string completo) para que "religosas" pegue
    contra "velas religiosas" sin que los tokens sobrantes lo hundan."""
    if not q_tokens or not c_tokens:
        return 0.0
    total = 0.0
    for qt in q_tokens:
        total += max(
            SequenceMatcher(None, qt, ct).ratio() for ct in c_tokens
        )
    return total / len(q_tokens)


def deslugify(slug: str) -> str:
    """`velas-religiosas` → `Velas Religiosas` (fallback cuando el snapshot
    no trae el nombre real de la categoría — snapshots pre-filtro)."""
    return " ".join(w.capitalize() for w in str(slug).replace("-", " ").split())


def collect_categories(
    products: list[CatalogProductDTO],
) -> list[CatalogCategoryDTO]:
    """Closed-list de categorías presentes en el snapshot, con su conteo.

    Orden estable (label alfabético) — el LLM cita esta lista al cliente y no
    puede cambiar de un turno a otro.
    """
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for product in products:
        for slug in product.categories:
            counts[slug] = counts.get(slug, 0) + 1
            label = (product.category_labels or {}).get(slug)
            if label and slug not in labels:
                labels[slug] = label
    return sorted(
        (
            CatalogCategoryDTO(
                slug=slug,
                label=labels.get(slug) or deslugify(slug),
                product_count=count,
            )
            for slug, count in counts.items()
        ),
        key=lambda c: (normalize_label(c.label), c.slug),
    )


def resolve_category(
    query: str, categories: list[CatalogCategoryDTO]
) -> CategoryResolution:
    """Texto libre del cliente → UNA categoría del catálogo (o nada).

    Cascada determinista: exacto → contenido → difuso. Empate dentro del
    margen ⇒ `ambiguous` con candidatos (nunca elige por el cliente).
    """
    q_tokens = _tokens(query)
    if not q_tokens or not categories:
        return CategoryResolution(query=query)

    # 1) Exacto (singularizado, sin acentos/case): "velas religiosas",
    #    "velas-religiosas", "difusor" → difusores.
    for cat in categories:
        if q_tokens in (_tokens(cat.label), _tokens(cat.slug)):
            return CategoryResolution(
                query=query, matched=cat, confidence="exact"
            )

    # 2) Contenido: todos los tokens de la query están en la categoría
    #    ("religiosas" ⊂ "velas religiosas").
    q_set = set(q_tokens)
    contained = [
        cat
        for cat in categories
        if q_set <= (set(_tokens(cat.label)) | set(_tokens(cat.slug)))
    ]
    if len(contained) == 1:
        return CategoryResolution(
            query=query, matched=contained[0], confidence="partial"
        )
    if len(contained) > 1:
        return CategoryResolution(
            query=query, confidence="ambiguous", candidates=contained
        )

    # 3) Difuso (typos). Antes de puntuar, se descartan los tokens que no
    #    hablan de NINGUNA categoría ("porfa", "bicicletas"): se descartan
    #    globalmente — no por categoría — para que todas se puntúen sobre el
    #    mismo conjunto de tokens y el ganador sea comparable.
    cat_tokens = [
        (cat, set(_tokens(cat.label)) | set(_tokens(cat.slug)))
        for cat in categories
    ]
    relevant = tuple(
        qt
        for qt in q_tokens
        if any(
            _fuzzy_score((qt,), tuple(tokens)) >= _RELEVANCE_FLOOR
            for _, tokens in cat_tokens
        )
    )
    if not relevant:
        return CategoryResolution(query=query)
    q_tokens = relevant

    scored = sorted(
        (
            (
                max(
                    _fuzzy_score(q_tokens, _tokens(cat.label)),
                    _fuzzy_score(q_tokens, _tokens(cat.slug)),
                ),
                cat,
            )
            for cat in categories
        ),
        key=lambda pair: (-pair[0], pair[1].slug),
    )
    best_score, best_cat = scored[0]
    if best_score < _FUZZY_THRESHOLD:
        return CategoryResolution(query=query)
    tied = [cat for score, cat in scored if best_score - score <= _AMBIGUITY_MARGIN]
    if len(tied) > 1:
        return CategoryResolution(
            query=query, confidence="ambiguous", candidates=tied
        )
    return CategoryResolution(query=query, matched=best_cat, confidence="fuzzy")


def text_matches_loosely(query: str, text: str) -> bool:
    """Todos los tokens de `query` aparecen en `text`, tolerando acentos,
    plurales y typos. Se usa SOLO en el fallback de categoría (catálogo sin
    categorías cargadas): el search por `q` sigue siendo substring estricto.
    """
    q_tokens = _tokens(query)
    t_tokens = _tokens(text)
    if not q_tokens or not t_tokens:
        return False
    for qt in q_tokens:
        if any(qt in tt or tt in qt for tt in t_tokens):
            continue
        best = max(SequenceMatcher(None, qt, tt).ratio() for tt in t_tokens)
        if best < _FUZZY_THRESHOLD:
            return False
    return True
