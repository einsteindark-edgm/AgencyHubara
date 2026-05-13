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
    port = int(os.environ.get("UVICORN_PORT", "8000"))
    uvicorn.run("src.main:app", host="127.0.0.1", port=port, reload=True)
