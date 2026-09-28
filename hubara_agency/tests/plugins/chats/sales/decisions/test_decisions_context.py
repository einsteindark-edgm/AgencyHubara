"""Contexto del turno para Jev (diseño v2 §01, fase F1). PURO.

Jev contestaba a ciegas: solo veía la ráfaga. Un «Si» o un «Ok» no dicen nada
sin lo que el cliente vio antes. El motor le da a Jev, en una sección
separada de los mensajes de este turno:

* la ventana de lo que vio el cliente, leída del historial del vault (textos
  del bot, notas de botones/tarjetas/formularios, mensajes del equipo), sin
  los mensajes de esta ráfaga, cortada a 8 eventos o ~1.800 caracteres, con
  los mensajes largos del bot cortados por el principio (la pregunta va al
  final);
* los hechos del pedido (etapa, ítems, ciudad; lo personal solo «dado» o
  «falta»);
* lo que el código YA sabe que se preguntó (tarjeta de confirmación o
  formulario): ahí no hace falta preguntarle a Jev.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.decisions.context import customer_window, order_facts, redact_terms_from_slots


def _user(text: str, wamid: str | None = None, **extra) -> dict:
    event = {"role": "user", "content": text}
    if wamid:
        event["wamid"] = wamid
    return {**event, **extra}


def _bot(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _card(kind: str, text: str) -> dict:
    return {"role": "assistant", "kind": "ui_component", "component_kind": kind, "content": text}


def test_the_window_is_what_the_customer_saw_before_this_turn() -> None:
    events = [
        _user("Quiero el dúo amarillo aries lavanda", "w1"),
        _bot("¡Listo! Me acuerdo perfecto de tu Duo Zodiacal Aries. ¿Te lo enviamos a la misma dirección?"),
        _user("Si", "w2"),
        _user("Y quiero una vela adicional", "w3"),
    ]

    window = customer_window(events, burst_wamids={"w2", "w3"})

    assert window.lines == (
        "[cliente] Quiero el dúo amarillo aries lavanda",
        "[asesor] ¡Listo! Me acuerdo perfecto de tu Duo Zodiacal Aries. ¿Te lo enviamos a la misma dirección?",
    )
    assert window.last_component is None and window.bot_asked_known is None


def test_without_ids_the_burst_is_what_came_after_the_last_bot_message() -> None:
    events = [_bot("¿Quieres que te muestre los aromas?"), _user("Ok")]

    assert customer_window(events, burst_wamids=set(), burst_size=1).lines == ("[asesor] ¿Quieres que te muestre los aromas?",)


def test_an_earlier_unanswered_message_stays_in_the_context() -> None:
    """En el laboratorio el historial ya viene cortado al inicio del turno: con
    los ids, lo anterior sin responder queda igual que en producción."""
    events = [_bot("Hola 👋"), _user("¿tienen velas de soya?", "w1")]

    assert customer_window(events, burst_wamids={"w9"}).lines == ("[asesor] Hola 👋", "[cliente] ¿tienen velas de soya?")


def test_cards_buttons_and_the_team_are_part_of_what_the_customer_saw() -> None:
    events = [
        {"role": "assistant", "sender": "human", "content": "Hola, soy Laura del equipo"},
        _card("quick_replies", "🔘 El bot envió botones: Sí, lo quiero · Cambiar algo — con el mensaje: «Total $58.000»"),
        _user("Si", "w2"),
    ]

    window = customer_window(events, burst_wamids={"w2"})

    assert window.lines == (
        "[equipo] Hola, soy Laura del equipo",
        "[asesor] 🔘 El bot envió botones: Sí, lo quiero · Cambiar algo — con el mensaje: «Total $58.000»",
    )
    assert window.last_component == "quick_replies"


def test_the_code_already_knows_what_a_confirmation_card_or_the_form_asked() -> None:
    card = customer_window([_card("order_confirmation", "🧾 El bot envió el resumen del pedido con botones para confirmar"),
                            _user("Si", "w1")], burst_wamids={"w1"})
    form = customer_window([_card("shipping_flow", "📋 El bot pidió los datos de envío (formulario)"),
                            _user("ya", "w1")], burst_wamids={"w1"})
    text = customer_window([_bot("¿Te lo enviamos a la misma dirección?"), _user("Si", "w1")], burst_wamids={"w1"})

    assert card.bot_asked_known == "confirmar_compra"
    assert form.bot_asked_known == "confirmar_dato_envio"
    assert text.bot_asked_known is None


def test_a_long_bot_message_is_cut_from_the_start_to_keep_its_question() -> None:
    long = "Te cuento de nuestras velas. " * 60 + "¿Cuál te gusta más?"
    window = customer_window([_bot(long), _user("la roja", "w1")], burst_wamids={"w1"})

    [line] = window.lines
    assert line.startswith("[asesor] …") and line.endswith("¿Cuál te gusta más?")
    assert len(line) <= 520


def test_the_window_keeps_the_last_eight_events_and_about_1800_characters() -> None:
    events = [_bot(f"mensaje {i} " + "x" * 300) for i in range(20)] + [_user("hola", "w1")]

    window = customer_window(events, burst_wamids={"w1"})

    assert 1 <= len(window.lines) <= 8
    assert sum(len(line) for line in window.lines) <= 1800
    assert window.lines[-1].startswith("[asesor] mensaje 19")


def test_a_quoted_message_travels_with_the_turn() -> None:
    events = [_bot("¿Cuál prefieres?"), _user("Esta", "w1", reply_to={"id": "wx", "text": "Vela Cubo Love — $21.000"})]

    window = customer_window(events, burst_wamids={"w1"})

    assert window.quoted == "Vela Cubo Love — $21.000"


def test_order_facts_show_the_items_and_only_given_or_missing_for_personal_data() -> None:
    metadata = {
        "episodes": [
            {
                "episode_id": "ep_1",
                "closed_at_ms": None,
                "order_draft": {
                    "slots": {"producto": "Duo Zodiacal", "ciudad": "Medellín", "direccion": "Cra 7 # 12-34",
                              "telefono": "3001234567", "nombre_recibe": "Carolina Pérez"},
                    "items": [{"producto": "Duo Zodiacal Aries", "color": "amarillo", "aroma": "lavanda", "cantidad": 1}],
                },
            }
        ]
    }

    facts = order_facts(metadata, stage="etapa_datos_envio")

    assert facts == (
        "Etapa: datos de envío",
        "Ítem 1: Duo Zodiacal Aries, amarillo, lavanda, 1 unidad",
        "Ciudad: Medellín",
        "Dirección: dada · Teléfono: dado · Quien recibe: dado · Método de pago: falta",
        "Compra confirmada: no",
    )
    assert not any(secret in " ".join(facts) for secret in ("Cra 7", "3001234567", "Carolina"))


def test_without_a_draft_the_facts_are_only_the_stage() -> None:
    assert order_facts({}, stage="etapa_descubrimiento") == ("Etapa: descubrimiento",)


def test_what_is_hidden_from_jev_comes_from_the_personal_slots_of_the_draft() -> None:
    """Lo que se tapa de ESTE cliente antes de que el turno salga hacia Jev
    (lo usan la activity y el banco de referencia): las casillas personales
    completas y el nombre de quien recibe también palabra por palabra (el
    asesor lo repite solo: «Listo Carolina»). La ciudad y el producto no se
    tapan: Jev los necesita para entender el asunto."""
    slots = {
        "nombre_recibe": "Carolina Pérez",
        "direccion": "Cra 7 # 12-34",
        "barrio": "Chapinero Alto",
        "telefono": "3001234567",
        "ciudad": "Bogotá",
        "producto": "Duo Zodiacal",
    }

    assert redact_terms_from_slots(slots) == sorted(
        {"Carolina Pérez", "Carolina", "Pérez", "Cra 7 # 12-34", "Chapinero Alto", "3001234567"}
    )
    assert redact_terms_from_slots({"nombre_recibe": "  ", "direccion": None}) == []
    assert redact_terms_from_slots({}) == []
