from __future__ import annotations

import os
import sys
from pathlib import Path

import nbtlib

from enderterm.datapack import ensure_datapack_skeleton
from enderterm.minecraft_jar import save_configured_minecraft_jar_path, validate_minecraft_client_jar
from enderterm.nbttool import DEFAULT_PARAM_PATH, find_minecraft_client_jar, main as enderterm_main


def _demo_pack_dir() -> Path:
    # Keep this alongside the existing per-user params.json so the app remains
    # self-contained (no bundled Minecraft jar required).
    return DEFAULT_PARAM_PATH.parent / "demo-pack"


def _demo_structure_file() -> nbtlib.File:
    """Return a tiny valid structure file used for demo-pack bootstrap."""
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound](
                [nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")})]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)}
                    )
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    return nbtlib.File(root)


def _ensure_demo_pack() -> Path:
    demo_dir = _demo_pack_dir()
    ensure_datapack_skeleton(demo_dir, description="EnderTerm demo pack")

    nbt_path = demo_dir / "data" / "enderterm" / "structures" / "demo.nbt"
    if not nbt_path.is_file():
        nbt_path.parent.mkdir(parents=True, exist_ok=True)
        _demo_structure_file().save(nbt_path, gzipped=True)  # type: ignore[arg-type]

    return demo_dir


def _default_launch_args() -> list[str]:
    # If the user has a Minecraft jar installed (or set via $MINECRAFT_JAR),
    # use it by default. Otherwise, launch into a bundled demo datapack so the
    # app always opens for beta testers.
    jar = find_minecraft_client_jar()
    if jar is not None and jar.is_file():
        return ["datapack-view"]

    demo_dir = _ensure_demo_pack()
    # Run untextured (unless the user later sets $MINECRAFT_JAR).
    return ["datapack-view", str(demo_dir)]


def _try_configure_minecraft_jar_from_arg(raw: object) -> bool:
    value = str(raw)
    if not value or value.startswith("-"):
        return False
    path = Path(value).expanduser()
    if path.suffix.lower() != ".jar":
        return False
    if validate_minecraft_client_jar(path) is not None:
        return False
    save_configured_minecraft_jar_path(path)
    os.environ["MINECRAFT_JAR"] = str(path)
    return True


def main(argv: list[str] | None = None) -> int:
    if argv is not None:
        return int(enderterm_main(argv))

    # When launched by dropping a .jar onto the app icon, macOS can pass the
    # file path as an argv argument (PyInstaller's --argv-emulation helps).
    for raw in sys.argv[1:]:
        if _try_configure_minecraft_jar_from_arg(raw):
            break

    return int(enderterm_main(_default_launch_args()))


if __name__ == "__main__":
    raise SystemExit(main())
