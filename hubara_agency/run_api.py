import os
from collections.abc import Mapping

import uvicorn


def uvicorn_options(env: Mapping[str, str]) -> dict:
    """Cómo arranca uvicorn.

    UVICORN_PORT override: el frontend pipeline elige un puerto random para
    evitar choques cuando varios pipelines corren en paralelo. Si no está
    seteado, default 8000 para compat dev local.

    host=0.0.0.0 es OBLIGATORIO para Docker: si bindeamos a 127.0.0.1 el port
    mapping del compose forwardea al bridge del container (172.18.x.x) pero
    uvicorn solo escucha en el loopback del container → ERR_EMPTY_RESPONSE.
    En host dev también funciona — el pipeline curl-ea 127.0.0.1:$PORT y
    0.0.0.0 incluye 127.0.0.1.

    Sin `reload` salvo que se pida (`UVICORN_RELOAD=1`, para editar en el
    host): en la imagen el código no cambia, y `reload` corría un supervisor que
    vigila archivos más un hijo `multiprocessing.spawn` — memoria y CPU en la
    caja de 4 GB sin ganancia, y una capa más entre el SIGTERM del deploy y el
    apagado ordenado de la API (ADR 2026-10-15).
    """
    return {
        "host": "0.0.0.0",
        "port": int(env.get("UVICORN_PORT", "8000")),
        "reload": env.get("UVICORN_RELOAD", "") == "1",
    }


if __name__ == "__main__":
    # Arranca Uvicorn programáticamente (con `reload`, esto resuelve el bug de
    # pérdida de paths — ModuleNotFoundError — que ocurre cuando Uvicorn crea
    # subprocesos para el "reload" en macOS/Anaconda).
    uvicorn.run("src.main:app", **uvicorn_options(os.environ))
