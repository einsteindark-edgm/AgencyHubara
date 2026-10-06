"""`merge_changes(base, ours, fresh)` — el merge de tres vías de `metadata.json`.

Incidente 2026-10-06 (conversación de prueba): el turno 4 mandó la foto del
producto y el flush la sacó de `pending_ui_intents`; un escritor que había
leído ANTES (el ingest esperando a Jev, el aviso de entrega) escribió su
copia entera DESPUÉS y la foto volvió a la cola: el turno 5 la mandó otra vez
y el `outbound_media_index` perdió la entrega del turno 4.

`merge_changes` es la pieza pura que lo evita: el escritor solo lleva lo que
ÉL cambió (``ours`` frente a ``base``, lo que leyó) sobre lo que hay en disco
AHORA (``fresh``):

  * subárbol que el escritor no tocó (``ours == base``) → gana ``fresh``;
  * subárbol que solo tocó el escritor (``fresh == base``) → gana ``ours``;
  * dicts → llave por llave (borrar también es un cambio);
  * listas de dicts con identidad (``id``, ``wa_message_id``, ``event_id``,
    ``media_id``, ``episode_id``) → elemento por elemento;
  * listas sin identidad a las que ambos solo agregaron al final → las dos colas;
  * conflicto en una hoja → gana el escritor.
"""
from __future__ import annotations

import copy

from src.platform.state import merge_changes


def _merge(base, ours, fresh):
    """Corre el merge sobre copias y verifica que no muta sus entradas."""
    snapshot = copy.deepcopy((base, ours, fresh))
    result = merge_changes(base, ours, fresh)
    assert (base, ours, fresh) == snapshot, "merge_changes no debe mutar sus entradas"
    return result


# --- reglas base ----------------------------------------------------------


def test_untouched_by_the_writer_keeps_what_is_on_disk() -> None:
    base = {"tag": "INTERESADO", "n": 1}
    fresh = {"tag": "HUMANO", "n": 1, "active_route": "humano"}

    assert _merge(base, copy.deepcopy(base), fresh) == fresh


def test_changed_only_by_the_writer_wins() -> None:
    base = {"tag": "INTERESADO"}
    ours = {"tag": "RECHAZO"}

    assert _merge(base, ours, copy.deepcopy(base)) == ours


def test_each_writer_keeps_its_own_keys() -> None:
    base = {"a": 1, "b": 1}
    ours = {"a": 2, "b": 1}
    fresh = {"a": 1, "b": 3}

    assert _merge(base, ours, fresh) == {"a": 2, "b": 3}


def test_new_keys_from_both_sides_survive() -> None:
    base = {"a": 1}
    ours = {"a": 1, "mine": True}
    fresh = {"a": 1, "theirs": True}

    assert _merge(base, ours, fresh) == {"a": 1, "theirs": True, "mine": True}


def test_conflict_on_a_leaf_the_writer_wins() -> None:
    base = {"tag": "INTERESADO"}
    ours = {"tag": "RECHAZO"}
    fresh = {"tag": "HUMANO"}

    assert _merge(base, ours, fresh) == {"tag": "RECHAZO"}


def test_same_change_on_both_sides_is_not_a_conflict() -> None:
    base = {"tag": "INTERESADO"}
    ours = {"tag": "HUMANO"}
    fresh = {"tag": "HUMANO"}

    assert _merge(base, ours, fresh) == {"tag": "HUMANO"}


def test_type_change_is_a_leaf_conflict_and_the_writer_wins() -> None:
    base = {"x": {"a": 1}}
    ours = {"x": [1, 2]}
    fresh = {"x": {"a": 2}}

    assert _merge(base, ours, fresh) == {"x": [1, 2]}


# --- borrar llaves --------------------------------------------------------


def test_key_deleted_by_the_writer_stays_deleted() -> None:
    base = {"pending_transcription": {"media_id": "m1"}, "n": 1}
    ours = {"n": 1}
    fresh = {"pending_transcription": {"media_id": "m1"}, "n": 2}

    assert _merge(base, ours, fresh) == {"n": 2}


def test_key_deleted_on_disk_stays_deleted_if_the_writer_did_not_touch_it() -> None:
    base = {"pending_handoff_summary": "resumen", "n": 1}
    ours = {"pending_handoff_summary": "resumen", "n": 2}
    fresh = {"n": 1}

    assert _merge(base, ours, fresh) == {"n": 2}


def test_writer_edit_beats_a_deletion_on_disk() -> None:
    base = {"flag": 1}
    ours = {"flag": 2}
    fresh: dict = {}

    assert _merge(base, ours, fresh) == {"flag": 2}


def test_writer_deletion_beats_an_edit_on_disk() -> None:
    base = {"shipping_flow_awaiting_reply_since_ms": 1}
    ours: dict = {}
    fresh = {"shipping_flow_awaiting_reply_since_ms": 2}

    assert _merge(base, ours, fresh) == {}


def test_key_deleted_on_both_sides() -> None:
    base = {"gone": 1, "n": 1}
    ours = {"n": 1}
    fresh = {"n": 1}

    assert _merge(base, ours, fresh) == {"n": 1}


# --- dicts anidados ---------------------------------------------------------


def test_nested_dicts_merge_key_by_key() -> None:
    base = {"watchdog": {"fired_at_ms": None, "cancelled_at_ms": None}}
    ours = {"watchdog": {"fired_at_ms": 10, "cancelled_at_ms": None}}
    fresh = {"watchdog": {"fired_at_ms": None, "cancelled_at_ms": None, "last_fire_wa_message_id": "w"}}

    assert _merge(base, ours, fresh) == {
        "watchdog": {"fired_at_ms": 10, "cancelled_at_ms": None, "last_fire_wa_message_id": "w"}
    }


def test_dict_added_on_both_sides_merges_its_keys() -> None:
    base: dict = {}
    ours = {"web_cart": {"cart_id": "c1"}}
    fresh = {"web_cart": {"status": "hydrated"}}

    assert _merge(base, ours, fresh) == {"web_cart": {"status": "hydrated", "cart_id": "c1"}}


def test_media_index_keeps_entries_from_both_writers() -> None:
    base = {"outbound_media_index": {"wamid.0": {"label": "a"}}}
    ours = {"outbound_media_index": {"wamid.0": {"label": "a"}, "wamid.2": {"label": "c"}}}
    fresh = {"outbound_media_index": {"wamid.0": {"label": "a"}, "wamid.1": {"label": "b"}}}

    assert _merge(base, ours, fresh) == {
        "outbound_media_index": {
            "wamid.0": {"label": "a"},
            "wamid.1": {"label": "b"},
            "wamid.2": {"label": "c"},
        }
    }


def test_dict_order_is_the_disk_order_then_the_writer_new_keys() -> None:
    base = {"b": 1, "a": 1}
    ours = {"b": 1, "a": 1, "z": 1}
    fresh = {"c": 1, "b": 2, "a": 1}

    assert list(_merge(base, ours, fresh)) == ["c", "b", "a", "z"]


# --- listas con identidad ---------------------------------------------------


def _intent(intent_id: str, kind: str = "product_detail") -> dict:
    return {"id": intent_id, "kind": kind, "params": {}, "queued_at_ms": 1}


def test_the_incident_popped_photo_does_not_come_back() -> None:
    """El ingest leyó con la foto en cola, esperó a Jev y escribe; mientras
    tanto el flush la sacó y anotó su entrega: la foto no vuelve y la entrega
    no se pierde."""
    photo = _intent("foto-t4")
    base = {"pending_ui_intents": [photo], "outbound_media_index": {}, "last_inbound_at_ms": 1}
    ours = {**copy.deepcopy(base), "last_inbound_at_ms": 2, "last_inbound_message_id": "wamid.in"}
    fresh = {"pending_ui_intents": [], "outbound_media_index": {"wamid.foto": {"handle": "h"}}, "last_inbound_at_ms": 1}

    assert _merge(base, ours, fresh) == {
        "pending_ui_intents": [],
        "outbound_media_index": {"wamid.foto": {"handle": "h"}},
        "last_inbound_at_ms": 2,
        "last_inbound_message_id": "wamid.in",
    }


def test_writer_pops_one_intent_while_another_is_queued() -> None:
    a, b = _intent("a"), _intent("b", "variant_picker")
    base = {"pending_ui_intents": [a]}
    ours = {"pending_ui_intents": []}
    fresh = {"pending_ui_intents": [a, b]}

    assert _merge(base, ours, fresh) == {"pending_ui_intents": [b]}


def test_both_sides_append_distinct_items_by_identity() -> None:
    a, b, c = _intent("a"), _intent("b"), _intent("c")
    base = {"pending_ui_intents": [a]}
    ours = {"pending_ui_intents": [a, c]}
    fresh = {"pending_ui_intents": [a, b]}

    assert _merge(base, ours, fresh) == {"pending_ui_intents": [a, b, c]}


def test_episodes_merge_by_episode_id_and_field() -> None:
    """El ingest abre el episodio 2 y cierra el 1; una tool escribió el
    borrador del pedido del 1 mientras tanto."""
    ep1 = {"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}
    base = {"episodes": [ep1]}
    ours = {
        "episodes": [
            {**ep1, "closed_at_ms": 5, "closing_tag": "TIMEOUT"},
            {"episode_id": "ep_002", "started_at_ms": 6, "closed_at_ms": None},
        ]
    }
    fresh = {"episodes": [{**ep1, "order_draft": {"slots": {"producto": "Cubo Love"}}}]}

    assert _merge(base, ours, fresh) == {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1,
                "closed_at_ms": 5,
                "order_draft": {"slots": {"producto": "Cubo Love"}},
                "closing_tag": "TIMEOUT",
            },
            {"episode_id": "ep_002", "started_at_ms": 6, "closed_at_ms": None},
        ]
    }


def test_outbound_messages_merge_by_wa_message_id() -> None:
    """El aviso de entrega le pone el precio a un mensaje mientras el envío
    agrega otro."""
    m1 = {"wa_message_id": "w1", "pricing": None}
    base = {"outbound": [m1]}
    ours = {"outbound": [m1, {"wa_message_id": "w2", "pricing": None}]}
    fresh = {"outbound": [{"wa_message_id": "w1", "pricing": {"category": "service"}}]}

    assert _merge(base, ours, fresh) == {
        "outbound": [
            {"wa_message_id": "w1", "pricing": {"category": "service"}},
            {"wa_message_id": "w2", "pricing": None},
        ]
    }


def test_item_deleted_on_disk_and_untouched_by_the_writer_stays_deleted() -> None:
    a, b = _intent("a"), _intent("b")
    base = {"pending_ui_intents": [a, b]}
    ours = {"pending_ui_intents": [a, b], "n": 1}
    fresh = {"pending_ui_intents": [b]}

    assert _merge(base, ours, fresh) == {"pending_ui_intents": [b], "n": 1}


def test_capi_outbox_merges_by_event_id() -> None:
    """El flusher de CAPI cierra un evento mientras una tool encola otro."""
    e1 = {"event_id": "viewcontent_1", "attempts": 0}
    e2 = {"event_id": "addtocart_1", "attempts": 0}
    base = {"capi_outbox": [e1], "capi_events_sent": []}
    ours = {"capi_outbox": [], "capi_events_sent": [{"event_id": "viewcontent_1", "status": "sent"}]}
    fresh = {"capi_outbox": [e1, e2], "capi_events_sent": []}

    assert _merge(base, ours, fresh) == {
        "capi_outbox": [e2],
        "capi_events_sent": [{"event_id": "viewcontent_1", "status": "sent"}],
    }


def test_most_specific_identity_wins_over_episode_id() -> None:
    """Las descripciones de fotos llevan `episode_id` (referencia) y
    `media_id` (identidad): dos fotos del mismo episodio no se confunden."""
    d1 = {"media_id": "m1", "episode_id": "ep_001"}
    base = {"recent_image_descriptions": [d1]}
    ours = {"recent_image_descriptions": [d1, {"media_id": "m2", "episode_id": "ep_002"}]}
    fresh = {"recent_image_descriptions": [d1, {"media_id": "m3", "episode_id": "ep_002"}]}

    merged = _merge(base, ours, fresh)["recent_image_descriptions"]
    assert [d["media_id"] for d in merged] == ["m1", "m3", "m2"]


def test_duplicate_identities_fall_back_to_append_merge() -> None:
    """`capi_events_sent` puede repetir `event_id` (un skip que se reencola):
    sin identidad confiable, se juntan las dos colas."""
    s1 = {"event_id": "e", "status": "skipped_no_config"}
    base = {"capi_events_sent": [s1]}
    ours = {"capi_events_sent": [s1, {"event_id": "e", "status": "sent"}]}
    fresh = {"capi_events_sent": [s1, {"event_id": "x", "status": "sent"}]}

    assert _merge(base, ours, fresh) == {
        "capi_events_sent": [s1, {"event_id": "x", "status": "sent"}, {"event_id": "e", "status": "sent"}]
    }


# --- listas sin identidad ---------------------------------------------------


def test_lists_both_appended_keep_both_tails() -> None:
    """`status_history` no tiene id: dos escritores que agregan una entrada
    cada uno no se pisan."""
    h0 = {"tag": "NO_ETIQUETADO", "timestamp": 1}
    base = {"status_history": [h0]}
    ours = {"status_history": [h0, {"tag": "HUMANO", "timestamp": 3}]}
    fresh = {"status_history": [h0, {"tag": "INTERESADO", "timestamp": 2}]}

    assert _merge(base, ours, fresh) == {
        "status_history": [h0, {"tag": "INTERESADO", "timestamp": 2}, {"tag": "HUMANO", "timestamp": 3}]
    }


def test_second_write_after_a_rebase_keeps_what_the_other_writer_appended() -> None:
    """Revisión del PR #393 (D2): el ingest escribe dos veces en un mismo
    `execute`. Tras la primera, su `base` es SU vista (sin la entrada que otro
    escritor agregó antes); en disco esa entrada quedó EN MEDIO. `base` ya no
    es prefijo de lo de disco, pero sí subsecuencia: no es un conflicto."""
    h0 = {"tag": "SIN_RESPUESTA", "timestamp": 1.0}
    other = {"tag": "INTERESADO", "timestamp": 2.0, "source": "otro_escritor"}
    returned = {"tag": "NO_ETIQUETADO", "timestamp": 3.0, "source": "ingest:customer_returned"}
    pdf = {"tag": "HUMANO", "timestamp": 4.0, "source": "ingest:pdf"}
    base = {"status_history": [h0, returned]}
    ours = {"status_history": [h0, returned, pdf]}
    fresh = {"status_history": [h0, other, returned]}

    assert _merge(base, ours, fresh) == {"status_history": [h0, other, returned, pdf]}


def test_base_missing_an_item_on_disk_is_still_a_conflict() -> None:
    """Si a lo de disco le falta algo de `base` (otro lo sacó), no es solo
    agregar: conflicto, gana el escritor."""
    base = {"q": [1, 2, 3]}
    ours = {"q": [1, 2, 3, 4]}
    fresh = {"q": [1, 3, 5]}

    assert _merge(base, ours, fresh) == {"q": [1, 2, 3, 4]}


def test_append_already_on_disk_is_not_duplicated() -> None:
    base = {"ctwa_clids_seen": ["c1"]}
    ours = {"ctwa_clids_seen": ["c1", "c2"]}
    fresh = {"ctwa_clids_seen": ["c1", "c2", "c3"]}

    assert _merge(base, ours, fresh) == {"ctwa_clids_seen": ["c1", "c2", "c3"]}


def test_list_rewritten_by_the_writer_wins_on_conflict() -> None:
    """Recortar o reordenar no es «agregar al final»: conflicto, gana el escritor."""
    base = {"recent": [1, 2, 3]}
    ours = {"recent": [2, 3, 4]}
    fresh = {"recent": [1, 2, 3, 5]}

    assert _merge(base, ours, fresh) == {"recent": [2, 3, 4]}


def test_list_created_on_both_sides_keeps_both() -> None:
    base: dict = {}
    ours = {"tags": ["a"]}
    fresh = {"tags": ["b"]}

    assert _merge(base, ours, fresh) == {"tags": ["b", "a"]}


# --- documento completo -----------------------------------------------------


def test_untouched_document_returns_the_disk_state_itself() -> None:
    base = {"a": {"b": [1]}}
    fresh = {"a": {"b": [1, 2]}}

    assert _merge(base, copy.deepcopy(base), fresh) is fresh


def test_first_write_of_a_new_session() -> None:
    ours = {"phone_number_id": "pnid", "episodes": [{"episode_id": "ep_001"}]}

    assert _merge({}, ours, {}) == ours
