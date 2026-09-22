"""Importación de contactos desde un archivo (CSV/TSV/texto) — PURO.

El operador sube una lista de números (base de una feria, clientes de otro
canal) para usarla como audiencia de una campaña. Acá solo se decide qué
filas son contactos válidos; ni I/O ni vault.

Normalización de teléfonos (E.164 sin `+`, como los `wa_<digits>` del vault):
  * Colombia por defecto: un celular de 10 dígitos que empieza en 3 recibe el
    indicativo 57 (`3001234567` → `573001234567`).
  * Ya con indicativo (`57…`, `+57…`, `0057…`) se deja tal cual.
  * Otros países: se acepta cualquier número con `+`/`00` de 8 a 15 dígitos.
  * Fijos colombianos (10 dígitos sin 3 inicial) y basura → None.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

_DIGITS_RE = re.compile(r"\d+")
#: Encabezados que reconocemos como columna de teléfono (case-insensitive).
_PHONE_HEADERS = (
    "telefono",
    "teléfono",
    "phone",
    "celular",
    "movil",
    "móvil",
    "whatsapp",
    "numero",
    "número",
    "tel",
    "cel",
    "mobile",
    "msisdn",
)
_NAME_HEADERS = ("nombre", "name", "cliente", "contacto", "first_name", "nombres")

#: Rango E.164: mínimo 8 dígitos (países chicos), máximo 15.
_E164_MIN = 8
_E164_MAX = 15

REASON_INVALID = "numero_invalido"
REASON_NO_PHONE_COLUMN = "sin_columna_telefono"


@dataclass(frozen=True)
class ImportedContact:
    phone: str
    name: str | None = None


@dataclass(frozen=True)
class RejectedRow:
    line: int
    reason: str


@dataclass(frozen=True)
class ContactsImport:
    contacts: list[ImportedContact]
    rejected: list[RejectedRow]
    duplicates: int


def normalize_phone(raw: str | None, *, default_country: str = "57") -> str | None:
    """Teléfono → dígitos E.164 (sin `+`) o None si no es un número usable."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    explicit_international = text.startswith("+") or text.startswith("00")
    digits = "".join(_DIGITS_RE.findall(text))
    if explicit_international and text.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None
    if explicit_international:
        return digits if _E164_MIN <= len(digits) <= _E164_MAX else None
    # Sin `+`: heurística Colombia.
    if len(digits) == 10:
        if digits[0] != "3":
            return None  # fijo (601…) u otra cosa: no es WhatsApp
        return default_country + digits
    if len(digits) == 12 and digits.startswith(default_country) and digits[2] == "3":
        return digits
    if _E164_MIN <= len(digits) <= _E164_MAX and len(digits) >= 11:
        # Ya trae indicativo de otro país (sin `+`): lo aceptamos tal cual.
        return digits
    return None


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _header_index(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    lowered = [h.strip().lower() for h in headers]
    for idx, h in enumerate(lowered):
        if any(h == c or h.startswith(c) for c in candidates):
            return idx
    return None


def _looks_like_header(row: list[str]) -> bool:
    return not any(normalize_phone(cell) for cell in row)


def _phone_from_free_text(cell: str) -> str | None:
    """Última secuencia larga de dígitos de una celda ("Camila - 3001234567")."""
    candidates = [m for m in re.findall(r"[+\d][\d\s\-().]{6,}\d", cell)]
    for cand in reversed(candidates):
        phone = normalize_phone(cand)
        if phone:
            return phone
    return None


def parse_contacts_file(text: str) -> ContactsImport:
    """Parsea el archivo completo. Tolerante: BOM, `,`/`;`/tab, con o sin
    encabezado, columnas extra. Devuelve contactos únicos + rechazados con su
    número de línea (1-based, contando el encabezado) + cuántos se dedup."""
    text = text.lstrip("﻿")
    lines = [ln for ln in text.splitlines()]
    if not any(ln.strip() for ln in lines):
        return ContactsImport(contacts=[], rejected=[], duplicates=0)

    delimiter = _sniff_delimiter("\n".join(lines[:20]))
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))

    first_nonempty = next((r for r in rows if any(c.strip() for c in r)), [])
    has_header = _looks_like_header(first_nonempty)
    phone_col: int | None = None
    name_col: int | None = None
    if has_header:
        phone_col = _header_index(first_nonempty, _PHONE_HEADERS)
        name_col = _header_index(first_nonempty, _NAME_HEADERS)
        if phone_col is None:
            # Encabezado sin columna reconocible: ¿alguna columna trae números?
            data_rows = [r for r in rows[1:] if any(c.strip() for c in r)]
            phone_col = _guess_phone_column(data_rows)
            if phone_col is None:
                return ContactsImport(
                    contacts=[],
                    rejected=[RejectedRow(line=1, reason=REASON_NO_PHONE_COLUMN)],
                    duplicates=0,
                )
    else:
        data_rows = [r for r in rows if any(c.strip() for c in r)]
        phone_col = _guess_phone_column(data_rows)

    contacts: list[ImportedContact] = []
    rejected: list[RejectedRow] = []
    seen: set[str] = set()
    duplicates = 0
    start = 1 if has_header else 0
    for offset, row in enumerate(rows[start:], start=start):
        line_no = offset + 1
        if not any(c.strip() for c in row):
            continue
        phone = None
        if phone_col is not None and phone_col < len(row):
            phone = normalize_phone(row[phone_col]) or _phone_from_free_text(
                row[phone_col]
            )
        if phone is None and not has_header:
            # Sin encabezado: buscar en toda la fila (texto libre).
            phone = _phone_from_free_text(delimiter.join(row))
        if phone is None:
            rejected.append(RejectedRow(line=line_no, reason=REASON_INVALID))
            continue
        if phone in seen:
            duplicates += 1
            continue
        seen.add(phone)
        name: str | None = None
        if name_col is not None and name_col < len(row):
            name = row[name_col].strip() or None
        contacts.append(ImportedContact(phone=phone, name=name))
    return ContactsImport(contacts=contacts, rejected=rejected, duplicates=duplicates)


def _guess_phone_column(rows: list[list[str]]) -> int | None:
    """Columna con más celdas que parsean como teléfono (None si ninguna)."""
    if not rows:
        return None
    width = max(len(r) for r in rows)
    best: tuple[int, int] | None = None
    for col in range(width):
        hits = sum(
            1
            for r in rows
            if col < len(r)
            and (normalize_phone(r[col]) or _phone_from_free_text(r[col]))
        )
        if hits and (best is None or hits > best[1]):
            best = (col, hits)
    return best[0] if best else None


__all__ = [
    "REASON_INVALID",
    "REASON_NO_PHONE_COLUMN",
    "ContactsImport",
    "ImportedContact",
    "RejectedRow",
    "normalize_phone",
    "parse_contacts_file",
]
