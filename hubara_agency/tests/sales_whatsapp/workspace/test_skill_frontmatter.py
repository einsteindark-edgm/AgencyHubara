"""Verifica que la skill hubara_catalog esta deprecada (always: false)
y que el frontmatter sigue siendo single-line inline JSON (no block scalar).
"""
from __future__ import annotations

import re
from pathlib import Path

_SKILL = (
    Path(__file__).resolve().parents[3]
    / "src/sales_whatsapp/workspace/skills/hubara_catalog/SKILL.md"
)


def test_metadata_is_single_line_inline_json():
    text = _SKILL.read_text(encoding="utf-8")
    # Frontmatter rule: single-line inline JSON, NO block scalar.
    assert "metadata: |" not in text, (
        "block scalar form silently breaks the loader (exoclaw quirk)"
    )
    assert re.search(r"^metadata:\s*\{", text, flags=re.MULTILINE), (
        "metadata must be inline JSON, not multiline"
    )


def test_skill_marked_deprecated():
    text = _SKILL.read_text(encoding="utf-8")
    assert '"always": false' in text, (
        "skill debe estar marcada always:false (HU-05 rollout)"
    )
