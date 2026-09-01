"""Adapter filesystem del log append-only de mensajes por sesion.

Persiste tres shapes de evento en ``<vault_dir>/<session_id>/sessions/<session_id>.jsonl``:

  * ``append_user_event(session_id, content)`` →
    ``{"role": "user", "content": ...}``
  * ``append_assistant_event(session_id, content, tool_calls=None)`` →
    ``{"role": "assistant", "content": ..., "timestamp": "<ISO>"}``
    (``tool_calls`` se incluye solo si no es None ni vacio)
  * ``append_human_event(session_id, content)`` →
    ``{"role": "assistant", "sender": "human", "content": ..., "timestamp": "<ISO>"}``
    (mensaje del humano operador via dashboard handoff; rol assistant para que
    el LLM lo vea como historial natural al retomar el chat; campo ``sender``
    extra para que el dashboard pinte la burbuja distinto y para trazabilidad).

Todos serializan con ``ensure_ascii=False`` para preservar caracteres no-ASCII.

El clasificador del dashboard (``api.py::get_session_detail``) deriva el
campo ``ui_type`` a partir de ``role``, ``sender`` y ``tool_calls`` — no es
necesario persistirlo aca.

Vive en ``src.platform.session_history`` porque tanto ``sales_whatsapp`` como
``remarketing_whatsapp`` (y el dashboard handoff) lo necesitan (R-DIP #10).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class FilesystemMessageHistoryStore:
    """Adapter filesystem del log append-only por sesion."""

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path_for(self, session_id: str) -> Path:
        return self._vault_dir / session_id / "sessions" / f"{session_id}.jsonl"

    def _append(self, session_id: str, event: dict[str, Any]) -> None:
        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_user_event(
        self,
        session_id: str,
        content: str,
        *,
        image_url: str | None = None,
        document_url: str | None = None,
        document_filename: str | None = None,
    ) -> None:
        """Persiste un inbound del cliente con timestamp ISO UTC.

        HU-WA24H-001 F1.2: simetria con `append_assistant_event` —
        downstream usa el timestamp para tracking de service window 24h
        + métricas de tiempo de respuesta del agente.

        ``image_url``: ref relativa a una imagen inbound ya persistida en el
        media store (ver ``platform/media``). Solo lo pobla el reentry de
        visión del ingest; los inbounds de texto lo dejan en None. Cuando
        está presente, el dashboard renderiza la foto en la burbuja (clave
        para comprobantes de pago que el humano debe ver, no solo leer la
        descripción que generó la visión).

        ``document_url`` + ``document_filename``: ref a un documento PDF
        inbound persistido (comprobante de pago típico) + su nombre visible.
        El dashboard pinta un chip clickeable en la burbuja. Ausentes en el
        resto de los inbounds — no se persisten nulls.
        """
        event: dict[str, Any] = {
            "role": "user",
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if image_url:
            event["image_url"] = image_url
        if document_url:
            event["document_url"] = document_url
        if document_filename:
            event["document_filename"] = document_filename
        self._append(session_id, event)

    def append_assistant_event(
        self,
        session_id: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tools_used: list[str] | None = None,
    ) -> None:
        """``tools_used``: NOMBRES de las tools que el agente ejecutó durante el
        turno que culminó en este mensaje. Campo distinto de ``tool_calls`` a
        propósito: el clasificador del dashboard proyecta ``tool_calls`` →
        ``ui_type: agent_tool_call`` (burbuja distinta), mientras que
        ``tools_used`` es metadata de auditoría que el dashboard ignora y el
        eval (reconstruct.to_evaluable_turns) le pasa al juez — sin esto el
        juez no ve que el turno llamó search_products/escalate_to_human y
        falla con falsos negativos (caso ep_010: correct_handoff=0.5 con la
        escalada efectivamente hecha)."""
        event: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if tool_calls:
            event["tool_calls"] = tool_calls
        if tools_used:
            event["tools_used"] = list(tools_used)
        self._append(session_id, event)

    def append_human_event(
        self,
        session_id: str,
        content: str,
        *,
        image_url: str | None = None,
        document_url: str | None = None,
        document_filename: str | None = None,
    ) -> None:
        """Mensaje del humano operador via dashboard handoff.

        Rol ``assistant`` (no rol nuevo): cuando el bot retome el chat,
        ``build_prompt`` lo verá como parte del historial assistant natural,
        sin que la API de Anthropic se rompa por un rol desconocido. El campo
        extra ``sender: "human"`` queda persistido para que (1) el clasificador
        del dashboard lo proyecte como ``ui_type: human_message`` y pinte una
        burbuja distinta, y (2) quede traza histórica de qué fue agente vs
        humano cuando un analista revise el JSONL.

        ``image_url``: ref relativa a una foto que el operador mandó al cliente,
        ya persistida en el media store outbound (``persist_outbound_image``).
        Simétrico al ``image_url`` de ``append_user_event`` (inbound). Cuando
        está presente el dashboard re-renderiza la foto en la burbuja saliente;
        el texto (``content``) queda como caption. Ausente en mensajes de solo
        texto — no se persiste el campo para no ensuciar el JSONL con nulls.

        ``document_url`` + ``document_filename``: lo mismo para un documento
        PDF saliente (comprobante) — el dashboard pinta un chip clickeable con
        el nombre del archivo. Ausentes salvo envío de documento.
        """
        event: dict[str, Any] = {
            "role": "assistant",
            "sender": "human",
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if image_url:
            event["image_url"] = image_url
        if document_url:
            event["document_url"] = document_url
        if document_filename:
            event["document_filename"] = document_filename
        self._append(session_id, event)
