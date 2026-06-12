"""TCK del plugin ``ads`` — instanciado, no copiado (P-27, F-SDK-2).

Estas 3 líneas generan ~12 tests parametrizados (C0/C1 + P-rules + perfil del
arquetipo). Un check nuevo en ``src/sdk/testkit/`` upgradea este plugin
AUTOMÁTICAMENTE — no editar este archivo para "agregar checks" (regla L-3:
nada de listas a mano). Los tests de DOMINIO del plugin viven aparte, en
``tests/plugins/ads/``.
"""
from src.sdk.testkit import conformance_suite

globals().update(conformance_suite("ads"))
