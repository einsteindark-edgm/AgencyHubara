"""La nota con que el turno cita lo que escribió un colega desde el chat.

Caso del 2026-10-09: el bot dijo que no había descuento y ofreció mostrar otra
línea; un colega tomó el chat, le escribió al cliente que sí le aplicaban el
de la página y devolvió el chat al bot. El cliente contestó «Si por favor» y
el bot, que no tenía ese mensaje en su historial, le mandó la línea que él
mismo había ofrecido.

Formato y lectura en un solo lugar: la arma Ventas
(`episode_memory.unseen_team_exchange` + `quote_team_exchange_in_turn`) y la
lee la calificación (`our_lines_in_turn`: lo que dijo el colega es nuestro,
como la cita de una plantilla). Tuteo colombiano (REGLA #1).
"""
from __future__ import annotations

#: Quién escribió cada línea de la nota.
TEAM_SPEAKERS = {"colega": "Tu colega", "cliente": "Cliente", "aviso": "Mensaje automático"}

TEAM_NOTE_HEAD = "[Después de tu último mensaje, un colega del equipo"
#: Las líneas de la nota que dijimos nosotros (el colega o un aviso).
_OUR_LINE_PREFIXES = tuple(f"- {TEAM_SPEAKERS[who]}: «" for who in ("colega", "aviso"))


def quote_team_exchange_in_turn(exchange: list[tuple[str, str]], text: str) -> str:
    """El mensaje del cliente con lo que escribió el colega citado adelante.

    Sin corchetes adentro: la nota va entre corchetes y la calificación quita
    las notas del ingest hasta el primer «]» (`_INGEST_NOTE_RE`)."""
    lines = "\n".join(
        f"- {TEAM_SPEAKERS.get(who, who)}: «{said.replace('[', '(').replace(']', ')')}»"
        for who, said in exchange
    )
    return (
        f"{TEAM_NOTE_HEAD} le escribió al cliente "
        "en este chat (el cliente lo ve como la misma conversación):\n"
        f"{lines}\n"
        "Lo que el cliente escribe ahora puede ser la respuesta a tu colega: sigue "
        "desde ahí y no contradigas lo que le dijo.]\n"
        f"{text}"
    )


def our_lines_in_turn(text: str) -> list[str]:
    """Lo que dijimos nosotros (el colega o un aviso) según la nota que puso
    `quote_team_exchange_in_turn` en el turno; ``[]`` si el turno no la trae."""
    start = text.find(TEAM_NOTE_HEAD)
    if start < 0:
        return []
    end = text.find("]", start)
    note = text[start : end if end >= 0 else len(text)]
    return [
        line[len(prefix) : -1]
        for line in note.splitlines()
        for prefix in _OUR_LINE_PREFIXES
        if line.startswith(prefix) and line.endswith("»")
    ]
