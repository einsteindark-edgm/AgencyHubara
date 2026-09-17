# Runbook — Borrar las orders canceladas de Medusa

> Complemento de [`hubara_agency/scripts/reset_environment.py`](../hubara_agency/scripts/reset_environment.py).
> Ese script **cancela** las orders (Medusa v2 no deja borrarlas por la Admin API:
> quedan con `status="canceled"`). Este runbook las hace **desaparecer**.

## Por qué hace falta

- La Admin API de Medusa v2 **no expone** `DELETE /admin/orders/{id}` — solo `cancel`.
- El dashboard de Hubara **muestra** las canceladas (columna "Cancelado" del
  kanban; no las filtra — ver `medusa_order_query.py`), así que ensucian la vista.
- La única forma de eliminarlas es por debajo de la API, vía el **Order Module
  Service**: `softDeleteOrders(ids)`.

## Qué hace el `softDelete`

Setea `deleted_at` en cada order. Medusa filtra los soft-deleted en **todas** sus
queries → desaparecen del dashboard de Hubara **y** de Medusa Admin. Medusa
maneja el cascade (items, summary, shipping, etc.) solo. **Es reversible**:
`restoreOrders([...ids])`.

> Esto **no** es un borrado físico de filas. Para "desde 0" práctico alcanza:
> las orders dejan de existir a todos los efectos. Si necesitás un wipe físico
> real (p.ej. liberar el `display_id`), eso es SQL directo en el Postgres de
> Railway — pedímelo y lo armamos con inspección previa de FKs.

## El script

Corre en tu **proyecto backend Medusa** (`hubarabackend`), no en este repo.
Copiá esto a `src/scripts/soft-delete-canceled-orders.ts` de ese proyecto:

```typescript
/**
 * soft-delete-canceled-orders.ts — soft-borra TODAS las orders canceladas.
 *
 * USO (desde el repo del backend Medusa):
 *   # 1) Dry-run (default): lista qué borraría, NO borra nada:
 *   npx medusa exec ./src/scripts/soft-delete-canceled-orders.ts
 *
 *   # 2) Borrar de verdad (soft-delete, reversible):
 *   CONFIRM=1 npx medusa exec ./src/scripts/soft-delete-canceled-orders.ts
 *
 *   # Apuntando a la DB de Railway desde tu máquina:
 *   railway run npx medusa exec ./src/scripts/soft-delete-canceled-orders.ts
 *   # (o exportá DATABASE_URL="<postgres de Railway>" antes del comando)
 *
 * Recuperar si te arrepentís:  orderModuleService.restoreOrders([...ids])
 */
import { ExecArgs } from "@medusajs/framework/types"
import { ContainerRegistrationKeys, Modules } from "@medusajs/framework/utils"

export default async function softDeleteCanceledOrders({ container }: ExecArgs) {
  const logger = container.resolve(ContainerRegistrationKeys.LOGGER)
  const orderModuleService = container.resolve(Modules.ORDER)

  // `listOrders` no expone `status` como filtro tipado → paginamos trayendo
  // id+status+display_id y filtramos las canceladas en código. Las orders ya
  // soft-deleted NO vuelven a aparecer acá, así que el script es idempotente.
  const pageSize = 200
  let skip = 0
  const canceled: { id: string; display_id?: number }[] = []

  for (;;) {
    const orders = (await orderModuleService.listOrders(
      {},
      { select: ["id", "status", "display_id"], take: pageSize, skip },
    )) as Array<{ id: string; status?: string; display_id?: number }>

    if (orders.length === 0) break
    for (const o of orders) {
      if (o.status === "canceled") {
        canceled.push({ id: o.id, display_id: o.display_id })
      }
    }
    if (orders.length < pageSize) break
    skip += pageSize
  }

  if (canceled.length === 0) {
    logger.info("No hay orders canceladas. Nada para borrar.")
    return
  }

  const ids = canceled.map((o) => o.id)
  const preview = canceled
    .slice(0, 20)
    .map((o) => (o.display_id ? `#${o.display_id}` : o.id))
    .join(", ")
  const confirm = process.env.CONFIRM === "1"

  logger.info(
    `${confirm ? "BORRANDO" : "[DRY-RUN]"} ${ids.length} orders canceladas` +
      (canceled.length > 20 ? ` (primeras 20: ${preview}, …)` : `: ${preview}`),
  )

  if (!confirm) {
    logger.info("DRY-RUN: no se borró nada. Re-corré con CONFIRM=1 para ejecutar.")
    return
  }

  await orderModuleService.softDeleteOrders(ids)
  logger.info(
    `✅ Soft-delete OK: ${ids.length} orders. Reversible con restoreOrders([...]).`,
  )
}
```

## Orden recomendado para el reset completo

1. `reset_environment.py --dry-run` → revisás conteos.
2. `reset_environment.py` → borra drafts + **cancela** orders + borra customers + limpia el vault.
3. `CONFIRM=1 npx medusa exec ./src/scripts/soft-delete-canceled-orders.ts` → hace
   desaparecer las que quedaron canceladas en el paso 2.

Resultado: Medusa y el vault quedan limpios para el arranque real.
