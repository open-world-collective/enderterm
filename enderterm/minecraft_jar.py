from __future__ import annotations

import zipfile
from pathlib import Path

from enderterm import params as params_mod

MINECRAFT_JAR_PATH_FILE = params_mod.DEFAULT_PARAM_PATH.parent / "minecraft_jar.txt"
_REQUIRED_CLIENT_ASSET_PREFIXES = (
    "assets/minecraft/",
    "assets/minecraft/blockstates/",
    "assets/minecraft/models/",
    "assets/minecraft/textures/",
)


def _normalize_minecraft_jar_path(raw: object, *, base_dir: Path) -> Path | None:
    """Best-effort normalization for persisted jar paths."""
    try:
        if isinstance(raw, str):
            text = str(raw).strip()
            if not text:
                return None
            p = Path(text).expanduser()
        else:
            p = Path(raw).expanduser()  # type: ignore[arg-type]
            if not str(p).strip():
                return None
        if not p.is_absolute():
            p = Path(base_dir) / p
        return p.resolve(strict=False)
    except Exception:
        return None


def _read_configured_minecraft_jar_path_text(cfg_path: Path) -> str | None:
    """Return the first non-empty persisted path line, if available."""
    try:
        raw_text = cfg_path.read_text(encoding="utf-8")
    except Exception:
        return None

    for line in str(raw_text).splitlines():
        text = str(line).lstrip("\ufeff").strip()
        if text:
            return text
    return None


def _normalize_validation_path(raw: object) -> tuple[Path | None, str | None]:
    """Normalize a validation input path into an absolute resolved path."""
    if isinstance(raw, str) and not str(raw).strip():
        return (None, "Invalid path: empty path")
    try:
        p = Path(raw).expanduser()  # type: ignore[arg-type]
    except Exception as e:
        return (None, f"Invalid path: {type(e).__name__}: {e}")
    try:
        return (p.resolve(strict=False), None)
    except Exception as e:
        return (None, f"Invalid path: {type(e).__name__}: {e}")


def load_configured_minecraft_jar_path() -> Path | None:
    cfg_path = MINECRAFT_JAR_PATH_FILE
    try:
        if not cfg_path.is_file():
            return None
    except Exception:
        return None
    raw = _read_configured_minecraft_jar_path_text(cfg_path)
    if raw is None:
        return None
    return _normalize_minecraft_jar_path(raw, base_dir=cfg_path.parent)


def save_configured_minecraft_jar_path(path: Path | None) -> None:
    cfg_path = MINECRAFT_JAR_PATH_FILE
    try:
        if path is None:
            if cfg_path.is_file():
                cfg_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            return
        p = _normalize_minecraft_jar_path(path, base_dir=Path.cwd())
        if p is None:
            return
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(str(p) + "\n", encoding="utf-8")
    except Exception:
        # Best-effort persistence; failure shouldn't break the app.
        return


def _read_jar_member_names(path: Path) -> list[str]:
    """Return all archive member names from a jar/zip file."""
    with zipfile.ZipFile(path, "r") as zf:
        return zf.namelist()


def validate_minecraft_client_jar(path: Path) -> str | None:
    """Return an error message if the given path doesn't look like a client jar."""
    p, path_err = _normalize_validation_path(path)
    if path_err is not None:
        return path_err
    assert p is not None
    if not p.is_file():
        return f"Not a file: {p}"
    if p.suffix.lower() != ".jar":
        return f"Not a .jar file: {p.name}"
    try:
        names = _read_jar_member_names(p)
    except Exception as e:
        return f"Not a valid zip/jar: {type(e).__name__}: {e}"

    # Heuristic: a Minecraft client jar should contain vanilla assets (textures/models/blockstates).
    has_required_assets = all(
        any(name.startswith(prefix) for name in names) for prefix in _REQUIRED_CLIENT_ASSET_PREFIXES
    )
    if not has_required_assets:
        return "Jar does not look like a Minecraft client jar (missing vanilla assets)."

    return None
