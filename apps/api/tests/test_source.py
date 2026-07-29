from pathlib import Path

import pytest
from mafia.services.source import SourcePathError, SourceReader, resolve_in_root


def test_reader_lists_reads_and_searches(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("first\nneedle here\n", encoding="utf-8")
    reader = SourceReader(tmp_path)
    assert reader.list_source("src") == ["src/app.py"]
    assert reader.read_source("src/app.py", 2, 2) == "2: needle here"
    assert reader.search_source("needle") == [{"path": "src/app.py", "line": 2, "text": "needle here"}]
    assert reader.activity.snapshot() == {
        "files_inspected": ["src", "src/app.py"],
        "files_inspected_count": 2,
        "searches": 1,
        "search_matches": 1,
        "recent_searches": ["needle"],
    }


def test_reader_normalizes_a_relative_repository_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "go.mod").write_text("module example.com/test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    reader = SourceReader(Path("repository"))

    assert reader.root == repository
    assert reader.list_source() == ["go.mod"]


def test_rejects_path_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(SourcePathError):
        resolve_in_root(tmp_path, "../outside.txt")


def test_rejects_escaping_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SourcePathError):
        resolve_in_root(tmp_path, "link/secret.txt")
