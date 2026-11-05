"""TCK del plugin ``costs`` — instanciado, no copiado (P-27, F-SDK-2).

Generado por `hubara create plugin`. NO agregar checks acá (viven en
src/sdk/testkit/ — L-3); los tests de dominio van en tests/plugins/costs/.
"""
from src.sdk.testkit import conformance_suite

globals().update(conformance_suite("costs"))
