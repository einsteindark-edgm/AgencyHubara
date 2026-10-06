"""`ventas-2`: la siguiente versión del paquete de la tienda (PAQUETES_DE_DECISION.md F7).

La certificación de F7 dejó a la vista que el asunto `promocion` no tenía
regla ② desde que llegó (rafaga-v4): nada lo daba por atendido dentro del
turno, así que en el workflow V1 con el motor, cuando una tool cortaba el
turno, «¿tienen promociones?» contaba como sin atender aunque el bot hubiera
mostrado las promociones, y pedía una ronda más. `ventas@1` no se edita (un
paquete publicado es inmutable): `ventas-2` es `ventas` con esa regla y nada
más. Se promueve nombrándolo en Terraform (`decisions_bundle`).

El laboratorio no lo puede medir con un brazo (`B@ventas-2`): el bot B es el
workflow V2, que no usa la regla ② (solo el contrato). Por eso la prueba es
determinista: la regla ② de `ventas-2`, sobre la misma función que aplica el
workflow (`uncovered_topics`).

En un clon de forge `ventas-2` no viaja (es un experimento de esta tienda):
las pruebas se saltan.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan, uncovered_topics
from src.plugins.chats.agent.sales.decisions.policies import turno_v1
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH

V1, V2 = BUNDLES_DIR / "ventas", BUNDLES_DIR / "ventas-2"
PROMOCION = {"tools": ["apply_coupon", "list_promotions"], "words": ["promocion", "descuento", "cupon"]}
#: forge no viaja a un clon (`copy_exclude`): sin él, esto es otra tienda.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()


def _need_v2() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V2 / "bundle.yaml").is_file(), "falta el paquete ventas-2"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_ventas_2_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle, load_bundle

    _need_v2()

    assert check_bundle(V2, CATALOG_PATH) == []
    assert load_bundle(V2, CATALOG_PATH).ref == "ventas-2@2"


def test_ventas_2_is_ventas_with_the_promotions_rule_and_nothing_else() -> None:
    _need_v2()

    v1_files = sorted(p.relative_to(V1) for p in V1.rglob("*.yaml"))
    assert sorted(p.relative_to(V2) for p in V2.rglob("*.yaml")) == v1_files
    for rel in v1_files:
        old, new = _load(V1 / rel), _load(V2 / rel)
        if str(rel) == "bundle.yaml":
            assert {**new, "id": "ventas", "version": 1} == old
        elif str(rel) == "turn.yaml":
            assert old["coverage"]["promocion"] == {}
            assert new["coverage"]["promocion"] == PROMOCION
            assert {**new, "coverage": {**new["coverage"], "promocion": {}}} == old
        else:
            assert new == old, rel


def _rules(bundle_dir: Path) -> dict[str, Any]:
    from src.plugins.chats.agent.sales.decisions.turn import tables_of
    from src.sdk.decisionkit import load_bundle

    turn = load_bundle(bundle_dir, CATALOG_PATH).turn
    plan = TurnPlan(ok=True, topics=(PlanTopic("promocion", 1, 0.9),))
    return turno_v1.coverage_rules(plan, tables_of(turn))


@pytest.mark.parametrize(
    ("tools_used", "shown"),
    [
        (["list_promotions"], ""),                                       # mostró las promociones
        (["apply_coupon"], ""),                                          # aplicó el cupón que dio
        ([], "Esta semana tenemos 10% de descuento en velas aromáticas"),
        ([], "¡Claro! Con el cupón BIENVENIDA te queda en $40.000"),
        ([], "Ahora mismo no tenemos promociones activas"),
    ],
)
def test_with_ventas_2_an_answered_promotions_question_is_attended(tools_used: list[str], shown: str) -> None:
    _need_v2()
    topics = [PlanTopic("promocion", 1, 0.9)]

    assert uncovered_topics(topics, _rules(V1), tools_used=tools_used, shown=shown) == topics
    assert uncovered_topics(topics, _rules(V2), tools_used=tools_used, shown=shown) == []


def test_with_ventas_2_an_unanswered_promotions_question_still_asks_for_a_round() -> None:
    _need_v2()
    topics = [PlanTopic("promocion", 1, 0.9)]

    missing = uncovered_topics(topics, _rules(V2), tools_used=["send_shipping_rates"], shown="El envío cuesta $12.000")

    assert missing == topics
