"""TCK del plugin order_sentinel (P-27): 3 líneas — un check nuevo en
src/sdk/testkit/ upgradea este suite automáticamente. NO agregar checks acá."""
from src.sdk.testkit import conformance_suite

globals().update(conformance_suite("order_sentinel"))
