"""`hubara create plugin` debe escribir DENTRO del monorepo desde cualquier checkout
(también un worktree bajo `.claude/worktrees/<x>/`)."""
from src.sdk.cli.scaffold import default_repo_root


def test_default_repo_root_is_the_monorepo_root() -> None:
    root = default_repo_root()
    assert (root / "hubara_agency" / "src" / "sdk" / "cli").is_dir()
    assert (root / "frontend_dashboard" / "src" / "plugins").is_dir()
