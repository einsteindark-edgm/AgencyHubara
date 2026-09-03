# Plugin: catalog

> Behavior contract — bootstrap 2026-09-02 (HU multi-catálogo, PR #226).
> Fuente: `hubara_agency/src/plugins/catalog/` + `frontend_dashboard/src/plugins/catalog/`.
> Tests que enforcan los Scenarios: `hubara_agency/tests/catalog_sync/`.

## Purpose

El plugin `catalog` mantiene la **copia local del catálogo** (snapshot desde
Medusa, la verdad para el agente de ventas) y la **propaga a Meta Commerce
Catalog** para que WhatsApp muestre tarjetas de producto. Un sync = pull
(Medusa) → write (snapshot) → push (Meta). El push va a un catálogo
**primario** y, opcionalmente, a N catálogos **réplica** que Meta no permite
compartir ni conectar (caso real: el catálogo del número en coexistencia con
la app WhatsApp Business del teléfono).

## Requirements

### Requirement: Push del mismo batch a primario y réplicas

El sistema SHALL enviar exactamente el mismo conjunto de ítems (mismos
retailer_id, precios, imágenes, `item_group_id` y color de variantes) al
catálogo primario y a cada catálogo réplica configurado, en ese orden, sin
duplicados ni ids vacíos. El catálogo primario MUST seguir siendo uno solo
(el agente de ventas lo usa para las tarjetas `product_list`).

#### Scenario: Réplicas configuradas

- GIVEN `META_CATALOG_ID=A` y `META_EXTRA_CATALOG_IDS=" B , ,A, C "`
- WHEN corre el sync
- THEN se envía un batch a A, otro a B y otro a C, con los mismos ítems
- AND el resultado reporta `catalogs_pushed=3` y `per_catalog_json` con el desglose de cada uno

#### Scenario: Sin réplicas

- GIVEN `META_EXTRA_CATALOG_IDS` vacío o ausente
- WHEN corre el sync
- THEN el comportamiento es idéntico al histórico (un solo batch al primario, `catalogs_pushed=1`)

#### Scenario: Sin primario

- GIVEN `META_CATALOG_ID` vacío, aunque haya réplicas
- WHEN corre el sync
- THEN el push hace graceful skip (`pushed=false`, `error=meta_not_configured`) y el snapshot local sigue válido

### Requirement: Delta independiente por catálogo

El sistema SHALL mantener un estado de delta (hashes del último push
exitoso) por catálogo, de modo que agregar una réplica no re-envíe el
primario y la réplica nueva reciba un push completo.

#### Scenario: Réplica agregada después del primer sync

- GIVEN el primario ya sincronizó y su estado existe
- WHEN se agrega una réplica y corre el sync sin cambios en Medusa
- THEN el primario no recibe batch (sin cambios)
- AND la réplica recibe todos los ítems como CREATE (upsert), incluyendo `item_group_id` de las variantes

#### Scenario: Re-sync forzado

- GIVEN cualquier estado previo
- WHEN el operador dispara "Forzar re-sync completo"
- THEN todos los catálogos reciben todos los ítems (recupera imágenes no descargadas por Meta)

### Requirement: Aislamiento de fallas entre catálogos

El sistema SHALL seguir pusheando a los demás catálogos cuando uno falla,
reportar `ok=false` con el catálogo culpable en `error`, y NO persistir el
estado del catálogo fallido (el próximo sync lo reintenta).

#### Scenario: Una réplica responde 5xx

- GIVEN primario OK y réplica B respondiendo error
- WHEN corre el sync
- THEN el primario persiste su estado y la réplica no
- AND el resultado es `ok=false`, `error` contiene "B: …", `per_catalog_json[B].ok=false`
- AND el workflow completa con el step "push" en `failed` y `progress.error` con el mensaje (la historia de syncs no lo muestra verde)

### Requirement: Verificación del resultado asíncrono del batch

Meta acepta el batch y lo procesa después. El sistema SHALL consultar el
estado del batch hasta que termine (con presupuesto acotado) y MUST tratar
los rechazos por ítem como fallo del push de ese catálogo, conservando los
hashes previos para reintentar.

#### Scenario: Ítem rechazado por Meta

- GIVEN un batch aceptado cuyo estado final trae errores por ítem
- WHEN el sync verifica el estado
- THEN el push de ese catálogo es `ok=false` con el retailer_id y el mensaje del rechazo
- AND los hashes previos se conservan (los ítems se reintentan en el próximo sync)

#### Scenario: Batch todavía en curso agotado el presupuesto

- GIVEN un batch que sigue `started` tras el máximo de consultas
- WHEN se agota el presupuesto
- THEN el push se considera exitoso y el handle queda en el resultado para consulta posterior
