from __future__ import annotations

from contextlib import contextmanager
import zipfile
from pathlib import Path

from enderterm import minecraft_jar as jar_mod


@contextmanager
def _configured_jar_path_file(path: Path):
    orig = jar_mod.MINECRAFT_JAR_PATH_FILE
    jar_mod.MINECRAFT_JAR_PATH_FILE = path
    try:
        yield path
    finally:
        jar_mod.MINECRAFT_JAR_PATH_FILE = orig


def test_save_and_load_configured_minecraft_jar_path(tmp_path: Path) -> None:
    cfg = tmp_path / "minecraft_jar.txt"

    with _configured_jar_path_file(cfg):
        assert jar_mod.load_configured_minecraft_jar_path() is None
        jar_path = tmp_path / "1.20.1.jar"
        jar_mod.save_configured_minecraft_jar_path(jar_path)
        assert jar_mod.load_configured_minecraft_jar_path() == jar_path

        jar_mod.save_configured_minecraft_jar_path(None)
        assert jar_mod.load_configured_minecraft_jar_path() is None


def test_load_configured_minecraft_jar_path_missing_file_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / "missing.txt"
    with _configured_jar_path_file(cfg):
        assert jar_mod.load_configured_minecraft_jar_path() is None


def test_load_configured_minecraft_jar_path_empty_and_whitespace_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / "minecraft_jar.txt"
    with _configured_jar_path_file(cfg):
        cfg.write_text("", encoding="utf-8")
        assert jar_mod.load_configured_minecraft_jar_path() is None
        cfg.write_text("   \n\t", encoding="utf-8")
        assert jar_mod.load_configured_minecraft_jar_path() is None


def test_load_configured_minecraft_jar_path_uses_first_non_empty_line(tmp_path: Path) -> None:
    cfg = tmp_path / "minecraft_jar.txt"
    with _configured_jar_path_file(cfg):
        cfg.write_text("\n  ./first/client.jar \n ./second/client.jar\n", encoding="utf-8")
        assert jar_mod.load_configured_minecraft_jar_path() == (tmp_path / "first/client.jar").resolve(strict=False)


def test_load_configured_minecraft_jar_path_unreadable_is_best_effort(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "minecraft_jar.txt"
    cfg.write_text("/tmp/client.jar\n", encoding="utf-8")

    real_read_text = Path.read_text

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == cfg:
            raise PermissionError("denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    with _configured_jar_path_file(cfg):
        assert jar_mod.load_configured_minecraft_jar_path() is None


def test_load_configured_minecraft_jar_path_invalid_text_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / "minecraft_jar.txt"
    with _configured_jar_path_file(cfg):
        cfg.write_text("\x00bad-path\n", encoding="utf-8")
        assert jar_mod.load_configured_minecraft_jar_path() is None


def test_save_configured_minecraft_jar_path_normalizes_relative_for_determinism(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "minecraft_jar.txt"
    monkeypatch.chdir(tmp_path)

    with _configured_jar_path_file(cfg):
        rel = Path("relative/client.jar")
        jar_mod.save_configured_minecraft_jar_path(rel)
        loaded = jar_mod.load_configured_minecraft_jar_path()
        assert loaded == (tmp_path / "relative/client.jar").resolve(strict=False)


def test_save_configured_minecraft_jar_path_invalid_input_preserves_previous_value(tmp_path: Path) -> None:
    cfg = tmp_path / "minecraft_jar.txt"
    jar_path = (tmp_path / "client.jar").resolve(strict=False)

    class _BadPath:
        def __fspath__(self) -> str:
            raise ValueError("boom")

    with _configured_jar_path_file(cfg):
        jar_mod.save_configured_minecraft_jar_path(jar_path)
        assert jar_mod.load_configured_minecraft_jar_path() == jar_path

        jar_mod.save_configured_minecraft_jar_path(_BadPath())  # type: ignore[arg-type]
        assert jar_mod.load_configured_minecraft_jar_path() == jar_path


def test_validate_minecraft_client_jar_invalid_path_object() -> None:
    class _BadPath:
        def __fspath__(self) -> str:
            raise ValueError("boom")

    err = jar_mod.validate_minecraft_client_jar(_BadPath())  # type: ignore[arg-type]
    assert err is not None
    assert str(err).startswith("Invalid path:")


def test_validate_minecraft_client_jar_empty_and_whitespace_paths() -> None:
    assert jar_mod.validate_minecraft_client_jar(Path("   ")) is not None
    assert str(jar_mod.validate_minecraft_client_jar("")).startswith("Invalid path: empty path")  # type: ignore[arg-type]
    assert str(jar_mod.validate_minecraft_client_jar("   ")).startswith("Invalid path: empty path")  # type: ignore[arg-type]


def test_validate_minecraft_client_jar(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_jar.txt"
    bad.write_text("nope", encoding="utf-8")
    assert jar_mod.validate_minecraft_client_jar(bad) is not None

    jar_path = tmp_path / "client.jar"
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr("assets/minecraft/blockstates/stone.json", "{}")
        zf.writestr("assets/minecraft/models/block/stone.json", "{}")
        zf.writestr("assets/minecraft/textures/block/stone.png", b"")
    assert jar_mod.validate_minecraft_client_jar(jar_path) is None
