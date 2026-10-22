# ADR 2026-10-22 — Acktos Stores: una plataforma, las tiendas como configuración

- **Status**: Accepted
- **Date**: 2026-10-22
- **Decisión del operador**: «plan D» del análisis de costos por tienda (2026-10-19): Vincenzo es la primera tienda de la plataforma y Hubara se muda después. Las decisiones de cuenta, base de datos, app de Meta y reactivación son del 2026-10-21 (plan, §9).
- **Plan**: `ACKTOS_STORES_PLAN.md` (fases, costos, riesgos y runbook de la migración)
- **Reemplaza** para las tiendas pequeñas: el silo por cliente de `VINCENZO_SPLIT_PLAN.md` (2026-07-09)
- **Spinal files**: ninguno en F1.1. Aprueba de antemano dos cambios del plan: `src/platform/tool_extensions.py` (F1.7: las tools llevan el agente como llave) y `src/platform/constants.py` (F1.8: IDs por tienda). Cada PR que los toque cita este ADR y lleva el label `architecture-change`.

## Contexto

El motor es «un proceso = una tienda» a propósito (`ADR-2026-06-05` dejó para
después el multi-tenant compartido). Para una tienda nueva, forge clona el repo
y levanta toda la infraestructura:

- **Costo.** Un clon de forge con sus valores por defecto cuesta unos US$184 al
  mes por tienda, y unos US$68 ajustado. Los clientes son emprendedores
  pequeños: el margen es lo que queda entre lo que pagan y lo que cuesta el
  software.
- **Operación.** Cada mejora del motor hay que fusionarla en un repo por
  cliente. Las dos fusiones de `main` a la rama de forge de octubre pidieron
  arreglar unas 30 pruebas.
- **Momento.** Vincenzo todavía no está en producción. Se puede cambiar el
  modelo sin migrar a nadie.

Además, desde la ola 0 de Temporal (#444) los dispatchers arrancan con
`id_conflict_policy` (`TERMINATE_EXISTING` / `USE_EXISTING`). Si dos tiendas
comparten un namespace y el mismo cliente les escribe a las dos, un ID sin la
tienda termina o reusa el workflow de la otra sin error visible.

## Decisión

1. **Un motor, un repo y una sola `main`.** No hay fork ni rama larga de
   Acktos. El motor aprende de qué tienda es cada cosa. Hubara, en su caja de
   siempre, corre el mismo código como única tienda: la tienda por defecto, con
   las mismas rutas del vault y los mismos IDs de Temporal (tiene workflows
   vivos). Cada paso de F1 se despliega primero a Hubara sin cambiar su
   comportamiento.
2. **Contexto de tienda** (F1.1). `src.platform.tenant` es una `ContextVar`
   que la entrada (webhook, API, Temporal, ciclos) fija con `tenant_scope()`.
   Todo lo demás la lee con `current_tenant()`. Los plugins la importan de
   `src.sdk.tenantkit` (`docs/_sdk/19-tenantkit.md`). Nadie más lee
   `ACKTOS_TENANT` ni `ACKTOS_CELL` del entorno. La celda (`ACKTOS_CELL=1`) no
   tiene tienda por defecto: leerla fuera de un alcance es un error, nunca
   otra tienda.
3. **La tienda es dato, no código.** Todo lo que forge reescribe hoy en el
   motor pasa a la configuración de la tienda (`TenantConfig`, F1.3 y F1.6). Los
   paquetes de tienda viven en un repo privado `acktos-tenants`, y
   `forge tenant create` reemplaza a `forge apply` para las tiendas pequeñas.
4. **Temporal.** En la celda, todo ID de workflow y de schedule lleva el
   prefijo `<tienda>:`. La tienda viaja en un encabezado (F1.2). Un payload
   lleva la tienda, nunca sus secretos: es la regla de
   `2026-10-19-llm-key-out-of-temporal-payloads`, porque en un namespace
   compartido cualquiera que lea la historia vería los secretos de todas.
5. **Infraestructura.**
   - Una cuenta de AWS nueva dentro de una organización, donde Hubara es la
     cuenta de gestión. Terraform propio (`infra/terraform/acktos/`).
   - Una celda (EC2 t4g.large) con el motor, LiteLLM, Redis y un Medusa por
     tienda, más una base de RDS t4g.micro por tienda.
   - El namespace `acktos` en la misma cuenta de Temporal Cloud, en pago por
     uso.
6. **El silo de forge** queda como opción para un cliente grande que exija
   aislamiento total.

## Alternativas descartadas

- **Seguir con un clon de forge por tienda:** cuesta de US$68 a US$184 al mes
  por tienda y una fusión por repo en cada mejora.
- **Un fork o una rama larga de Acktos:** diverge de Hubara. Es justo el costo
  de operación que se quiere quitar.
- **Pasar la tienda como argumento por todas las funciones:** toca cientos de
  firmas, y una que se olvide la pierde. La `ContextVar` en la entrada y el
  encabezado de Temporal la llevan solos, y la guarda de lector único impide
  los atajos.
- **Un proceso de workers por tienda en la misma caja:** multiplica la memoria
  por tienda y no llega a las 10–15 tiendas por caja del plan.
- **Medusa multi-tienda o Mercur, EKS, Lambda y Hetzner:** el plan, §10.

## Consecuencias

- **F1 va en pasos chicos.** Cada PR corre la suite completa de Hubara y pasa
  por canary. Una suite con dos tiendas sintéticas (F1.12) exige que nada se
  cruce: vault, Temporal, API, SSE y app.
- **Código nuevo:**
  - lo que dependa de la tienda la lee con `current_tenant()`;
  - un caché o una global con estado de tienda lleva la tienda en la llave;
  - para correr en un hilo algo que lee la tienda, se usa `asyncio.to_thread`,
    porque `loop.run_in_executor` no copia el contexto.
- **forge:** el default `"hubara"` de `DEFAULT_STORE` es una cifra de la tienda
  madre. Cuando el PR #410 llegue a `main`, el manifest de forge suma la regla
  que lo cambia por `{{slug}}` en el clon, como ya hace con
  `META_ADS_TENANT`.
- **Riesgos y cómo se contienen:** el plan, §8.
