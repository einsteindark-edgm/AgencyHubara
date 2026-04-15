import uvicorn

if __name__ == "__main__":
    # Arranca Uvicorn programáticamente. 
    # Esto resuelve el bug de pérdida de paths (ModuleNotFoundError) que ocurre
    # cuando Uvicorn intenta crear subprocesos para el "reload" en macOS/Anaconda.
    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=True)
