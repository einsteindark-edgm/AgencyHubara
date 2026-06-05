# ADR-2026-06-05 — Multi-Tenant Commerce Architecture

**Estado:** Propuesto
**Owner:** Operador
**Design doc:** [MULTI_TENANT_COMMERCE_ARCHITECTURE.md](MULTI_TENANT_COMMERCE_ARCHITECTURE.md) (contrato detallado)
**Relacionado:** [PLUGIN_ARCHITECTURE.md](PLUGIN_ARCHITECTURE.md) (D1), ADR-2026-05-20 (declarative orchestration)
**Implementation status:** ⏳ No iniciado — este ADR autoriza el diseño, no edita código.

---

## §0. TL;DR

AgencyHubara va a multi-tenant. El requerimiento se descompone en **dos ejes
ortogonales**:

- **Eje A (Routing):** a qué Medusa apunta cada tenant. → **modelo silo** (1
  Medusa + 1 DB por tenant) + bundle de config por tenant. **Cero cambio al
  código vivo** (es packaging). Confirmado: Medusa v2 **no es multi-tenant
  nativo** y comparte `customers` a nivel instancia, así que pooled-single-instance
  no aísla empresas. = decisión D1 ya tomada.
- **Eje B (Combinación):** cómo se guardan productos, se resuelven variantes y
  cambian de estado las órdenes (hoy **hardcodeado** en los adaptadores Medusa).
  → **commerce profile** (la combinación como YAML) + **registry de 6
  estrategias** (espeja `tool_extensions.py`). El comportamiento actual se
  **extrae byte-idéntico** como `hubara-co-default`.

**Invariante:** agregar un tenant, un profile o una estrategia **NUNCA edita
archivos en producción**.

---

## §1. Contexto y problema

### §1.1 Estado actual (single-tenant por proceso)

- Órdenes y catálogo son **hexagonal estricto**: puertos (`OrderRegistrationPort`,
  `OrderQueryPort`, `OrderCommandPort`, `CatalogPort`) + adaptadores Medusa +
  composition root con `@lru_cache(maxsize=1)`.
- La config Medusa vive en env `MEDUSA_*` ([`MedusaSettings`](hubara_agency/src/platform/medusa/settings.py))
  leída una vez. **Un proceso = un tenant.**
- Las "combinaciones" están hardcodeadas en los adaptadores:
  - variantes-como-tags ([`_pick_variant_with_status`](hubara_agency/src/platform/orders/medusa_order.py#L417)),
  - flujo draft→convert→payment ([`MedusaOrderCommand`](hubara_agency/src/platform/orders/medusa_order_command.py)),
  - máquina de 6 estados ([`state.py`](hubara_agency/src/platform/orders/state.py)),
  - currency COP, customer email sintetizado, shipping por keywords.

### §1.2 El requerimiento del operador

1. Multi-tenant: cada empresa puede tener config Medusa distinta y/o backend
   distinto.
2. Onboarding de Medusa nueva **por archivos de config**.
3. Prender/apagar el plugin de órdenes **por tenant** y que funcione todo.
4. **Aislamiento total**: plugin/config nuevo no toca nada productivo.
5. **No romper producción por ninguna razón.**

### §1.3 La pregunta sobre Medusa

> *"medusa puede que esté configurado diferente y va a ir a un backend diferente
> o no sé si medusa soporte multitenant, investiga eso bien."*

Investigado (§4 del design doc). Respuesta corta: **Medusa v2 no soporta
multi-tenancy nativo**; `customers` se comparten a nivel instancia; el patrón
que gana para 5–50 merchants es **una instancia + DB por tenant (silo)**.

---

## §2. Decisión

### §2.1 Eje A — silo + routing por config

- **C1.** 1 Medusa + 1 DB por tenant (silo). Único modelo con aislamiento real
  (= D1).
- **C2.** La Hubara-app sigue single-tenant por proceso (1 deployment/tenant). Se
  preservan `@lru_cache(1)`, R-STATELESS y los bindings module-level del sales
  worker **sin tocarlos**.
- **C3.** Routing por config: `infra/tenants/<id>/hubara.yaml` (enabled_plugins +
  `secret_ref` a Medusa + `commerce_profile`). El env `COMMERCE_PROFILE` + el
  secret bundle son el switch por deployment.
- **C7.** Medusa nueva por config = `medusa-config.ts`/env **+ `medusa-seed.yaml`
  declarativo** reproducido vía Admin API tras `db:migrate` (regions/channels/
  shipping/currency son runtime-only).

### §2.2 Eje B — commerce profile + strategy registry

- **C4.** La combinación se declara en `commerce_profiles/<id>.yaml`; lo que
  difiere como *valor* es config, lo que difiere como *algoritmo* es una
  estrategia registrada.
- **C5.** El comportamiento actual = `hubara-co-default`, extraído byte-idéntico
  a las 6 estrategias (variant · order_flow · state_machine · shipping · customer
  · payment).
- **C6.** Los puertos NO cambian su firma; el cambio es interno a adaptadores +
  composition.
- **C8.** El registry es append-only / auto-discovered (espeja `register_tool_extension`
  + el auto-discovery de plugins) → estrategia nueva = archivo nuevo.

### §2.3 Regla resultante

> **Agregar un tenant, un commerce profile o una estrategia se hace SOLO
> creando archivos nuevos. Nunca se edita el bundle de otro tenant, un profile
> existente, una estrategia existente, ni un puerto. El profile default
> (`hubara-co-default`) reproduce producción byte-idéntico; toda extracción se
> valida con la suite completa antes de mergear.**

---

## §3. Alternativas rechazadas

| Alternativa | Veredicto | Razón |
|---|---|---|
| Medusa single-instance multi-store (sales-channel/tenant) | ❌ | `customers` compartidos; aislamiento débil para empresas distintas. |
| Medusa RLS shared-schema (`tenant_id` en 44+ tablas) | ❌ (por ahora) | Patch del framework eterno; raw SQL/singletons/jobs bypassean RLS. Reconsiderar a escala de cientos/miles. |
| Medusa schema-per-tenant | ❌ | No soportado; migraciones dolorosas; app compartida. |
| Pooled multi-tenant en Hubara (1 proceso, N tenants) | ⏸ Diferido | El silo lo hace innecesario; requeriría delazyficar los bindings module-level del sales worker. |
| Mini-DSL de condiciones en el profile | ❌ | Pendiente resbaladiza (cf. ADR-2026-05-20 §10.3). El profile solo selecciona estrategias + primitivos. |

---

## §4. Consecuencias / Trade-offs (a sabiendas)

**Positivas:**
- Aislamiento físico entre empresas (no se puede filtrar mal).
- Onboarding declarativo; nuevo tenant/profile/estrategia = archivos nuevos.
- Cero cambio al código vivo para el Eje A.
- Idiomático: reusa plugin auto-discovery + `tool_extensions` + composition.

**Negativas (mitigadas):**
- **Costo N deployments** → bin-pack containers + Postgres compartido (1 DB/tenant)
  + Redis con `redisPrefix`.
- **Control-plane a construir** → empezar con runbook manual; automatizar después.
- **Riesgo de la extracción Eje B** → byte-idéntica + gateada por suite +
  architecture gates + functional E2E; default reproduce hoy.
- **Registry completo es trabajo upfront** antes del 2º profile real → aceptado
  explícitamente por el operador (decisión "registry completo ya").

---

## §5. Plan (autoriza, no ejecuta)

Detalle en design doc §11. Resumen: PR0 scaffolding → PR1–PR4 extraer las 6
estrategias (1 grupo por PR, byte-idéntico) → PR5 composition profile-aware →
PR6 bundle + control-plane. Cada PR verde en `pytest` + `lint-imports` +
`pytest -m architecture` antes de mergear.

---

## §6. Referencias

- **[MULTI_TENANT_COMMERCE_ARCHITECTURE.md](MULTI_TENANT_COMMERCE_ARCHITECTURE.md)** — contrato detallado (6 estrategias, schemas, seams, migración).
- **[PLUGIN_ARCHITECTURE.md](PLUGIN_ARCHITECTURE.md)** — D1 (silo), R1/R2/R3 (aislamiento de plugins).
- **ADR-2026-05-20** — declarative orchestration (patrón de manifest-as-SSoT que este ADR extiende al eje comercio).
- Medusa: [no native multi-tenancy](https://docs.medusajs.com/resources/commerce-modules/store) · [deployment](https://docs.medusajs.com/learn/deployment) · [#11671](https://github.com/medusajs/medusa/discussions/11671).
- Código vivo de referencia: `src/platform/{medusa,orders,catalog}/`, `src/platform/tool_extensions.py`, `src/plugins/{orders,catalog}/`.

---

## §7. Decisión

**Propuesto — pendiente de aprobación del operador para arrancar PR0.**

Al aprobarse:
- ✅ Eje A queda definido (silo + bundle); el onboarding deja de requerir edición de código.
- ✅ Eje B queda definido (profile + 6 estrategias); cualquier combinación futura es config o archivo-nuevo.
- ✅ Producción protegida: default byte-idéntico + aislamiento de 4 niveles.

---

**Fin ADR-2026-06-05.**
