"""LabKit — el laboratorio de conversaciones, para plugins.

Fachada SDK (P-28/P-3) sobre `src.platform.lab`: el almacén del laboratorio
(`bench/`, `orders/`, `runs/` en el S3 privado, o un directorio en
desarrollo) y el lanzador de la caja (prenderla y darle órdenes por SSM; el
backend NUNCA se conecta a la caja). Plan: LABORATORIO_CONVERSACIONES_PLAN.md
§3.4 y §3.7.

Uso canónico (activity del worker `sales_eval`)::

    from src.sdk.labkit import get_lab_launcher, get_lab_store

    store = get_lab_store()            # None = laboratorio apagado
    store.put_file("bench/b1/vault/…/metadata.json", path)
    launcher = get_lab_launcher()
    launcher.start_box()
    launcher.dispatch(run_id, image)   # "dispatched" | "already_dispatched"
"""
from __future__ import annotations

from src.platform.lab.composition import (
    get_lab_launcher as get_lab_launcher,
)
from src.platform.lab.composition import (
    get_lab_store as get_lab_store,
)
from src.platform.lab.launcher import (
    IMAGE_RE as IMAGE_RE,
)
from src.platform.lab.launcher import (
    RUN_ID_RE as RUN_ID_RE,
)
from src.platform.lab.launcher import (
    Boto3LabLauncher as Boto3LabLauncher,
)
from src.platform.lab.launcher import (
    LabBoxLauncher as LabBoxLauncher,
)
from src.platform.lab.store import (
    FilesystemLabStore as FilesystemLabStore,
)
from src.platform.lab.store import (
    LabStorePort as LabStorePort,
)
from src.platform.lab.store import (
    S3LabStore as S3LabStore,
)
