# 04 · Lecciones (qué NO repetir — índice de §9, L-0..L-34)

> Índice scannable de `ARCHITECTURE_FINAL_fable.md §9`. Cada lección allá tiene
> Síntoma → Causa → Fix → Regla-para-el-skill → Guard. Acá, la regla en una
> línea: si tu cambio roza alguna, leé la entrada completa ANTES de codear.

| L | Tema | La regla en una línea |
|---|---|---|
| L-0 | Refactor F1–F8 | lecciones operativas de la ejecución del refactor de plugins |
| L-1 | Cast HTTP timeout | el timeout de un cast se dimensiona para el hop LOCAL, no para el upstream del provider |
| L-2 | Latencia cloud | la latencia de un provider se ataca eliminando llamadas, no adelgazándolas |
| L-3 | Activity no registrada | una activity usada por un helper compartido pero ausente del worker muere en RUNTIME, no en boot (`F821`) |
| L-4 | Notificar ≠ poseer | notificar un estado no es tomar el turno conversacional; no acoples ownership al tracking |
| L-5 | Texto pre-tool | el content junto a una tool interna es narración de proceso, NO va al cliente |
| L-6 | Guard heredado | un guard de un modelo viejo puede bloquear el caso de negocio principal — revisá su vigencia |
| L-7 | Fire-and-forget | una task sin referencia la mata el GC sin log; guardá la referencia |
| L-8 | signal efímero | `via: signal` a un workflow efímero es carrera perdida → `signal_with_start` + mapping cubre el START |
| L-9 | nondeterminism | deploy de un workflow con runs vivos sin `workflow.patched()` rompe al restart (sticky cache lo esconde) |
| L-10 | Zod drift | parse estricto en el boundary sin estado de error visible = sección vacía en silencio; el contrato Zod es parte del MISMO cambio de dominio |
| L-11 | tool-loop sin corte | una tool que espera al cliente DEBE cortar el turno; el prompt no frena al modelo, el código sí |
| L-12 | autotransferencia | una tool de transferencia se registra SOLO en el worker ORIGEN; en el destino es autotransferencia |
| L-13 | handoff incompleto | el origen NO razona mensajes post-transferencia (los reenvía); buzón escrito-por-muchos/leído-por-uno = append-mode |
| L-14 | label en CI | un gate de CI por label lee el label del CONTEXTO DEL EVENTO; re-correr reusa el payload viejo (togglear por REST) |
| L-15 | ratchet stale | CI testea `refs/pull/NN/merge`; un ratchet congelado en un PR stale diverge → mergeá main + regenerá, no edites a mano |
| L-16 | cast sin identidad | todo cast loopback PORTA el `Authorization` entrante (castkit) — el hop interno no hereda la identidad del edge gratis |
| L-17 | seam olvidada | integrar un plugin con GraphAgents incluye la seam en `vscode-hubara/seams.yaml` (guard `test_graphagents_seams.py`); una regla solo-de-prosa se olvida — guard en el mismo PR |
| L-18 | depends_on omitido | plugin que escribe en sesiones ⇒ `depends_on: [chats]` (guard `test_session_plugins_depend_on_chats.py`); sin declarar, Acktos Studio lo dibuja como ISLA y P-6 no protege el deploy — al crear un plugin, verificá con `build_system_graph()` que queda CONECTADO |
| L-19 | precio del LLM | todo monto que se muestra o registra NACE en una tool desde el catálogo; el LLM solo transporta handles y cantidades; un umbral vive en UN módulo de `config/` |
| L-20 | acuse tras tool terminal | una tool que termina la conversación lleva el texto del cliente en un PARAM tipado y CORTA el turno — nunca un `llm_chat` "de despedida" tras el tool result (el modelo le acusa recibo al sistema y eso le llega al cliente); un tool result describe hechos, no da órdenes; la regla de VOZ del operador se hace ley con guard sobre los guiones que nosotros dictamos |
| L-21 | regex = lógica de replay | un predicado (regex, umbral, lista) cuyo veredicto decide commands DENTRO de un workflow se versiona como código: valor nuevo → set nuevo + `workflow.patched` propio; el mejor test es una history REAL saneada con control negativo |
| L-22 | historial = few-shot | lo que NO salió el LLM no lo recuerda: `record_turn` corre antes de que el caller decida enviar → el helper recibe `admin_turn` y recorta el `assistant` final no enviado; el corte de L-20 se decide por lo que la tool DECLARA (`tag_closure.ends_turn`), no por su nombre; cambio de solo-payload no lleva `patched`; si el corte depende de una clave NUEVA del envelope, la fixture útil es la de versiones mezcladas, con control negativo automatizado |
| L-23 | id `-preview` en prod | un id de modelo con fecha de apagado (o un `-preview`) en un camino de clientes se cae sin aviso si sus consumidores no lanzan: todo id upstream con revisión fechada (`REVIEWED_UPSTREAM_IDS`) |
| L-24 | corte por nombre | un corte de turno se decide por lo que la tool DECLARÓ que hizo (su resultado), no por su nombre |
| L-25 | sandbox con `setdefault` | un sandbox ASIGNA las variables que enrutan su estado (vault, historial, catálogo); `setdefault` no aísla |
| L-26 | Medusa y promociones | con draft orders Medusa es el libro contable: Hubara calcula el descuento y lo escribe en el precio; nunca `promo_codes` |
| L-27 | límite que falla abierto | un límite que se lee como «vacío» cuando algo falla se vuelve «sin límite»: falla cerrado |
| L-28 | conteo derivado | un conteo derivado de lo ya escrito incluye tu propia escritura (el reintento se ve a sí mismo) |
| L-29 | convención del envelope | una convención nueva del envelope aplica a TODOS sus emisores, también los que otra rama agregó en paralelo |
| L-30 | fallar cerrado ≠ mentir | «no pude leer el dato» no es «el dato cambió» |
| L-31 | lista cerrada | validar con lista cerrada un campo que ya viajaba libre rompe a sus emisores; lo que el bot dice tras una tool terminal va DENTRO de esa tool |
| L-32 | regla en todas las puertas | una regla que cambia qué se vende o a qué precio llega a TODAS las puertas y a todo lo que el bot muestra |
| L-33 | aviso que dice «sí» | un aviso que confirma algo al LLM enumera TODAS las condiciones del sí y lee el dato vivo |
| L-34 | decisión regada en código | un bug de decisión se arregla en una versión nueva del paquete (veredicto → ejemplo rojo → cambio mínimo → laboratorio → Terraform), nunca con un `if`/regex en el lugar que decide (`06-decision-bundles.md`) |

## El patrón que las genera (y cómo contribuís)

Cuando un run real revela un bug nuevo: escribí el **guard rojo** que lo
reproduce (00-tdd-law.md), aplicá el fix, y registrá la lección **L-#** en §9
de la semilla con su formato — ANTES de cerrar el incidente. Esa disciplina es
lo que hace que el skill no repita la clase de bug.

---
Fuente canónica: `ARCHITECTURE_FINAL_fable.md §9`. Es append-only y vive; este
índice puede ir atrás del código vivo — confirmá el número/título allá.
