"""Alertas del scorecard (HU-SC-6): un episodio en FALLA abre un issue de
GitHub, deduplicado por la huella de sus checks críticos. Sin token → no-op."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard import alerts


class FakeTracker:
    def __init__(self, existing: dict[str, int] | None = None) -> None:
        self.existing = dict(existing or {})
        self.created: list[tuple[str, str, list[str]]] = []
        self.comments: list[tuple[int, str]] = []

    async def find_open(self, fingerprint: str) -> int | None:
        return self.existing.get(fingerprint)

    async def create(self, title: str, body: str, labels: list[str]) -> int:
        self.created.append((title, body, labels))
        return 42

    async def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))


def _record(**over) -> dict:
    rec = {
        "session_id": "wa_573001234567", "episode_id": "ep_007", "verdict": "FALLA", "date": "2026-09-14",
        "first_critical": {"turn": 9, "check_id": "CON-01"},
        "results": [
            {"check_id": "TAG-01", "verdict": "falla", "level": "critico", "turn": 10, "evidence": "etiqueta sin sí"},
            {"check_id": "CON-01", "verdict": "falla", "level": "critico", "turn": 9,
             "evidence": "formulario sin confirmación; escribió al 3001234567 y a ana@correo.com"},
            {"check_id": "DES-01", "verdict": "falla", "level": "mayor", "turn": 4, "evidence": "tres preguntas"},
        ],
    }
    rec.update(over)
    return rec


def test_fingerprint_depends_only_on_the_set_of_critical_checks() -> None:
    a = alerts.fingerprint(_record())
    b = alerts.fingerprint(_record(session_id="wa_100000000009",
                                   results=list(reversed(_record()["results"]))))
    c = alerts.fingerprint(_record(results=_record()["results"][:1]))

    assert a == b
    assert a != c


async def test_new_failure_mode_opens_a_redacted_issue() -> None:
    tracker = FakeTracker()

    assert await alerts.notify_failure(_record(), tracker=tracker) is True

    title, body, labels = tracker.created[0]
    assert "CON-01" in title and "TAG-01" in title
    assert alerts.LABEL in labels
    assert alerts.fingerprint(_record()) in body
    assert "3001234567" not in body and "573001234567" not in body and "ana@correo.com" not in body
    assert "ep_007" in body and "4567" in body  # episodio + últimos 4 dígitos para ubicarlo


async def test_known_failure_mode_comments_instead_of_duplicating() -> None:
    fp = alerts.fingerprint(_record())
    tracker = FakeTracker(existing={fp: 7})

    assert await alerts.notify_failure(_record(), tracker=tracker) is True

    assert tracker.created == []
    assert tracker.comments and tracker.comments[0][0] == 7
    assert "2026-09-14" in tracker.comments[0][1]


async def test_without_configuration_it_is_a_no_op(monkeypatch) -> None:
    monkeypatch.delenv("SCORECARD_ALERTS_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("SCORECARD_ALERTS_REPO", raising=False)

    assert alerts.tracker_from_env() is None
    assert await alerts.notify_failure(_record()) is False


async def test_non_failing_verdicts_never_alert() -> None:
    tracker = FakeTracker()

    assert await alerts.notify_failure(_record(verdict="ALERTA"), tracker=tracker) is False
    assert tracker.created == []


async def test_github_tracker_finds_the_open_issue_by_marker_without_the_search_index() -> None:
    """La búsqueda de GitHub indexa con retraso y limita a 30/min: dos FALLA
    seguidos abrían issues duplicados. Se lista por etiqueta (consistente)."""
    import httpx

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[
            {"number": 11, "body": "otro <!-- scorecard-fingerprint:aaaaaaaaaaaa -->"},
            {"number": 12, "body": "<!-- scorecard-fingerprint:deadbeef1234 -->", "pull_request": {"url": "pr"}},
            {"number": 13, "body": "cuerpo\n<!-- scorecard-fingerprint:deadbeef1234 -->"},
        ])

    tracker = alerts.GithubIssueTracker("acme/repo", "tok", transport=httpx.MockTransport(handler))

    assert await tracker.find_open("deadbeef1234") == 13
    assert await tracker.find_open("000000000000") is None
    req = seen[0]
    assert req.url.path == "/repos/acme/repo/issues"
    assert (req.url.params["labels"], req.url.params["state"]) == (alerts.LABEL, "open")
    assert req.headers["Authorization"] == "Bearer tok"
