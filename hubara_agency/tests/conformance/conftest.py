"""tests/conformance — los TCK instanciados por plugin (F-SDK-2).

Todo test de este directorio recibe el marker ``architecture`` automático:
la conformance de plugins ES parte del gate que bloquea merge a main
(``pytest -m architecture`` y el job plugin-certification de CI la corren).
"""
from __future__ import annotations

from pathlib import Path

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    conf_dir = str(Path(__file__).parent)
    for item in items:
        if str(item.fspath).startswith(conf_dir):
            item.add_marker(pytest.mark.architecture)
