"""Conectar/desconectar nodos del task graph EDITANDO los manifests — el backend del
modo edición del explorer (estilo n8n). La verdad SIGUE siendo el YAML (git + cert +
TDD): esto no introduce otra fuente, solo mueve la mano que edita.

Tres piezas:
- `validate_connection` — PURA (no escribe). El gate de compatibilidad que la UI consulta
  antes de dibujar la línea: G-BIND / G-BIND-AGENT / G-WIRE / G-CONTRACT + tipos
  (la entrada tiene que ACEPTAR la salida del otro, por contrato declarado).
- `connect` / `disconnect` — edición TEXTUAL quirúrgica del manifest (los comentarios son
  load-bearing y `yaml.dump` los destruiría; no hay ruamel en la imagen — cero deps, L-6).
  Red de seguridad: tras escribir se RECARGA el manifest y corren los checks del TestKit;
  cualquier falla → ROLLBACK byte-idéntico + el error visible. Nunca queda un manifest roto.

Alcance deliberado: se editan las listas `tools:` / `agents:` del NODO RAÍZ del manifest
(que es lo que el grafo del explorer proyecta, `_edges_of`). Los `consumes:` (ports) no se
editan desde la UI: el port vive acoplado a su capability (G-PORT) — quitarlo es un cambio
de código, no de wiring.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from sdk.manifest_model import AgentNode, load_manifest
from sdk.registry import discover_agents, discover_tools
from sdk.testkit.checks import run_checks

_STATE_REF = re.compile(r"^\$state\.([A-Za-z_][A-Za-z0-9_]*)$")
_PLAIN_SCALAR = re.compile(r"^[\w$][\w.$/@:-]*$")


# --------------------------------------------------------------------- helpers

def _split(node_id: str) -> tuple[str, str]:
    kind, sep, ident = node_id.partition(":")
    if not sep or not ident:
        raise ValueError(f"id de nodo inválido '{node_id}' (esperaba 'kind:id')")
    return kind, ident


def _manifest_path(ga_root: Path, agent_id: str) -> Path | None:
    manifests = ga_root / "manifests"
    cand = sorted(manifests.glob(f"{agent_id}.agent.yaml")) + sorted(
        manifests.glob(f"{agent_id}.taskgraph.yaml"))
    return cand[0] if cand else None


def _state_key(value) -> str | None:
    m = _STATE_REF.match(str(value))
    return m.group(1) if m else None


def _specs(d: dict) -> dict:
    return {k: v.model_dump() for k, v in d.items()}


def _reaches(catalog: dict[str, AgentNode], start: str, goal: str, seen: set | None = None) -> bool:
    """¿`start` llega a `goal` siguiendo referencias agent:// del catálogo? (guard de ciclos)"""
    if start == goal:
        return True
    seen = seen if seen is not None else set()
    if start in seen:
        return False
    seen.add(start)
    node = catalog.get(start)
    if node is None:
        return False
    return any(_reaches(catalog, a.ref_agent_id, goal, seen) for a in node.agents if a.ref_agent_id)


# ------------------------------------------------------------------- validación

def validate_connection(ga_root: Path, source: str, target: str, binding: dict | None = None,
                        inputs: dict | None = None) -> dict:
    """El gate PURO de una conexión. `binding`/`inputs` en None = modo informativo (devuelve
    el contrato del destino + el mapeo sugerido para que la UI arme el formulario); con dict
    = validación completa pre-escritura."""
    ga_root = Path(ga_root)
    errors: list[str] = []
    warnings: list[str] = []
    res: dict = {"ok": False, "kind": None, "errors": errors, "warnings": warnings,
                 "target_contract": None, "suggested": {}, "state_keys": []}
    try:
        s_kind, s_id = _split(source)
        t_kind, t_id = _split(target)
    except ValueError as e:
        errors.append(str(e))
        return res
    if s_kind != "agent":
        errors.append(f"solo un agente puede ser origen de una conexión (llegó '{source}')")
        return res
    path = _manifest_path(ga_root, s_id)
    if path is None:
        errors.append(f"no existe el manifest del agente '{s_id}' en manifests/")
        return res
    m = load_manifest(path)

    if t_kind == "tool":
        res["kind"] = "uses"
        _validate_tool(ga_root, m, t_id, binding, res)
    elif t_kind == "agent":
        res["kind"] = "agent"
        _validate_agent(ga_root, m, t_id, inputs, res)
    else:
        errors.append(f"un nodo '{t_kind}' no se conecta desde la UI — el `consumes:` de un "
                      "port vive acoplado a su capability (G-PORT)")
    res["ok"] = not errors
    return res


def _validate_tool(ga_root: Path, m: AgentNode, tool_id: str, binding: dict | None, res: dict) -> None:
    errors, warnings = res["errors"], res["warnings"]
    catalog = {t.id: t for t in discover_tools(ga_root)}
    contract = catalog.get(tool_id)
    if contract is None:
        errors.append(f"[G-BIND] la tool '{tool_id}' no está en el catálogo (tools/)")
        return
    res["target_contract"] = {"inputs": _specs(contract.inputs), "outputs": _specs(contract.outputs)}
    res["suggested"] = {k: f"$state.{k}" for k in contract.inputs}
    if any(t.ref_id == tool_id for t in m.tools):
        errors.append(f"'{m.name}' ya usa la tool '{tool_id}' — ya están conectados")
        return
    if contract.side_effect == "outward" and not contract.approval_required:
        errors.append(f"[T-DUR] '{tool_id}' es outward sin approval_required — certificá la tool "
                      "antes de conectarla")
    if binding is None:
        return  # modo informativo
    declared = contract.inputs
    unknown = sorted(k for k in binding if k not in declared)
    if unknown:
        errors.append(f"[G-BIND] el `with:` nombra {unknown} que no son inputs del contrato de "
                      f"'{tool_id}' (declara: {sorted(declared)})")
    missing = sorted(k for k, s in declared.items() if s.required and k not in binding)
    if missing:
        errors.append(f"[G-BIND] faltan cablear los inputs required {missing} de '{tool_id}'")
    src_inputs = m.contract.inputs if m.contract else {}
    for k, v in binding.items():
        key = _state_key(v)
        if key is None:
            warnings.append(f"'{k}: {v}' no es una referencia $state.<clave> — se persiste literal")
            continue
        if k not in declared:
            continue
        spec = src_inputs.get(key)
        if spec is None:
            warnings.append(f"'$state.{key}' no está en el contrato de '{m.name}' — no verificable "
                            "(¿clave intermedia del run o seed?)")
        elif spec.type != declared[k].type:
            errors.append(f"incompatible: '{tool_id}.{k}' espera {declared[k].type} pero "
                          f"'$state.{key}' es {spec.type} según el contrato de '{m.name}'")


def _validate_agent(ga_root: Path, m: AgentNode, target_id: str, inputs: dict | None, res: dict) -> None:
    errors, warnings = res["errors"], res["warnings"]
    if not m.is_supervisor:
        errors.append(f"'{m.name}' no es supervisor — solo un supervisor compone agentes (`agents:`)")
        return
    catalog = {a.name: a for a in discover_agents(ga_root)}
    ref = catalog.get(target_id)
    if ref is None:
        errors.append(f"[G-BIND-AGENT] 'agent://{target_id}' no está en el catálogo "
                      "(manifests/*.agent.yaml)")
        return
    if target_id == m.name:
        errors.append(f"'{m.name}' no puede componerse a sí mismo (ciclo)")
        return
    if any(a.ref_agent_id == target_id for a in m.agents):
        errors.append(f"'{m.name}' ya compone a '{target_id}' — ya están conectados")
        return
    if _reaches(catalog, target_id, m.name):
        errors.append(f"conectar '{m.name}' → '{target_id}' formaría un ciclo "
                      f"('{target_id}' ya llega a '{m.name}' por sus referencias)")

    contract = ref.contract
    if contract is not None:
        res["target_contract"] = {"inputs": _specs(contract.inputs), "outputs": _specs(contract.outputs)}
        res["suggested"] = {k: f"$state.{k}" for k in contract.inputs}

    # qué claves de estado PRODUCE el río arriba (los outputs declarados de los miembros):
    produced: dict[str, tuple[str, object]] = {}
    for a in m.agents:
        ra = catalog.get(a.ref_agent_id) if a.ref_agent_id else None
        if ra is not None and ra.contract is not None:
            for k, spec in ra.contract.outputs.items():
                produced[k] = (ra.name, spec)
    res["state_keys"] = [{"key": k, "type": s.type, "source": src}
                         for k, (src, s) in sorted(produced.items())]

    composing = m.strategy not in ("router", "manual")
    if inputs is None:
        return  # modo informativo
    if not composing:
        # router/manual despachan el input CRUDO a UN miembro: no hay wiring que validar
        # (espejo de G-WIRE/G-CONTRACT en los checks).
        if inputs:
            warnings.append(f"'{m.name}' (strategy={m.strategy}) despacha el input crudo — "
                            "el `inputs:` declarado no se usa para rutear")
        return
    if not inputs:
        errors.append(f"[G-WIRE] '{m.name}' (strategy={m.strategy}) compone: la conexión exige "
                      "`inputs:` (el wiring state→input del task graph)")
    if contract is None:
        if inputs:
            warnings.append(f"'{target_id}' no declara `contract:` — el wiring no es verificable "
                            "(declaralo para conectar con validación)")
        return
    declared = contract.inputs
    unknown = sorted(k for k in inputs if k not in declared)
    if unknown:
        errors.append(f"[G-CONTRACT] el wiring nombra {unknown} que no son inputs del contrato de "
                      f"'{target_id}' (declara: {sorted(declared)})")
    missing = sorted(k for k, s in declared.items() if s.required and k not in inputs)
    if missing:
        errors.append(f"[G-CONTRACT] faltan cablear los inputs required {missing} del contrato de "
                      f"'{target_id}'")
    for k, v in inputs.items():
        key = _state_key(v)
        if key is None:
            warnings.append(f"'{k}: {v}' no es una referencia $state.<clave> — se persiste literal")
            continue
        if k not in declared:
            continue
        prod = produced.get(key)
        if prod is None:
            warnings.append(f"nadie produce '$state.{key}' río arriba de '{m.name}' — se asume "
                            "que viene del seed")
        elif prod[1].type != declared[k].type:
            errors.append(f"incompatible: '{target_id}.{k}' espera {declared[k].type} pero "
                          f"'{prod[0]}' produce '{key}' como {prod[1].type}")


# ------------------------------------------------------------- edición textual

def _scalar(v) -> str:
    s = str(v)
    return s if _PLAIN_SCALAR.match(s) else json.dumps(s, ensure_ascii=False)


def _item_lines(ref: str, map_key: str, mapping: dict) -> list[str]:
    lines = [f"  - uses: {ref}"]
    if mapping:
        lines.append(f"    {map_key}:")
        for k, v in mapping.items():
            lines.append(f"      {k}: {_scalar(v)}")
    return lines


def _block(lines: list[str], key: str) -> tuple[int, int] | None:
    """(idx de la línea `key:`, fin exclusivo) del bloque TOP-LEVEL. Un comentario a col 0
    corta el bloque (documenta la próxima key) — es el punto seguro de append."""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"{key}:"):
            start = i
            break
    if start is None:
        return None
    j = start + 1
    while j < len(lines) and (not lines[j].strip() or lines[j].startswith(" ")):
        j += 1
    return start, j


def _items(lines: list[str], start: int, end: int) -> list[tuple[int, int, dict]]:
    """Los items `- ` del bloque: (inicio incl. sus comentarios pegados, fin, dict parseado)."""
    idxs = [i for i in range(start + 1, end) if lines[i].startswith("  - ")]
    starts = []
    for i in idxs:
        k = i
        while k - 1 > start and lines[k - 1].startswith("  ") and lines[k - 1].strip().startswith("#"):
            k -= 1
        starts.append(k)
    out = []
    for pos, dash in enumerate(idxs):
        item_end = starts[pos + 1] if pos + 1 < len(idxs) else end
        try:
            parsed = yaml.safe_load("\n".join(lines[dash:item_end]))[0]
        except Exception:  # noqa: BLE001 — item ilegible: no matchea, no crashea
            parsed = {}
        out.append((starts[pos], item_end, parsed if isinstance(parsed, dict) else {}))
    return out


def _insert_item(lines: list[str], key: str, item: list[str], fname: str) -> tuple[list[str] | None, str | None]:
    blk = _block(lines, key)
    if blk is None:
        out = list(lines)
        while out and not out[-1].strip():
            out.pop()
        return out + ["", f"{key}:"] + item + [""], None
    start, end = blk
    rest = lines[start][len(key) + 1:]
    val = rest.partition("#")[0].strip()
    if val == "[]":
        comment = rest.partition("#")[2].strip()
        new = list(lines)
        new[start] = f"{key}:" + (f"  # {comment}" if comment else "")
        return new[:start + 1] + item + new[start + 1:], None
    if val:
        return None, f"'{key}:' de {fname} tiene forma inline no editable — editá el YAML a mano"
    at = end
    while at - 1 > start and not lines[at - 1].strip():
        at -= 1
    return lines[:at] + item + lines[at:], None


def _ref_matches(parsed: dict, kind: str, target_id: str) -> bool:
    uses = str(parsed.get("uses") or "")
    if kind == "uses":
        return uses.split("@", 1)[0] == target_id
    return uses.split("://", 1)[-1].split("@", 1)[0] == target_id


def _verify_or_rollback(ga_root: Path, path: Path, original: str, present) -> dict:
    """La red de seguridad: recargar + checks + confirmar que el cambio SE VE en el modelo.
    Cualquier falla → el archivo vuelve byte-idéntico y el error se reporta (no hay estado roto)."""
    try:
        m = load_manifest(path)
    except Exception as e:  # noqa: BLE001 — YAML/modelo roto por la edición textual
        path.write_text(original, encoding="utf-8")
        return {"ok": False, "errors": [f"rollback — el manifest editado no parsea: {e}"],
                "warnings": [], "file": None}
    res = run_checks(m, ga_root)
    if res["errors"]:
        path.write_text(original, encoding="utf-8")
        return {"ok": False, "errors": ["rollback — la edición rompía la cert:"] + res["errors"],
                "warnings": res["warnings"], "file": None}
    if not present(m):
        path.write_text(original, encoding="utf-8")
        return {"ok": False, "errors": ["rollback — la edición no se reflejó en el manifest "
                                        "(la edición textual no encontró dónde escribir)"],
                "warnings": [], "file": None}
    return {"ok": True, "errors": [], "warnings": res["warnings"],
            "file": str(path.relative_to(ga_root))}


# ----------------------------------------------------------------- connect/disconnect

def connect(ga_root: Path, source: str, target: str, binding: dict | None = None,
            inputs: dict | None = None) -> dict:
    """Valida (gate completo) y persiste la conexión en el manifest del ORIGEN."""
    ga_root = Path(ga_root)
    v = validate_connection(ga_root, source, target,
                            binding=binding if binding is not None else {},
                            inputs=inputs if inputs is not None else {})
    if not v["ok"]:
        return {"ok": False, "errors": v["errors"], "warnings": v["warnings"], "file": None}
    s_id = _split(source)[1]
    t_id = _split(target)[1]
    path = _manifest_path(ga_root, s_id)
    original = path.read_text(encoding="utf-8")
    lines = original.split("\n")
    if v["kind"] == "uses":
        contract = next(t for t in discover_tools(ga_root) if t.id == t_id)
        item = _item_lines(f"{t_id}@{contract.major}", "with", binding or {})
        key, present = "tools", (lambda m: any(t.ref_id == t_id for t in m.tools))
    else:
        item = _item_lines(f"agent://{t_id}@1", "inputs", inputs or {})
        key, present = "agents", (lambda m: any(a.ref_agent_id == t_id for a in m.agents))
    new_lines, err = _insert_item(lines, key, item, path.name)
    if err:
        return {"ok": False, "errors": [err], "warnings": v["warnings"], "file": None}
    path.write_text("\n".join(new_lines), encoding="utf-8")
    out = _verify_or_rollback(ga_root, path, original, present)
    out["warnings"] = v["warnings"] + out["warnings"]
    return out


def disconnect(ga_root: Path, source: str, target: str, kind: str) -> dict:
    """Remueve la conexión (el item de `tools:`/`agents:`) del manifest del ORIGEN."""
    ga_root = Path(ga_root)
    try:
        s_kind, s_id = _split(source)
        t_kind, t_id = _split(target)
    except ValueError as e:
        return {"ok": False, "errors": [str(e)], "warnings": [], "file": None}
    if kind == "consumes" or t_kind == "port":
        return {"ok": False, "errors": ["un port no se desconecta desde la UI — el `consumes:` "
                                        "vive acoplado a su capability (G-PORT); es un cambio de "
                                        "código, no de wiring"], "warnings": [], "file": None}
    if kind not in ("uses", "agent") or s_kind != "agent":
        return {"ok": False, "errors": [f"relación '{kind}' ({source} → {target}) no editable"],
                "warnings": [], "file": None}
    path = _manifest_path(ga_root, s_id)
    if path is None:
        return {"ok": False, "errors": [f"no existe el manifest del agente '{s_id}'"],
                "warnings": [], "file": None}
    original = path.read_text(encoding="utf-8")
    lines = original.split("\n")
    key = "tools" if kind == "uses" else "agents"
    blk = _block(lines, key)
    if blk is None:
        return {"ok": False, "errors": [f"'{s_id}' no tiene `{key}:` — no están conectados"],
                "warnings": [], "file": None}
    items = _items(lines, *blk)
    hit = next(((a, b) for a, b, parsed in items if _ref_matches(parsed, kind, t_id)), None)
    if hit is None:
        return {"ok": False, "errors": [f"'{s_id}' no está conectado a '{t_id}' — nada que desconectar"],
                "warnings": [], "file": None}
    new_lines = lines[:hit[0]] + lines[hit[1]:]
    if len(items) == 1:  # quedó vacío → forma explícita `key: []` (parseable; SUP decide si es válido)
        new_lines[blk[0]] = f"{key}: []"
    text = "\n".join(new_lines)
    if original.endswith("\n") and not text.endswith("\n"):
        text += "\n"  # el item era la cola del archivo — no comerse el newline final (diff limpio)
    path.write_text(text, encoding="utf-8")
    if kind == "uses":
        absent = lambda m: all(t.ref_id != t_id for t in m.tools)  # noqa: E731
    else:
        absent = lambda m: all(a.ref_agent_id != t_id for a in m.agents)  # noqa: E731
    return _verify_or_rollback(ga_root, path, original, absent)
