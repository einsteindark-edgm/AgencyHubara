"""Checks de juez (HU-SC-3): una llamada aislada por check, binaria, con
evidencia y crítica; dos muestras que deben coincidir."""
from __future__ import annotations

import json

from src.plugins.chats.agent.sales_eval.scorecard import judge_checks as jc
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, traj
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix


class FakeJudge:
    def __init__(self, answers: dict[str, list[str]] | None = None, default: str | None = None) -> None:
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.default = default or json.dumps({"veredicto": "pasa", "turno": None, "evidencia": "", "critica": "ok"})
        self.prompts: list[str] = []

    async def a_generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        check_id = prompt.split("CRITERIO ", 1)[1].split(" ", 1)[0]
        queue = self.answers.get(check_id)
        return queue.pop(0) if queue else self.default


def _ans(verdict: str, turn=None, evidence="", critique="") -> str:
    return json.dumps({"veredicto": verdict, "turno": turn, "evidencia": evidence, "critica": critique})


def test_transcript_shows_what_the_old_judge_could_not_see() -> None:
    text = jc.render_transcript(pr281_before_fix())

    t9_tools = next(line for line in text.splitlines() if line.startswith("T9 · tools:"))
    assert "set_order_slot(ok)" in t9_tools and "request_shipping_details(ok)" in t9_tools
    assert "T9 · narración descartada:" in text
    assert "T9 · señal del cliente: aplazamiento" in text
    assert "T10 · estado: HUMANO" in text


def test_prompt_is_isolated_per_check_and_distrusts_system_state() -> None:
    prompt = jc.build_prompt("CON-04", pr281_before_fix(), CATALOG_CTX)

    assert "CRITERIO CON-04" in prompt
    assert "CRITERIO DES-04" not in prompt
    assert "no es prueba" in prompt
    assert '"veredicto"' in prompt


def test_catalog_summary_only_goes_to_the_hallucination_check() -> None:
    ctx = CheckContext(catalog_available=True, catalog_summary="• Cubo Love — $89000 COP")

    assert "Cubo Love — $89000" in jc.build_prompt("DES-06", pr281_before_fix(), ctx)
    assert "Cubo Love — $89000" not in jc.build_prompt("EST-04", pr281_before_fix(), ctx)


async def test_two_agreeing_samples_give_the_verdict_with_turn_and_critique() -> None:
    judge = FakeJudge({"CON-04": [_ans("falla", 9, "Voy apenas en camino", "no pidió el sí"),
                                  _ans("falla", 9, "Voy apenas en camino", "no pidió el sí")]})

    results = {r.check_id: r for r in await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge)}

    r = results["CON-04"]
    assert (r.verdict, r.turn, r.source) == ("falla", 9, "judge")
    assert r.critique == "no pidió el sí"


async def test_disagreeing_samples_are_unknown_not_a_coin_flip() -> None:
    judge = FakeJudge({"EST-07": [_ans("pasa"), _ans("falla", 3)]})

    results = {r.check_id: r for r in await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge)}

    assert results["EST-07"].verdict == "desconocido"
    assert "inconsistente" in results["EST-07"].critique


async def test_unparseable_answer_is_unknown() -> None:
    judge = FakeJudge({"EST-04": ["no sé", "tampoco"]})

    results = {r.check_id: r for r in await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge)}

    assert results["EST-04"].verdict == "desconocido"


async def test_prefilter_marks_not_applicable_without_calling_the_judge() -> None:
    judge = FakeJudge()
    t = traj(T(1, inbound="hola", sent=["¡Buenos días! Bienvenido a *Hubara*"]))

    results = {r.check_id: r for r in await jc.run_judge_checks(t, CheckContext(), judge)}

    for cid in ("POS-02", "TAG-04", "TAG-07", "CON-04", "ENV-06"):
        assert results[cid].verdict == "no_aplica", cid
    called = {p.split("CRITERIO ", 1)[1].split(" ", 1)[0] for p in judge.prompts}
    assert not called & {"POS-02", "TAG-04", "TAG-07", "CON-04", "ENV-06"}
    # Sin catálogo, la alucinación no se puede juzgar.
    assert results["DES-06"].verdict == "desconocido"


async def test_only_parameter_limits_the_checks() -> None:
    judge = FakeJudge()

    results = await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge, only={"EST-04"})

    assert [r.check_id for r in results] == ["EST-04"]


class RateLimitedJudge(FakeJudge):
    """Lanza el 429 de Gemini las primeras `fails` llamadas y después responde."""

    def __init__(self, fails: int, message: str = "litellm.RateLimitError: Error code: 429 - You exceeded your current quota") -> None:
        super().__init__()
        self.fails = fails
        self.message = message

    async def a_generate(self, prompt: str) -> str:
        if self.fails > 0:
            self.fails -= 1
            raise RuntimeError(self.message)
        return await super().a_generate(prompt)


async def test_rate_limited_judge_is_retried_with_backoff() -> None:
    """Primer informe: 71 de 76 llamadas cayeron en 429 por ráfaga y el juez
    quedó `desconocido` en todo. Un límite por minuto se reintenta con espera."""
    judge = RateLimitedJudge(fails=2)
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    results = await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge, only={"EST-04"}, sleep=sleep)

    assert results[0].verdict == "pasa"
    assert len(waits) == 2 and waits[0] < waits[1]


async def test_quota_error_disguised_as_403_is_also_retried() -> None:
    judge = RateLimitedJudge(fails=1, message='litellm.BadRequestError: Error code: 403 - {"code": 429, "status": "RESOURCE_EXHAUSTED"}')

    async def sleep(_s: float) -> None:
        return None

    results = await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge, only={"EST-04"}, sleep=sleep)

    assert results[0].verdict == "pasa"


async def test_judge_still_rate_limited_after_retries_is_unknown_with_error() -> None:
    judge = RateLimitedJudge(fails=99)

    async def sleep(_s: float) -> None:
        return None

    results = await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge, only={"EST-04"}, sleep=sleep)

    assert results[0].verdict == "desconocido"
    assert results[0].critique.startswith("error del juez")


async def test_non_rate_limit_errors_are_not_retried() -> None:
    judge = RateLimitedJudge(fails=1, message="litellm.AuthenticationError: invalid api key")
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    results = await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, judge, only={"EST-04"}, sleep=sleep)

    assert results[0].verdict == "desconocido" and waits == []
