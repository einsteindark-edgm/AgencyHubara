# 04 · Lecciones (qué NO repetir — índice de §9, L-0..L-15)

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

## El patrón que las genera (y cómo contribuís)

Cuando un run real revela un bug nuevo: escribí el **guard rojo** que lo
reproduce (00-tdd-law.md), aplicá el fix, y registrá la lección **L-#** en §9
de la semilla con su formato — ANTES de cerrar el incidente. Esa disciplina es
lo que hace que el skill no repita la clase de bug.

---
Fuente canónica: `ARCHITECTURE_FINAL_fable.md §9`. Es append-only y vive; este
índice puede ir atrás del código vivo — confirmá el número/título allá.
