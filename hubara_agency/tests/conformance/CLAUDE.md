# tests/conformance — contexto para agentes (los TCK instanciados)

> Se carga ADEMÁS de los CLAUDE.md raíz/backend cuando trabajás acá.
> Doc completo: `docs/_sdk/04-testkit-certificacion.md`.

## Qué es esta capa

Un archivo de **3 líneas por plugin** que instancia la suite de conformance
del SDK (`src/sdk/testkit/`). El gate P-27 exige que exista uno por cada
manifest del repo — "sin tests de arquitectura, el plugin no compila".

## Reglas duras

1. **NUNCA agregues checks acá.** Los checks viven en
   `src/sdk/testkit/checks.py` (+ su diagnóstico + su caso negativo en
   `tests/architecture/test_testkit_selftest.py`). Estos archivos solo
   instancian — así un check nuevo upgradea a TODOS los plugins (L-3).
2. **Plugin nuevo** ⇒ `hubara create plugin` lo genera; a mano:
   ```python
   from src.sdk.testkit import conformance_suite

   globals().update(conformance_suite("<id>"))
   ```
3. **Plugin renombrado/borrado** ⇒ renombrá/borrá su archivo TCK (el test
   `test_p27_no_orphan_conformance_files` caza zombies).
4. Los tests de DOMINIO del plugin van en `tests/plugins/<id>/` — acá solo
   arquitectura.
5. Si un TCK falla, el mensaje trae el diagnóstico completo (código + fix).
   El fix se aplica EN EL PLUGIN (o, si el perfil quedó viejo, en
   `src/sdk/testkit/archetypes.py` con ADR) — jamás "aflojando" el check.

## Verificar

```bash
cd hubara_agency && uv run pytest tests/conformance -q          # los 7 TCK
cd hubara_agency && uv run python -m src.sdk.cli certify --all  # + reportes JSON
```
