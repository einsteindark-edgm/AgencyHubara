import os
import uvicorn

if __name__ == "__main__":
    # Arranca Uvicorn programáticamente.
    # Esto resuelve el bug de pérdida de paths (ModuleNotFoundError) que ocurre
    # cuando Uvicorn intenta crear subprocesos para el "reload" en macOS/Anaconda.
    #
    # UVICORN_PORT override: el frontend pipeline elige un puerto random para
    # evitar choques cuando varios pipelines corren en paralelo. Si no está
    # seteado, default 8000 para compat dev local.
    # host=0.0.0.0 es OBLIGATORIO para Docker: si bindeamos a 127.0.0.1 el port
    # mapping del compose forwardea al bridge del container (172.18.x.x) pero
    # uvicorn solo escucha en el loopback del container → ERR_EMPTY_RESPONSE.
    # En host dev también funciona — el pipeline cur lea 127.0.0.1:$PORT y
    # 0.0.0.0 incluye 127.0.0.1.
    port = int(os.environ.get("UVICORN_PORT", "8000"))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=True)
