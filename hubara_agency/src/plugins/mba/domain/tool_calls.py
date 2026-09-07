"""Una llamada del connector, validada contra el ``request_definition`` autorado.

Meta invoca ``/api/mba/tools/<tool>`` con los parámetros que el agente extrajo
más el teléfono del cliente (macro ``WHATSAPP_PHONE_NUMBER``). Este módulo:

- deriva el contrato de cada tool de los ``requests[]`` que YA se registran en
  Meta (``connector_tools``): un solo schema, el que Meta ve;
- convierte el teléfono E.164 en la ``session_key`` de Hubara (``wa_<dígitos>``);
- valida tipos, requeridos y tamaños; ignora parámetros desconocidos (Meta
  puede agregar campos) y devuelve errores acumulados para un 422 legible.

Puro: sin I/O, sin FastAPI.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.plugins.mba.domain.config import MbaConfigDTO

MAX_STRING_LEN = 2000
MAX_ARRAY_LEN = 50
_PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")  # [0-9] y no \d: \d acepta dígitos Unicode
INT_MAX = 2**31


class ToolCallError(Exception):
    """Request inválido: ``errors`` lista cada problema (nombre: motivo)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors

    @property
    def payload(self) -> dict[str, Any]:
        return {"error": "invalid_request", "errors": list(self.errors)}


@dataclass(frozen=True)
class ToolContract:
    name: str
    method: str  # GET | POST
    phone_param: str
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    tool: str
    session_key: str
    customer_phone: str
    params: dict[str, Any] = field(default_factory=dict)


def session_key_from_phone(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    compact = re.sub(r"[\s\-()]", "", raw.strip())
    if not _PHONE_RE.match(compact):
        return None
    return "wa_" + compact.lstrip("+")


def _normalize_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    items = out.get("items")
    if isinstance(items, str):
        try:
            out["items"] = json.loads(items)
        except json.JSONDecodeError:
            out["items"] = {}
    return out


def contracts_from_config(cfg: MbaConfigDTO) -> dict[str, ToolContract]:
    """Contratos por tool a partir de los requests ``connector_tools`` del agente."""
    out: dict[str, ToolContract] = {}
    for req in cfg.requests:
        if req.section != "connector_tools":
            continue
        body = req.body
        rd = body.get("request_definition") or {}
        method = str(rd.get("method") or "GET").upper()
        raw = rd.get("query_parameters") if method == "GET" else (rd.get("body") or {}).get("params")
        params: dict[str, dict[str, Any]] = {}
        required: list[str] = []
        phone_param = "customer_phone"
        for name, schema in (raw or {}).items():
            if isinstance(schema, Mapping) and schema.get("binding"):
                phone_param = name
                continue
            params[name] = _normalize_schema(schema)
            if schema.get("required"):
                required.append(name)
        name = str(body.get("name") or "")
        out[name] = ToolContract(
            name=name, method=method, phone_param=phone_param, params=params, required=tuple(required)
        )
    return out


def _coerce(path: str, value: Any, schema: Mapping[str, Any], *, lenient_ints: bool) -> tuple[Any, list[str]]:
    kind = str(schema.get("type") or "string")
    if kind == "string":
        if not isinstance(value, str):
            return None, [f"{path}: debe ser texto"]
        if len(value) > MAX_STRING_LEN:
            return None, [f"{path}: supera {MAX_STRING_LEN} caracteres"]
        return value, []
    if kind == "integer":
        if isinstance(value, bool):
            return None, [f"{path}: debe ser un entero"]
        if lenient_ints and isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]{1,12}", value.strip()):
            value = int(value)
        if isinstance(value, int):
            if abs(value) > INT_MAX:
                return None, [f"{path}: fuera de rango"]
            return value, []
        return None, [f"{path}: debe ser un entero"]
    if kind == "boolean":
        return (value, []) if isinstance(value, bool) else (None, [f"{path}: debe ser true/false"])
    if kind == "array":
        if not isinstance(value, list):
            return None, [f"{path}: debe ser una lista"]
        if not value:
            return None, [f"{path}: no puede estar vacía"]
        if len(value) > MAX_ARRAY_LEN:
            return None, [f"{path}: supera {MAX_ARRAY_LEN} elementos"]
        item_schema = schema.get("items") or {}
        out: list[Any] = []
        errors: list[str] = []
        for i, item in enumerate(value):
            v, errs = _coerce(f"{path}[{i}]", item, item_schema, lenient_ints=lenient_ints)
            errors.extend(errs)
            if not errs:
                out.append(v)
        return (out if not errors else None), errors
    if kind == "object":
        if not isinstance(value, Mapping):
            return None, [f"{path}: debe ser un objeto"]
        props: Mapping[str, Any] = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        out_obj: dict[str, Any] = {}
        errors = []
        for name, sub in props.items():
            if name not in value or value[name] is None:
                if name in required:
                    errors.append(f"{path}.{name}: requerido")
                continue
            v, errs = _coerce(f"{path}.{name}", value[name], sub, lenient_ints=lenient_ints)
            errors.extend(errs)
            if not errs:
                out_obj[name] = v
        return (out_obj if not errors else None), errors
    return value, []


def parse_tool_call(contract: ToolContract, raw: Mapping[str, Any]) -> ToolCall:
    """Valida ``raw`` (query params o body JSON) contra el contrato. Lanza ``ToolCallError``."""
    errors: list[str] = []
    phone = raw.get(contract.phone_param)
    session_key = session_key_from_phone(phone)
    if session_key is None:
        errors.append(f"{contract.phone_param}: teléfono requerido en formato E.164")
    lenient = contract.method == "GET"  # en query todo llega como string
    params: dict[str, Any] = {}
    for name, schema in contract.params.items():
        if name not in raw or raw[name] is None:
            if name in contract.required:
                errors.append(f"{name}: requerido")
            continue
        value, errs = _coerce(name, raw[name], schema, lenient_ints=lenient)
        errors.extend(errs)
        if not errs:
            params[name] = value
    if errors:
        raise ToolCallError(errors)
    return ToolCall(tool=contract.name, session_key=str(session_key), customer_phone=str(phone), params=params)
