"""Plataforma del laboratorio de conversaciones (plan, PR 7): almacén y lanzador.

* `LabStorePort`: el banco (`bench/`), las órdenes (`orders/`) y los resultados
  (`runs/`). Dos adaptadores con el MISMO contrato: S3 (producción y caja) y
  disco (tests y desarrollo). Sube ARCHIVO POR ARCHIVO sin cargarlo en memoria:
  la caja de producción tiene ~420 MB libres (plan §3.7).
* `Boto3LabLauncher`: prende la caja (tag `Role=lab`) y le da órdenes por SSM,
  sin red entre cajas. Reutiliza el lanzador de GraphAgents.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.lab.launcher import Boto3LabLauncher
from src.platform.lab.store import FilesystemLabStore, S3LabStore
from tests.plugins.ads.test_analysis_boto3_launcher import _FakeEC2, _FakeSSM


class FakeS3:
    """Cliente S3 falso con la API de boto3 que usa el adaptador."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[tuple[str, str]] = []

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):  # noqa: N803
        self.uploads.append((Filename, Key))
        self.objects[Key] = Path(Filename).read_bytes()

    def put_object(self, *, Bucket, Key, Body, **_):  # noqa: N803
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        body = self.objects[Key]

        class _Body:
            def read(self_inner):
                return body

        return {"Body": _Body()}

    def get_paginator(self, name):
        objects = self.objects

        class _P:
            def paginate(self, *, Bucket, Prefix):  # noqa: N803
                keys = sorted(k for k in objects if k.startswith(Prefix))
                yield {"Contents": [{"Key": k} for k in keys]} if keys else {}

        return _P()


@pytest.fixture(params=["fs", "s3"])
def store(request, tmp_path: Path):
    if request.param == "fs":
        return FilesystemLabStore(tmp_path / "bucket")
    return S3LabStore("agencyhubara-lab-000000000000", client=FakeS3())


def test_put_file_and_read_back(store, tmp_path: Path) -> None:
    src = tmp_path / "metadata.json"
    src.write_text('{"episodes": []}', encoding="utf-8")

    store.put_file("bench/b1/vault/wa_573001234567/metadata.json", src)

    assert store.get_bytes("bench/b1/vault/wa_573001234567/metadata.json") == b'{"episodes": []}'


def test_put_bytes_list_and_missing(store) -> None:
    store.put_bytes("runs/r1/progress.json", b"{}")
    store.put_bytes("runs/r1/summary.json", b"{}")
    store.put_bytes("runs/r2/progress.json", b"{}")

    assert store.list_keys("runs/r1/") == ["runs/r1/progress.json", "runs/r1/summary.json"]
    assert store.get_bytes("runs/r9/progress.json") is None


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs/key", "bench/../../x", "", "bench//x"])
def test_keys_cannot_escape_the_bucket_or_the_root(store, bad: str) -> None:
    with pytest.raises(ValueError):
        store.put_bytes(bad, b"x")


def test_s3_uploads_file_by_file_without_reading_them_into_memory(tmp_path: Path) -> None:
    client = FakeS3()
    src = tmp_path / "turn_traces.jsonl"
    src.write_text("{}\n", encoding="utf-8")

    S3LabStore("b", client=client).put_file("bench/b1/x.jsonl", src)

    assert client.uploads == [(str(src), "bench/b1/x.jsonl")]


# ── Lanzador ────────────────────────────────────────────────────────────────


@pytest.fixture
def aws(monkeypatch):
    state = {"ec2": _FakeEC2(state="stopped"), "ssm": _FakeSSM(stdout="…\ndispatched\n")}
    monkeypatch.setattr(Boto3LabLauncher, "_clients", lambda self: (state["ec2"], state["ssm"]))
    return state


def test_launcher_finds_the_lab_box_by_its_tag_and_starts_it(aws) -> None:
    Boto3LabLauncher(region="us-east-1").start_box()

    assert aws["ec2"].described[0] == [{"Name": "tag:Role", "Values": ["lab"]}]
    assert aws["ec2"].started == [["i-abc"]]
    ready = aws["ssm"].sent[-1]["Parameters"]["commands"][0]
    assert "/opt/lab/dispatch.sh" in ready


def test_dispatch_sends_the_order_and_returns_the_box_answer(aws) -> None:
    answer = Boto3LabLauncher(region="us-east-1").dispatch("run-20260923-a1b2", "ghcr.io/o/agencyhubara:abc")

    assert answer == "dispatched"
    cmd = aws["ssm"].sent[-1]
    assert cmd["DocumentName"] == "AWS-RunShellScript"
    assert cmd["Parameters"]["commands"] == ["/opt/lab/dispatch.sh 'run-20260923-a1b2' 'ghcr.io/o/agencyhubara:abc'"]


def test_cancel_sends_the_cancel_script(aws) -> None:
    aws["ssm"] = _FakeSSM(stdout="cancel_requested\n")

    assert Boto3LabLauncher(region="us-east-1").cancel("run-20260923-a1b2") == "cancel_requested"
    assert aws["ssm"].sent[-1]["Parameters"]["commands"] == ["/opt/lab/cancel.sh 'run-20260923-a1b2'"]


@pytest.mark.parametrize("run_id", ["x", "run;reboot", "../run-123456"])
def test_launcher_rejects_bad_run_ids_before_touching_aws(aws, run_id: str) -> None:
    with pytest.raises(ValueError):
        Boto3LabLauncher(region="us-east-1").dispatch(run_id, "ghcr.io/o/agencyhubara:abc")
    assert aws["ssm"].sent == []


def test_labkit_reexports_the_platform_lab() -> None:
    import src.platform.lab.composition as comp
    import src.platform.lab.launcher as launcher
    import src.platform.lab.store as store_mod
    import src.sdk.labkit as kit

    assert kit.LabStorePort is store_mod.LabStorePort
    assert kit.S3LabStore is store_mod.S3LabStore
    assert kit.FilesystemLabStore is store_mod.FilesystemLabStore
    assert kit.Boto3LabLauncher is launcher.Boto3LabLauncher
    assert kit.get_lab_store is comp.get_lab_store


def test_lab_store_composition(monkeypatch, tmp_path: Path) -> None:
    from src.platform.lab import composition

    composition.get_lab_store.cache_clear()
    monkeypatch.setenv("LAB_BUCKET", "PLACEHOLDER_set_out_of_band")
    monkeypatch.delenv("LAB_STORE_DIR", raising=False)
    assert composition.get_lab_store() is None

    composition.get_lab_store.cache_clear()
    monkeypatch.setenv("LAB_STORE_DIR", str(tmp_path))
    assert isinstance(composition.get_lab_store(), FilesystemLabStore)

    composition.get_lab_store.cache_clear()
    monkeypatch.setenv("LAB_BUCKET", "agencyhubara-lab-000000000000")
    assert isinstance(composition.get_lab_store(), S3LabStore)
    composition.get_lab_store.cache_clear()


@pytest.mark.parametrize(("run_id", "image"), [
    ("run-20260923-a1b2\n", "ghcr.io/o/agencyhubara:abc"),
    ("run-20260923-a1b2", "ghcr.io/o/agencyhubara:abc\n"),
])
def test_the_launcher_refuses_ids_with_a_trailing_newline(run_id: str, image: str) -> None:
    """`re.match` con `$` acepta un salto de línea al final: la orden llegaría
    a la caja, que la rechaza (bash `=~`) y la corrida falla más tarde y peor."""
    from src.platform.lab.launcher import Boto3LabLauncher

    with pytest.raises(ValueError):
        Boto3LabLauncher(region="us-east-1").dispatch(run_id, image)


def test_list_children_gives_the_immediate_folders_of_a_prefix(tmp_path: Path) -> None:
    """Listar las corridas no puede recorrer cada objeto (unos 20 por
    conversación por corrida): basta con los nombres de las carpetas."""
    store = FilesystemLabStore(tmp_path)
    for key in ("runs/run-a/progress.json", "runs/run-a/threads/x.json", "runs/run-b/manifest.json", "bench/x/manifest.json"):
        store.put_bytes(key, b"{}")

    assert store.list_children("runs/") == ["run-a", "run-b"]
    assert store.list_children("nada/") == []


def test_s3_lists_children_with_a_delimiter() -> None:
    from types import SimpleNamespace

    class Pages:
        kwargs: dict = {}

        def paginate(self, **kwargs):
            Pages.kwargs = kwargs
            return [{"CommonPrefixes": [{"Prefix": "runs/run-b/"}, {"Prefix": "runs/run-a/"}]}]

    store = S3LabStore("bucket", client=SimpleNamespace(get_paginator=lambda _name: Pages()))

    assert store.list_children("runs/") == ["run-a", "run-b"]
    assert Pages.kwargs["Delimiter"] == "/" and Pages.kwargs["Prefix"] == "runs/"
