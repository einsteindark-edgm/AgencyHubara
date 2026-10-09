"""Regla de oro del SDK: `src.sdk.identitykit` re-exporta la identidad PURA de
un cliente de WhatsApp (teléfono o id de Meta) para los plugins.

Por qué es un kit aparte: el parser del webhook la necesita y el contrato
R-DIP #8 (`parsers-pure`) le prohíbe importar I/O (`httpx`, `temporalio`);
`messagingkit` los trae (activities de envío). El check es por IDENTIDAD
(`is`) y por pureza: el kit solo depende de `src.platform.whatsapp.user_id`,
que solo depende de la stdlib y de las constantes.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SDK = Path(__file__).resolve().parents[2] / "src" / "sdk"


def _imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module != "__future__":
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_identitykit_reexports_the_customer_identity_helpers() -> None:
    import src.platform.whatsapp.user_id as impl
    import src.sdk.identitykit as kit

    assert kit.address_from_user_id is impl.address_from_user_id
    assert kit.is_user_id_address is impl.is_user_id_address
    assert kit.is_customer_session_id is impl.is_customer_session_id


def test_identitykit_only_depends_on_the_pure_identity_module() -> None:
    assert _imported_modules(_SDK / "identitykit.py") == {"src.platform.whatsapp.user_id"}


def test_the_identity_module_behind_identitykit_is_pure() -> None:
    impl = _SDK.parent / "platform" / "whatsapp" / "user_id.py"
    assert _imported_modules(impl) <= {"re", "typing", "src.platform.constants"}
