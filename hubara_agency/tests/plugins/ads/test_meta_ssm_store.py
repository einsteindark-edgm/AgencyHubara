"""Token store real — SSM Parameter Store (SecureString), con cliente inyectable."""
from __future__ import annotations

from src.plugins.ads.meta.token_store import MetaToken, SsmTokenStore


class _FakeSsm:
    class exceptions:
        class ParameterNotFound(Exception):
            pass

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.last_put_type: str | None = None

    def put_parameter(self, Name: str, Value: str, Type: str, Overwrite: bool):  # noqa: N803
        self.store[Name] = Value
        self.last_put_type = Type
        return {}

    def get_parameter(self, Name: str, WithDecryption: bool):  # noqa: N803
        if Name not in self.store:
            raise self.exceptions.ParameterNotFound()
        return {"Parameter": {"Value": self.store[Name]}}

    def delete_parameter(self, Name: str):  # noqa: N803
        self.store.pop(Name, None)
        return {}


def _store() -> tuple[SsmTokenStore, _FakeSsm]:
    fake = _FakeSsm()
    return SsmTokenStore("/hubara/hubara/meta/oauth", client_factory=lambda: fake), fake


def _tok() -> MetaToken:
    return MetaToken("EAA-x", 1782842400, ("ads_read", "ads_management"), "act_1", "Hubara")


def test_load_is_none_when_parameter_absent() -> None:
    store, _ = _store()
    assert store.load() is None


def test_save_uses_securestring_and_roundtrips() -> None:
    store, fake = _store()
    store.save(_tok())
    assert fake.last_put_type == "SecureString"  # nunca String plano
    loaded = store.load()
    assert loaded == _tok()


def test_clear_deletes_parameter() -> None:
    store, _ = _store()
    store.save(_tok())
    store.clear()
    assert store.load() is None
