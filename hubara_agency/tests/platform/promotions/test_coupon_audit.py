"""Registro de cambios de la central de cupones (Fase 4, D6).

Cualquier usuario del dashboard crea y edita cupones; cada acción queda con
el actor VERIFICADO de la sesión, en un JSONL append-only del vault.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from src.platform.promotions.audit import CouponAuditLog, FakeCouponAuditLog


def test_audit_log_appends_one_line_per_action_with_actor(tmp_path: Path) -> None:
    log = CouponAuditLog(tmp_path, clock=lambda: "2026-09-23T17:00:00Z")

    log.append(actor="ana", action="create", promotion_id="promo_1", code="AMOR27",
               detail={"percentage": 10})
    log.append(actor="luis", action="set_status", promotion_id="promo_1", code="AMOR27",
               detail={"status": "inactive"})
    log.append(actor="ana", action="create", promotion_id="promo_2", code="OTRO")

    lines = (tmp_path / "_promotions" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines][0] == {
        "ts": "2026-09-23T17:00:00Z", "actor": "ana", "action": "create",
        "promotion_id": "promo_1", "code": "AMOR27", "detail": {"percentage": 10},
    }
    # Lo más nuevo primero, filtrado por cupón.
    assert [(e.actor, e.action) for e in log.entries(promotion_id="promo_1")] == [
        ("luis", "set_status"), ("ana", "create"),
    ]


def test_audit_log_concurrent_appends_keep_every_line(tmp_path: Path) -> None:
    log = CouponAuditLog(tmp_path)
    threads = [
        threading.Thread(target=lambda i=i: log.append(actor=f"u{i}", action="create", promotion_id="promo_1", code="X"))
        for i in range(25)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(log.entries(promotion_id="promo_1", limit=100)) == 25


def test_audit_log_skips_corrupt_lines(tmp_path: Path) -> None:
    log = CouponAuditLog(tmp_path)
    log.append(actor="ana", action="create", promotion_id="promo_1", code="X")
    with (tmp_path / "_promotions" / "audit.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{roto\n")

    assert [e.actor for e in log.entries()] == ["ana"]


def test_fake_audit_log_records_in_memory() -> None:
    log = FakeCouponAuditLog()
    log.append(actor="service", action="delete", promotion_id="promo_9", code="X")

    assert [(e.actor, e.action) for e in log.entries(promotion_id="promo_9")] == [("service", "delete")]
