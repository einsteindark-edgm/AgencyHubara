"""SEC-14: orígenes CORS configurables (default "*" en dev, restringido en prod).

`config.cors_allowed_origins()` parsea `CORS_ALLOWED_ORIGINS` (CSV) a lista.
"""
from __future__ import annotations

import pytest

from src.platform import config


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("*", ["*"]),
        ("", ["*"]),  # vacío → permisivo (dev)
        ("https://dash.hubara.co", ["https://dash.hubara.co"]),
        (
            "https://a.co, https://b.co",
            ["https://a.co", "https://b.co"],
        ),
        ("  https://a.co ,, ", ["https://a.co"]),  # trim + descarta vacíos
    ],
)
def test_cors_allowed_origins_parsing(
    raw: str, expected: list[str], monkeypatch
) -> None:
    monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", raw)
    assert config.cors_allowed_origins() == expected
