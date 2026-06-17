# 06 · CLI `hubara` — check / certify / explain / graph / create

> Fase F-SDK-3 · Fuente: `hubara_agency/src/sdk/cli/` · Gate: golden test del scaffolder

## Qué problema soluciona

El ciclo de vida del plugin system vivía en recetas en prosa
(ARCHITECTURE_FINAL §4: "crear un plugin en 6 pasos a mano") y el patrón real
era copy-paste de un plugin parecido. El CLI lo vuelve **comandos
deterministas** — para humanos y para los skills del pipeline (que dejan de
interpretar recetas y ejecutan verbos con exit codes claros).

## Cómo se usa

```bash
# SIEMPRE desde el repo root, con el prefijo del hook:
cd hubara_agency && uv run python -m src.sdk.cli <verbo>
```

| Verbo | Qué hace | Exit |
|---|---|---|
| `check [<id>...]` | el **compilador rápido**: TCK estático (sin red, segundos) con salida estilo rustc (`error[P-x] + fix + ref`). Sin args = todos | 0 ok · 1 violaciones |
| `certify [<id>...]` | check + escribe `.hubara/certification/<id>.json` + tabla de niveles | 1 si algún plugin < C2 |
| `explain <código>` | el diagnóstico completo de una regla (`P-27`, `C1-DEPS`, …) | 2 si el código no existe |
| `graph [--format=mermaid\|json]` | grafo del sistema derivado de los manifests: nodos con arquetipo + edges `depends_on` y `event:*` | 0 |
| `create plugin <id> --archetype <a>` | scaffold completo que **nace C2** + corre el TCK del recién nacido + imprime próximos pasos | 1 si no nace C2 · 2 input inválido |

Ejemplo de sesión:

```
$ uv run python -m src.sdk.cli create plugin reviews --archetype full_stack
create: 10 archivos generados:
  + frontend_dashboard/src/plugins/reviews/plugin.yaml
  + hubara_agency/src/plugins/reviews/api/__init__.py
  + hubara_agency/src/plugins/reviews/domain/logic.py
  + hubara_agency/tests/conformance/test_reviews_conformance.py
  ...
TCK del recién nacido: nivel C2 ✓ (nace certificado)
```

## Cómo funciona

- **`create` genera DESDE los perfiles** (INV-5): manifest con `archetype:`,
  api delgada + `domain/` puro + test de dominio + Page con PluginHost (si
  full_stack) + **el archivo TCK** — todo lo que el perfil exige y nada más.
- **El golden test** (`test_cli_scaffold_golden.py`) scaffoldea cada template
  en un repo-skeleton tmp y exige `C2` con **cero warnings** en CI: si un
  template y el TCK divergen, lo caza el gate, no un usuario.
- Inputs inválidos fallan ANTES de escribir a disco (id con guion, colisión).
- v1 scaffoldea `api_only` y `full_stack` completos. Los arquetipos con
  workers (`agentic`/`notifier`/`sync`) fallan EXPLÍCITO con guía
  (F-SDK-3b): mejor un error claro que un worker a medias que rompa la
  paridad k8s del premortem.

## Integración con el pipeline

Los skills reemplazan pasos de receta por:

- implementer §0.5 (bearings): `... cli check` como smoke estructural;
- pre-PR: `... cli certify` y leer el JSON en vez de re-derivar;
- plugin nuevo: `... cli create plugin` y completar dominio.

## Extensión (regla de oro)

Verbo nuevo ⇒ doc acá + (si genera código) su golden + (si verifica) sus
checks en el TestKit — el CLI no implementa reglas propias: SIEMPRE delega en
`src/sdk/testkit/` (una fuente, tres frontends).
