"""Códigos de la tienda que el bot reconoce (forge, 2026-10-09).

* ``sku_prefix``: el prefijo de los SKU de SU catálogo en Medusa (``HUB-``).
  Con él se lee el botón de la web (``ref: HUB-…``) y el código que se ve en
  una foto.
* ``web_domain``: lo que tiene que decir un enlace para ser de SU tienda (una
  captura de otra tienda puede traer un handle igual a uno nuestro).

Nacen en Terraform (``tenants.<t>.store`` → SSM → ``.env``:
``STORE_SKU_PREFIX``, ``STORE_WEB_DOMAIN``); sin variable, los de hoy.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class StoreCodes:
    sku_prefix: str = "HUB-"
    web_domain: str = "hubara"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> StoreCodes:
        base = cls()
        prefix = (env.get("STORE_SKU_PREFIX") or "").strip().upper() or base.sku_prefix
        domain = (env.get("STORE_WEB_DOMAIN") or "").strip().lower() or base.web_domain
        return cls(sku_prefix=prefix, web_domain=domain)


#: Los códigos de la tienda de este proceso.
CODES = StoreCodes.from_env(os.environ)
