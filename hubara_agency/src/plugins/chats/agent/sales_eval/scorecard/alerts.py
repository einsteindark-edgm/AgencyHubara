"""Alertas del scorecard (HU-SC-6) — un episodio en FALLA abre un issue.

Pieza 3 del `GOLDEN_EVAL_LOOP_PLAN.md`: la alerta ES el issue de GitHub.

  * **Dedup por modo de fallo**: la huella es el conjunto de checks críticos
    que fallaron. Si ya hay un issue abierto con esa huella, se comenta "volvió
    a pasar" en vez de abrir otro. El issue agrupa todos los episodios con el
    mismo modo de fallo (el Pareto, en GitHub).
  * **PII**: el cuerpo lleva el episodio y los últimos 4 dígitos de la sesión
    (para ubicarlo en el dashboard), nunca el teléfono completo; la evidencia
    pasa por `redact_pii` (teléfonos, emails, documentos).
  * **Sin configuración, no-op**: sin `SCORECARD_ALERTS_GITHUB_TOKEN` y
    `SCORECARD_ALERTS_REPO` (`owner/repo`) no se hace nada.

R-DIP: `IssueTrackerPort` (protocolo) + adapter GitHub por httpx.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Protocol

from src.plugins.chats.agent.sales_eval.evals.redaction import redact_pii
from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID

LABEL = "llm-scorecard"
_GITHUB_API = "https://api.github.com"
_TIMEOUT_S = 10.0
_MARKER = "scorecard-fingerprint"


class IssueTrackerPort(Protocol):
    async def find_open(self, fingerprint: str) -> int | None: ...

    async def create(self, title: str, body: str, labels: list[str]) -> int: ...

    async def comment(self, number: int, body: str) -> None: ...


def _critical_ids(record: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(r.get("check_id"))
            for r in record.get("results") or []
            if isinstance(r, dict) and r.get("verdict") == "falla" and r.get("level") == "critico"
        }
    )


def fingerprint(record: dict[str, Any]) -> str:
    ids = _critical_ids(record) or [str(record.get("verdict"))]
    return hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:12]


def _episode_ref(record: dict[str, Any]) -> str:
    session = str(record.get("session_id") or "")
    tail = session[-4:] if len(session) >= 4 else session
    return f"sesión …{tail} · {record.get('episode_id') or '?'} · {record.get('date') or ''}".strip()


def render_issue(record: dict[str, Any]) -> tuple[str, str]:
    ids = _critical_ids(record)
    fp = fingerprint(record)
    title = f"[scorecard] FALLA crítica: {', '.join(ids) or record.get('verdict')}"
    rows = []
    for r in record.get("results") or []:
        if not isinstance(r, dict) or r.get("verdict") != "falla":
            continue
        spec = SPECS_BY_ID.get(str(r.get("check_id")))
        evidence = redact_pii(str(r.get("evidence") or "")).replace("|", "/")
        rows.append(
            f"| {r.get('check_id')} | {spec.name if spec else ''} | {r.get('level')} | "
            f"{r.get('turn') if r.get('turn') is not None else '—'} | {evidence} |"
        )
    body = "\n".join(
        [
            "Un episodio del asesor de ventas reprobó el scorecard por etapa.",
            "",
            f"**Primer episodio:** {_episode_ref(record)}",
            f"**Primer crítico:** {(record.get('first_critical') or {}).get('check_id', '—')}",
            "",
            "| Check | Nombre | Nivel | Turno | Evidencia |",
            "|---|---|---|---|---|",
            *rows,
            "",
            "Ábrelo en el dashboard: Calidad LLM → Conversaciones, filtro FALLA en esa fecha.",
            "Cada nuevo episodio con el mismo modo de fallo se agrega como comentario.",
            "",
            f"<!-- {_MARKER}:{fp} -->",
        ]
    )
    return title, body


async def notify_failure(
    record: dict[str, Any], *, tracker: IssueTrackerPort | None = None
) -> bool:
    """Abre o comenta el issue del modo de fallo. True si notificó."""
    if record.get("verdict") != "FALLA":
        return False
    tracker = tracker if tracker is not None else tracker_from_env()
    if tracker is None:
        return False
    fp = fingerprint(record)
    existing = await tracker.find_open(fp)
    if existing is not None:
        await tracker.comment(existing, f"Volvió a pasar: {_episode_ref(record)}.")
        return True
    title, body = render_issue(record)
    await tracker.create(title, body, [LABEL])
    return True


class GithubIssueTracker:
    """Adapter GitHub REST (issues + search) con token de repo."""

    def __init__(self, repo: str, token: str) -> None:
        self._repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def find_open(self, fingerprint: str) -> int | None:
        import httpx

        query = f'repo:{self._repo} is:issue is:open label:{LABEL} "{_MARKER}:{fingerprint}" in:body'
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(f"{_GITHUB_API}/search/issues", params={"q": query}, headers=self._headers)
            resp.raise_for_status()
            items = resp.json().get("items") or []
        return int(items[0]["number"]) if items else None

    async def create(self, title: str, body: str, labels: list[str]) -> int:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                f"{_GITHUB_API}/repos/{self._repo}/issues",
                json={"title": title, "body": body, "labels": labels},
                headers=self._headers,
            )
            resp.raise_for_status()
            return int(resp.json()["number"])

    async def comment(self, number: int, body: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                f"{_GITHUB_API}/repos/{self._repo}/issues/{number}/comments",
                json={"body": body},
                headers=self._headers,
            )
            resp.raise_for_status()


def tracker_from_env() -> IssueTrackerPort | None:
    token = os.getenv("SCORECARD_ALERTS_GITHUB_TOKEN", "").strip()
    repo = os.getenv("SCORECARD_ALERTS_REPO", "").strip()
    if not token or not repo or "/" not in repo:
        return None
    return GithubIssueTracker(repo, token)
