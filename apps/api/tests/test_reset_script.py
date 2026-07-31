import os
import stat
import subprocess
from pathlib import Path

RESET_SCRIPT = Path("packaging/bin/reset-data").resolve()


def run_reset(
    *args: str, data_dir: str | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if data_dir is None:
        environment.pop("MAFIA_DATA_DIR", None)
    else:
        environment["MAFIA_DATA_DIR"] = data_dir

    return subprocess.run(
        [str(RESET_SCRIPT), *args],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_reset_requires_explicit_confirmation(tmp_path: Path) -> None:
    result = run_reset(data_dir=str(tmp_path / "data"))

    assert result.returncode == 2
    assert "--confirm-destructive-reset" in result.stderr


def test_reset_requires_explicit_data_directory() -> None:
    result = run_reset("--confirm-destructive-reset")

    assert result.returncode != 0
    assert "MAFIA_DATA_DIR" in result.stderr


def test_reset_rejects_normalized_root_current_and_parent_directories(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()

    for unsafe_target in ("/", ".", "./child/..", "..", "child/../.."):
        result = run_reset("--confirm-destructive-reset", data_dir=unsafe_target, cwd=current)

        assert result.returncode == 2
        assert "Refusing unsafe MAFIA_DATA_DIR" in result.stderr


def test_reset_rejects_symlinked_runtime_directory(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    marker = protected / "marker"
    marker.write_text("do not delete")
    data_dir = tmp_path / "data"
    data_dir.symlink_to(protected, target_is_directory=True)

    result = run_reset("--confirm-destructive-reset", data_dir=str(data_dir))

    assert result.returncode == 2
    assert marker.read_text() == "do not delete"
    assert data_dir.is_symlink()


def test_reset_recreates_only_the_requested_runtime_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "runtime"
    data_dir.mkdir(mode=0o700)
    stale_file = data_dir / "stale"
    stale_file.write_text("delete me")
    sibling = tmp_path / "keep"
    sibling.write_text("preserve me")

    result = run_reset("--confirm-destructive-reset", data_dir=str(data_dir))

    assert result.returncode == 0
    assert data_dir.is_dir()
    assert not stale_file.exists()
    assert sibling.read_text() == "preserve me"
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o750
