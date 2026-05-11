# Tech refinement — 01 HttpMedusaClient (adapter HTTP a Medusa Admin API)

- **HU id**: catalog-01
- **Source**: `features/catalogAgent/MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md` (guía técnica)
- **Target agent**: `platform` (cross-agent infra) en `hubara_agency/` (multi-agent repo)
- **Refiner**: exoclaw-tech-refiner
- **Date**: 2026-05-07

## 1. Scope

**Summary**: Adapter HTTP async (`httpx`) contra la Admin API de Medusa v2, tipado con Pydantic v2, sin SDK oficial. **No es una activity ni una tool todavía** — es la pieza de adapter que el agente `catalog_sync` (HU-03) consumirá dentro de sus activities, y que opcionalmente Sales podría usar en una "stock real-time" tool (futuro).

**Acceptance criteria**:
- Given `MEDUSA_BASE_URL` y `MEDUSA_ADMIN_TOKEN` válidos, When llamo `client.list_products(status="published", limit=2)`, Then recibo `MedusaProductPage` con `count >= 0` y los `amount` parseados como `Decimal`.
- Given un `product_id` inexistente, When llamo `client.get_product(...)`, Then se levanta `MedusaAPIError(status_code=404, ...)`.
- Given una expansión de `fields` que SÍ incluye `*variants,*variants.prices`, When parseo la respuesta, Then `MedusaProduct.variants[0].prices[0].amount` es `Decimal("49.99")` (no `float`).
- Given un fallo transitorio de red (`httpx.TransportError`), When `_request` reintenta, Then se reintenta hasta 3 veces con backoff exponencial.
- Given el JWT de Opción B expira (401), When la próxima llamada se hace, Then `_login` reloga una vez antes de propagar el 401 al caller.
- `pyproject.toml` declara `httpx>=0.27,<1`, `pydantic>=2.6,<3`, `tenacity>=8.2,<10`, `pydantic-settings>=2,<3`.

**Out of scope**:
- `catalog_sync` workflow / activities / Schedule (HU-03).
- `CatalogPort` / `LocalSnapshotCatalogClient` (HU-02).
- Tools del agente Sales (HU-04).
- Cualquier `register_tool_extension` o cambio en `worker.py` de Sales.
- Rollout / K8s wiring (HU-05).
- Métodos de escritura (`POST /admin/products`, etc.). Solo lectura por ahora.

## 2. Workflow mode

**Decision**: N/A — esta HU NO crea workflow. Es una librería de adapter HTTP que será **consumida desde activities** del workflow `CatalogSyncWorkflow` (HU-03).

**Justificación**: La librería no tiene durabilidad propia — la durabilidad la aporta la activity que la usa. Mezclar Temporal aquí violaría DEHA (R-DIP: el adapter es puro, no conoce Temporal).

**File**: N/A.

## 3. Boundary DTOs (R-JSON)

Esta HU NO añade DTOs de boundary porque ningún valor de este módulo cruza `workflow.execute_activity` directamente. Los modelos Pydantic viven dentro del adapter y se convierten a DTOs planos del `platform/catalog/dtos.py` (HU-02) en el límite.

**Modelos Pydantic internos del adapter** (NO son boundary DTOs — viven dentro del proceso de la activity):

| Modelo | Campos clave | Notas |
|---|---|---|
| `MedusaPrice` | `id`, `amount: Decimal`, `currency_code`, `min_quantity`, `max_quantity`, `price_list_id`, `rules` | `amount` es **Decimal**, NO float (gotcha §4.5 de la guía Medusa). |
| `MedusaOptionValue` | `id`, `value` | |
| `MedusaVariant` | `id`, `title`, `sku`, `manage_inventory`, `allow_backorder`, `prices`, `options` | |
| `MedusaOption` | `id`, `title`, `values` | |
| `MedusaImage` | `id`, `url`, `rank` | Ordenado por `rank`. |
| `MedusaTag` | `id`, `value` | |
| `MedusaCategory` | `id`, `name`, `handle`, `parent_category_id` | |
| `MedusaCollection` | `id`, `title`, `handle` | |
| `MedusaSalesChannel` | `id`, `name` | |
| `MedusaProduct` | `id`, `title`, `handle`, `description`, `status`, `thumbnail`, `metadata`, `created_at`, `updated_at`, `variants`, `options`, `images`, `tags`, `categories`, `collection`, `sales_channels` | |
| `MedusaProductPage` | `products: list[MedusaProduct]`, `count`, `offset`, `limit` | |

Todos con `model_config = ConfigDict(extra="ignore")` para tolerar campos nuevos de Medusa sin romper.

**Reused from `exoclaw_temporal.config`**: ninguno (esta HU no toca Temporal).

## 4. Activities

Ninguna en esta HU. Las activities que **consumirán** este adapter las define HU-03.

## 5. Tools

Ninguna en esta HU. Las tools que **consumirán** este adapter (vía el `CatalogPort` de HU-02) las define HU-04.

## 6. Use cases

**No use case needed** — el adapter es lo suficientemente delgado y específico (un cliente HTTP de UN backend) que no califica para `use_cases/`. Si en el futuro hay coordinación de múltiples llamadas (ej: paginar + dedupe + transformar), eso vivirá en HU-03 dentro de `catalog_sync/use_cases/`, no aquí.

## 7. State adapters

Ninguno. El adapter no persiste nada — solo HTTP + parseo en memoria.

## 8. Prompts / workspace changes

- `src/<agent>/prompts.py` — sin cambios.
- `workspace/IDENTITY.md` — sin cambios.
- `workspace/SOUL.md` — sin cambios.
- `workspace/USER.md` — sin cambios.
- `workspace/TOOLS.md` — sin cambios (las tools se introducen en HU-04).
- `workspace/AGENTS.md` — sin cambios.
- `workspace/skills/...` — sin cambios.

## 9. Composition wiring

| Factory en | Returns | Consumed by |
|---|---|---|
| `src/platform/medusa/composition.py` :: `get_medusa_client()` (lru_cache(1)) | `HttpMedusaClient` | Activities de `catalog_sync` (HU-03), opcionalmente futuras tools de Sales con datos en vivo. |
| `src/platform/medusa/composition.py` :: `get_medusa_product_service()` (lru_cache(1)) | `MedusaProductService` | Igual. |

> **Layout decision**: aunque la regla DEHA dice que cada agente tiene su propio `composition.py`, este adapter es **cross-agent** (vive en `platform/`). Por consistencia con `src/platform/temporal/client.py:get_temporal_client()` (que también es cross-agent), usamos `composition.py` dentro del paquete `platform/medusa/`. **No** crea un `composition.py` por agente — esa regla aplica a `<agent>/`, no a `platform/`.

**Settings provider** (lee env): `src/platform/medusa/settings.py` con `MedusaSettings(BaseSettings)` (Pydantic Settings v2). Esta es la única excepción a "no leer env fuera de `<agent>/config/env.py`" porque `platform/` es código compartido — sigue el mismo patrón que `src/platform/config.py:9-25` (que lee `TEMPORAL_URL`, `WORKSPACE_VAULT_DIR`, etc.).

## 10. Worker registration

Sin cambios. Esta HU no toca `worker.py` de ningún agente. La activity que sí lo hará es de HU-03 (`catalog_sync/worker.py` — un nuevo worker).

## 11. Hard rules check

- **R-DET**: **N/A** — no hay workflow code.
- **R-JSON**: **N/A** en esta HU (los Pydantic NO cruzan `workflow.execute_activity`; se convierten a `@dataclass` en HU-02). Riesgo: si alguien en HU-03 retorna un `MedusaProduct` desde una activity, se rompe R-JSON. **Mitigación documentada**: HU-03 sólo retornará DTOs de `platform/catalog/dtos.py`.
- **R-STATELESS**: **applies — handled how**: el `HttpMedusaClient` mantiene estado (`_jwt`, `_http`) pero es **una sola instancia compartida por proceso vía `lru_cache(1)` en composition**. No hay `_REGISTRY = ` ni mutables module-level. Cada activity invocation toma el mismo cliente — esto es correcto: el cliente HTTP es un recurso de larga vida (igual que `get_temporal_client`).
- **R-HEARTBEAT**: **N/A** en esta HU. Las activities de HU-03 que envuelvan `client.iter_products(...)` (potencialmente larga si el catálogo crece) llevarán `@with_heartbeat(every=10)`.
- **R-DIP**: **applies — handled how**: el adapter NO importa `temporalio.*`, NO importa `exoclaw.*`, NO importa de ningún agente. Solo `httpx`, `pydantic`, `tenacity`, `pydantic_settings`, stdlib. Confirma con `grep -rEn "^from (temporalio|exoclaw|src\.(sales|remarketing)_whatsapp)" src/platform/medusa/`.

## 12. Tests

| Test file | Type | Asserts |
|---|---|---|
| `tests/platform/medusa/test_client_auth.py` | Unit | Header `Authorization: Basic <base64(token + ":")>` se construye con un token conocido (golden). Header `Bearer <jwt>` cuando se usan email/password y no hay token. |
| `tests/platform/medusa/test_client_jwt_relogin.py` | Unit (`respx`) | En modo JWT: 401 → `_login` llamado 1 vez → segundo intento ok. Modo Secret: 401 → NO reloguea, levanta `MedusaAPIError`. |
| `tests/platform/medusa/test_client_retries.py` | Unit (`respx`) | `httpx.ConnectError` triplicado → tercer intento ok. Cuarto fallo → propaga. |
| `tests/platform/medusa/test_client_pagination.py` | Unit (`respx`) | `iter_products(page_size=2)` con `count=5` itera 3 páginas, yieldea 5 productos, llama `/admin/products` 3 veces con `offset` esperado. |
| `tests/platform/medusa/test_models_decimal.py` | Unit | `MedusaProduct.model_validate(...)` con `amount: 49.99` (number) y `amount: "49.99"` (string) → ambos producen `Decimal("49.99")`. |
| `tests/platform/medusa/test_models_extra_fields.py` | Unit | Producto con campos extras (`weight: 100`, `unknown_field: "x"`) — Pydantic ignora el extra (no levanta). |
| `tests/platform/medusa/test_settings.py` | Unit (monkeypatch env) | `MedusaSettings()` levanta si falta `MEDUSA_BASE_URL`. Lee `MEDUSA_ADMIN_TOKEN` con prefijo correcto. |
| `tests/platform/medusa/test_default_fields.py` | Unit | `DEFAULT_PRODUCT_FIELDS` contiene `*variants` y `*variants.prices` por separado (gotcha §4.1 de la guía). Asserts ambos substrings. |

Replay: N/A (esta HU no toca workflows).

## 13. Risks / open questions

- **R1**: ¿Qué `MEDUSA_BASE_URL` usamos en dev? Recomiendo apuntar a un Medusa de staging si existe; si no, levantar uno local con `docker compose` (Medusa OSS) y dejarlo como `MEDUSA_BASE_URL=http://localhost:9000`. **Recomendado default**: `http://localhost:9000` para dev, env var en K8s para prod (HU-05 lo cierra).
- **R2**: La guía `MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md` documenta Medusa **2.12.5**. Confirmar contra `hubara_backend/medusa-backend/package.json` que sigue siendo esa versión. **Acción**: HU-05 valida antes de rollout.
- **R3**: La Opción B (JWT email/password) la incluimos en el cliente para simetría con la guía pero **el path canónico para nosotros es Opción A** (Secret API Key). Tests cubren ambos.
- **R4**: El parámetro `fields` se serializa **como string CSV**, no como lista (`params["fields"] = "id,title,..."`). `httpx` codifica la string entera como un único valor — confirmar con un curl-equivalente test.
- **R5**: `id[]=...` y `tags[]=...` requieren que `httpx` mande el `[]` literal en el query string. `httpx.AsyncClient` lo hace bien con `params={"id[]": [...]}`. **Verify** con un test de paginación que pase `ids=[...]`.
- **R6**: Pydantic v2 `Decimal` desde JSON number puede perder precisión por el `float` intermedio del JSON parser. **Mitigación**: usar `model_config = ConfigDict(extra="ignore")` y declarar `amount: Decimal` — Pydantic v2 acepta number→Decimal directamente. Test cubre ambos paths (number / string).
- **Defer to `temporal:temporal-developer`**: ninguno (esta HU no toca Temporal).
- **Defer to `claude-api`**: ninguno (esta HU no toca LLM).

## 14. Implementation order (suggested)

1. Añadir deps a `pyproject.toml` (`httpx`, `pydantic`, `tenacity`, `pydantic-settings`). `uv sync`.
2. Crear `src/platform/medusa/__init__.py`, `settings.py`, `models.py`, `client.py`, `service.py`, `composition.py` (en ese orden — settings→models→client→service→composition; cada paso lo testea con respx).
3. Tests unitarios en `tests/platform/medusa/` (auth, retries, pagination, models, settings).
4. Smoke manual con `curl` siguiendo §6 de la guía Medusa para confirmar conectividad antes de cerrar la HU.

(Cada paso mantiene tests verdes; no hay Big Bang. El implementer convertirá esto en PRs.)

---

**Next step**: invocar al implementer con este archivo:

```
/exoclaw-implementer .exoclaw/refinements/01-http-medusa-client-tech.md
```
