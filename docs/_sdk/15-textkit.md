# 15 · TextKit (guards del texto LLM→cliente, para TOOLS)

> Fuente: `src/sdk/textkit.py` · Check: `tests/platform/test_textkit.py`
> (identidad + "solo depende del sanitizador puro")

## Qué problema soluciona

La regla de L-20: toda tool que termina la conversación lleva el texto del
cliente en un **param tipado** (`customer_message`) y corta el turno. Ese texto
lo redacta el LLM, así que la tool tiene que validarlo antes de declararlo.

Los guards ya existían y dos de ellos (`sanitize_llm_text`,
`looks_like_admin_leak`) ya salían por `src.sdk.agentkit` — pero ese kit trae el
turn loop (`workflow_helpers` → `temporalio`), y el contrato R-DIP prohíbe que
`tools/*.py` importe Temporal (ADR-001: la tool es inerte). Una tool de plugin
no tenía camino limpio hacia ellos: `src.platform` lo frena P-28, `agentkit` lo
frena `lint-imports`. Lo destapó `manage_conversation_tag` (run b06636a6).

TextKit es la fachada **libre de Temporal**: depende solo de
`src.platform.llm_text_sanitizer`, que es stdlib-only.

## Superficie

| Símbolo | Rol |
|---|---|
| `sanitize_llm_text` | quita meta-prefijos, comillas envolventes, duplicación back-to-back |
| `keep_customer_safe_sentences` | filtro POR ORACIÓN: se caen solo las que rompen la persona o huelen a parte interno; devuelve `""` si no sobrevive ninguna |
| `breaks_human_persona` | ¿el texto delata que no atiende una persona? ("humano", "bot", "IA", "automático"…) |
| `looks_like_admin_leak` | ¿huele a reporte administrativo interno? |

Son **los mismos objetos** de `src.platform.llm_text_sanitizer`; los que
`agentkit` también re-exporta son idénticos (workers y workflows siguen usando
ese kit). `keep_customer_safe_sentences` y `breaks_human_persona` salen SOLO por
acá: su lugar es la tool, no el workflow.

## Dónde se valida importa tanto como con qué (L-21)

Un regex cuyo veredicto decide commands DENTRO de un workflow es lógica de
replay: ampliarlo rompe las histories que ya tomaron la otra rama. La tool corre
en una **activity** y su resultado queda grabado en la history, así que:

- la tool valida y **declara** el resultado en su envelope;
- el workflow solo **lee** lo grabado (nada de regex en la decisión de cortar).

## Cómo se usa

```python
from src.sdk.textkit import keep_customer_safe_sentences, sanitize_llm_text

if customer_message.strip():
    closure["customer_message"] = keep_customer_safe_sentences(
        sanitize_llm_text(customer_message).text
    )
```

Tres estados que el loop distingue (contrato de `tag_closure`, L-22):

| Clave `customer_message` | Significado | El loop… |
|---|---|---|
| ausente | el modelo no mandó texto | no corta en turno de cliente (sigue esperando respuesta) |
| con texto | lo único que lee el cliente | corta y lo envía |
| `""` | habló y nada era seguro | corta en silencio (no se le reabre el canal) |

Reemplazo vs. silencio es decisión de cada tool: `escalate_to_human` reemplaza
por una despedida aprobada (la escalación es definitiva, no hay otra
oportunidad); `manage_conversation_tag` calla (una despedida enlatada podría
contradecir lo que el cliente acaba de preguntar).
