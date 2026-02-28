from __future__ import annotations

"""USDZ/USDA conversion + jar helpers (extracted from legacy nbttool_impl)."""

from collections.abc import Generator
from contextlib import contextmanager
from importlib import import_module as _import_module

# Persistent jar config (set via drag-drop in the viewer).
from enderterm.minecraft_jar import load_configured_minecraft_jar_path, validate_minecraft_client_jar

# Pull in shared helpers/constants/types from the main implementation without
# importing pyglet/OpenGL at module import time.
_impl = _import_module("enderterm.nbttool_impl")
for _k, _v in _impl.__dict__.items():
    if _k in {"__name__", "__loader__", "__package__", "__spec__", "__file__", "__cached__"}:
        continue
    globals().setdefault(_k, _v)


def _validated_existing_jar(path_obj: Path | None) -> Path | None:
    if path_obj is None:
        return None
    if path_obj.is_file() and validate_minecraft_client_jar(path_obj) is None:
        return path_obj
    return None


def _minecraft_versions_dirs_for_platform(*, platform: str, home: Path, appdata: str | None) -> list[Path]:
    versions_dirs: list[Path] = []
    if platform.startswith("darwin"):
        versions_dirs.append(home / "Library" / "Application Support" / "minecraft" / "versions")
    elif platform.startswith("win"):
        # Typical Minecraft Launcher install location.
        versions_dirs.append(home / "AppData" / "Roaming" / ".minecraft" / "versions")
        if appdata:
            versions_dirs.append(Path(appdata) / ".minecraft" / "versions")
    else:
        versions_dirs.append(home / ".minecraft" / "versions")
    return versions_dirs


def _preferred_version_jar(versions_dir: Path, preferred_version: str) -> Path | None:
    candidate = versions_dir / preferred_version / f"{preferred_version}.jar"
    if candidate.is_file():
        return candidate
    return None


def _latest_jar_in_versions_dir(versions_dir: Path) -> Path | None:
    if not versions_dir.is_dir():
        return None
    try:
        candidates = sorted(versions_dir.glob("*/*.jar"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        candidates = []
    if candidates:
        return candidates[0]
    return None


def find_minecraft_client_jar(preferred_version: str = "1.20.1") -> Path | None:
    env_path = os.environ.get("MINECRAFT_JAR")
    if env_path:
        env_candidate = _validated_existing_jar(Path(env_path).expanduser())
        if env_candidate is not None:
            return env_candidate

    configured_candidate = _validated_existing_jar(load_configured_minecraft_jar_path())
    if configured_candidate is not None:
        return configured_candidate

    platform = str(getattr(sys, "platform", "") or "").lower()
    home = Path.home()
    versions_dirs = _minecraft_versions_dirs_for_platform(
        platform=platform,
        home=home,
        appdata=os.environ.get("APPDATA"),
    )

    for versions_dir in versions_dirs:
        default_candidate = _preferred_version_jar(versions_dir, preferred_version)
        if default_candidate is not None:
            return default_candidate

    for versions_dir in versions_dirs:
        latest_candidate = _latest_jar_in_versions_dir(versions_dir)
        if latest_candidate is not None:
            return latest_candidate

    return None

@functools.lru_cache(maxsize=8192)
def _stable_rgb(block_id: str) -> tuple[float, float, float]:
    if block_id.startswith("minecraft:jigsaw"):
        return (0.803921568627, 0.0, 1.0)
    digest = hashlib.sha1(block_id.encode("utf-8")).digest()
    hue = int.from_bytes(digest[0:2], "big") / 65535.0
    saturation = 0.55 + (digest[2] / 255.0) * 0.35
    lightness = 0.45 + (digest[3] / 255.0) * 0.20
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return (r, g, b)


@functools.lru_cache(maxsize=8192)
def _material_name(block_id: str) -> str:
    digest = hashlib.sha1(block_id.encode("utf-8")).hexdigest()[:10]
    return f"mat_{digest}"


@functools.lru_cache(maxsize=8192)
def _material_name_prefixed(prefix: str, key: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{key}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _structure_center(structure: Structure) -> tuple[float, float, float]:
    if structure.blocks:
        it = iter(structure.blocks)
        b0 = next(it)
        min_x = max_x = b0.pos[0]
        min_y = max_y = b0.pos[1]
        min_z = max_z = b0.pos[2]
        for block in it:
            x, y, z = block.pos
            if x < min_x:
                min_x = x
            elif x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            elif y > max_y:
                max_y = y
            if z < min_z:
                min_z = z
            elif z > max_z:
                max_z = z
        return ((min_x + max_x + 1) / 2.0, (min_y + max_y + 1) / 2.0, (min_z + max_z + 1) / 2.0)

    sx, sy, sz = structure.size
    return (sx / 2.0, sy / 2.0, sz / 2.0)


def structure_to_usda_text(structure: Structure) -> str:
    center_x, center_y, center_z = _structure_center(structure)

    color_keys = sorted({b.color_key for b in structure.blocks})
    mat_by_key = {key: _material_name(key) for key in color_keys}

    lines: list[str] = []
    lines.append("#usda 1.0")
    lines.append("(")
    lines.append('    defaultPrim = "Root"')
    lines.append("    metersPerUnit = 1")
    lines.append('    upAxis = "Y"')
    lines.append(")")
    lines.append("")
    lines.append('def Xform "Root" {')

    lines.append('    def Scope "Materials" {')
    for color_key in color_keys:
        mat_name = mat_by_key[color_key]
        r, g, b = _stable_rgb(color_key)
        lines.append(f'        def Material "{mat_name}" {{')
        lines.append(
            f"            token outputs:surface.connect = </Root/Materials/{mat_name}/Shader.outputs:surface>"
        )
        lines.append('            def Shader "Shader" {')
        lines.append('                uniform token info:id = "UsdPreviewSurface"')
        lines.append(f"                color3f inputs:diffuseColor = ({r:.4f}, {g:.4f}, {b:.4f})")
        lines.append("                float inputs:roughness = 0.8")
        lines.append("                float inputs:metallic = 0")
        lines.append("                token outputs:surface")
        lines.append("            }")
        lines.append("        }")
    lines.append("    }")
    lines.append("")

    lines.append('    def Xform "Blocks" {')
    for idx, block in enumerate(structure.blocks):
        x, y, z = block.pos
        tx = x + 0.5 - center_x
        ty = y + 0.5 - center_y
        tz = z + 0.5 - center_z
        mat_name = mat_by_key[block.color_key]
        lines.append(f'        def Cube "b_{idx:06d}" (')
        lines.append('            prepend apiSchemas = ["MaterialBindingAPI"]')
        lines.append("        ) {")
        lines.append("            float size = 1")
        lines.append(f"            rel material:binding = </Root/Materials/{mat_name}>")
        lines.append(f"            double3 xformOp:translate = ({tx:.4f}, {ty:.4f}, {tz:.4f})")
        lines.append('            uniform token[] xformOpOrder = ["xformOp:translate"]')
        lines.append("        }")
    lines.append("    }")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _cube_prototype_mesh_usda(
    *,
    north_material_path: str,
    south_material_path: str,
    west_material_path: str,
    east_material_path: str,
    down_material_path: str,
    up_material_path: str,
    indent: str = "            ",
) -> list[str]:
    faces = list(FACE_DIRS)
    points: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    face_vertex_counts: list[int] = []
    face_vertex_indices: list[int] = []

    for face in faces:
        face_pts = _UNIT_CUBE_FACE_QUADS[face]
        base = len(points)
        points.extend(face_pts)
        uvs.extend(_cube_face_uv_quad(face))
        face_vertex_counts.append(4)
        face_vertex_indices.extend([base + 0, base + 1, base + 2, base + 3])

    lines: list[str] = []
    lines.append(f"{indent}uniform token subdivisionScheme = \"none\"")

    lines.append(f"{indent}point3f[] points = [")
    for i, (x, y, z) in enumerate(points):
        comma = "," if i < len(points) - 1 else ""
        lines.append(f"{indent}    ({x:.4f}, {y:.4f}, {z:.4f}){comma}")
    lines.append(f"{indent}]")

    lines.append(f"{indent}int[] faceVertexCounts = [")
    for i, c in enumerate(face_vertex_counts):
        comma = "," if i < len(face_vertex_counts) - 1 else ""
        lines.append(f"{indent}    {c}{comma}")
    lines.append(f"{indent}]")

    lines.append(f"{indent}int[] faceVertexIndices = [")
    per_line = 32
    for i in range(0, len(face_vertex_indices), per_line):
        chunk = face_vertex_indices[i : i + per_line]
        chunk_str = ", ".join(str(n) for n in chunk)
        comma = "," if i + per_line < len(face_vertex_indices) else ""
        lines.append(f"{indent}    {chunk_str}{comma}")
    lines.append(f"{indent}]")

    lines.append(f"{indent}texCoord2f[] primvars:st = [")
    for i, (u, v) in enumerate(uvs):
        comma = "," if i < len(uvs) - 1 else ""
        lines.append(f"{indent}    ({u:.4f}, {v:.4f}){comma}")
    # Author interpolation as metadata on the primvar (Quick Look expects this form).
    lines.append(f'{indent}] (interpolation = "vertex")')

    # Face ordering: north, south, west, east, down, up (0..5)
    def _subset(name: str, face_idx: int, material_path: str) -> None:
        lines.append(f'{indent}def GeomSubset "{name}" (')
        lines.append(f'{indent}    prepend apiSchemas = ["MaterialBindingAPI"]')
        lines.append(f"{indent}) {{")
        lines.append(f'{indent}    uniform token elementType = "face"')
        lines.append(f'{indent}    uniform token familyName = "materialBind"')
        lines.append(f"{indent}    int[] indices = [{face_idx}]")
        lines.append(f"{indent}    rel material:binding = <{material_path}>")
        lines.append(f"{indent}}}")

    _subset("North", 0, north_material_path)
    _subset("South", 1, south_material_path)
    _subset("West", 2, west_material_path)
    _subset("East", 3, east_material_path)
    _subset("Down", 4, down_material_path)
    _subset("Up", 5, up_material_path)

    return lines


def structure_to_usda_textured(structure: Structure, source: TextureSource) -> tuple[str, dict[str, bytes]]:
    center_x, center_y, center_z = _structure_center(structure)

    blocks_sorted = sorted(structure.blocks, key=lambda b: b.pos)
    resolver = MinecraftResourceResolver(source)
    texture_source_rel_by_packaged_path: dict[str, str] = {}
    proto_key_by_block: list[tuple[tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str]]] = []
    translations: list[tuple[float, float, float]] = []
    rotations: list[tuple[int, int]] = []

    for block in blocks_sorted:
        base_id = _block_id_base(block.block_id)
        force_color = base_id == "minecraft:jigsaw"
        appearance = None if force_color else resolver.resolve_block_appearance(block.block_id)

        def face_def(face: TextureFace) -> tuple[str, str]:
            if appearance is not None:
                jar_rel = appearance.face_texture_png_by_dir.get(face)
                if jar_rel:
                    packaged = jar_rel.removeprefix("assets/minecraft/")
                    texture_source_rel_by_packaged_path[packaged] = jar_rel
                    return ("tex", packaged)
            return ("col", block.color_key)

        north = face_def("north")
        south = face_def("south")
        west = face_def("west")
        east = face_def("east")
        down = face_def("down")
        up = face_def("up")
        proto_key_by_block.append((north, south, west, east, down, up))
        if appearance is None:
            rotations.append((0, 0))
        else:
            rotations.append((appearance.rotate_x_deg, appearance.rotate_y_deg))

        x, y, z = block.pos
        translations.append((x + 0.5 - center_x, y + 0.5 - center_y, z + 0.5 - center_z))

    proto_name_by_key: dict[
        tuple[tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str]], str
    ] = {}
    for pk in sorted(set(proto_key_by_block), key=lambda t: repr(t)):
        proto_name_by_key[pk] = _material_name_prefixed("proto", repr(pk))

    texture_bytes_by_packaged_path = {
        packaged: source.read(jar_rel) for packaged, jar_rel in texture_source_rel_by_packaged_path.items()
    }

    def _material_lines(name: str, face: tuple[str, str], indent: str) -> list[str]:
        kind, value = face
        lines: list[str] = []
        lines.append(f'{indent}def Material "{name}" {{')
        lines.append(f"{indent}    token outputs:surface.connect = <Preview.outputs:surface>")
        if kind == "tex":
            tex_path = value
            lines.append(f'{indent}    def Shader "Preview" {{')
            lines.append(f'{indent}        uniform token info:id = "UsdPreviewSurface"')
            lines.append(f"{indent}        color3f inputs:diffuseColor.connect = <../Tex.outputs:rgb>")
            lines.append(f"{indent}        float inputs:roughness = 0.8")
            lines.append(f"{indent}        float inputs:metallic = 0")
            lines.append(f"{indent}        token outputs:surface")
            lines.append(f"{indent}    }}")
            lines.append(f'{indent}    def Shader "Tex" {{')
            lines.append(f'{indent}        uniform token info:id = "UsdUVTexture"')
            lines.append(f"{indent}        asset inputs:file = @{tex_path}@")
            lines.append(f"{indent}        float2 inputs:st.connect = <../StReader.outputs:result>")
            lines.append(f'{indent}        token inputs:wrapS = "repeat"')
            lines.append(f'{indent}        token inputs:wrapT = "repeat"')
            lines.append(f"{indent}        float3 outputs:rgb")
            lines.append(f"{indent}    }}")
            lines.append(f'{indent}    def Shader "StReader" {{')
            lines.append(f'{indent}        uniform token info:id = "UsdPrimvarReader_float2"')
            lines.append(f'{indent}        token inputs:varname = "st"')
            lines.append(f"{indent}        float2 outputs:result")
            lines.append(f"{indent}    }}")
        else:
            r, g, b = _stable_rgb(value)
            lines.append(f'{indent}    def Shader "Preview" {{')
            lines.append(f'{indent}        uniform token info:id = "UsdPreviewSurface"')
            lines.append(f"{indent}        color3f inputs:diffuseColor = ({r:.4f}, {g:.4f}, {b:.4f})")
            lines.append(f"{indent}        float inputs:roughness = 0.8")
            lines.append(f"{indent}        float inputs:metallic = 0")
            lines.append(f"{indent}        token outputs:surface")
            lines.append(f"{indent}    }}")
        lines.append(f"{indent}}}")
        return lines

    prototypes_lines: list[str] = []
    prototypes_lines.append("#usda 1.0")
    prototypes_lines.append("")
    prototypes_lines.append('def Xform "Prototypes" {')
    for pk in sorted(proto_name_by_key.keys(), key=lambda t: repr(t)):
        proto_name = proto_name_by_key[pk]
        north, south, west, east, down, up = pk
        prototypes_lines.append(f'    def Xform "{proto_name}" {{')
        prototypes_lines.append('        def Mesh "Cube" {')
        prototypes_lines.append('            def Scope "Materials" {')
        prototypes_lines.extend(_material_lines("Up", up, indent="                "))
        prototypes_lines.extend(_material_lines("Down", down, indent="                "))
        prototypes_lines.extend(_material_lines("North", north, indent="                "))
        prototypes_lines.extend(_material_lines("South", south, indent="                "))
        prototypes_lines.extend(_material_lines("West", west, indent="                "))
        prototypes_lines.extend(_material_lines("East", east, indent="                "))
        prototypes_lines.append("            }")
        prototypes_lines.extend(
            _cube_prototype_mesh_usda(
                north_material_path="../Materials/North",
                south_material_path="../Materials/South",
                west_material_path="../Materials/West",
                east_material_path="../Materials/East",
                down_material_path="../Materials/Down",
                up_material_path="../Materials/Up",
                indent="            ",
            )
        )
        prototypes_lines.append("        }")
        prototypes_lines.append("    }")
    prototypes_lines.append("}")
    prototypes_lines.append("")

    scene_lines: list[str] = []
    scene_lines.append("#usda 1.0")
    scene_lines.append("(")
    scene_lines.append('    defaultPrim = "Root"')
    scene_lines.append("    metersPerUnit = 1")
    scene_lines.append('    upAxis = "Y"')
    scene_lines.append(")")
    scene_lines.append("")
    scene_lines.append('def Xform "Root" {')

    scene_lines.append('    def Xform "Blocks" {')
    for idx, (pk, (tx, ty, tz), (rx, ry)) in enumerate(zip(proto_key_by_block, translations, rotations, strict=True)):
        proto_name = proto_name_by_key[pk]
        scene_lines.append(f'        def Xform "b_{idx:06d}" (')
        scene_lines.append("            instanceable = true")
        scene_lines.append(f"            prepend references = @prototypes.usda@</Prototypes/{proto_name}>")
        scene_lines.append("        ) {")
        if rx or ry:
            # Minecraft model rotations are specified in a clockwise convention.
            # USD uses the standard right-hand rule, so negate to match.
            scene_lines.append(f"            double3 xformOp:rotateXYZ = ({-rx:.4f}, {-ry:.4f}, 0)")
        scene_lines.append(f"            double3 xformOp:translate = ({tx:.4f}, {ty:.4f}, {tz:.4f})")
        if rx or ry:
            scene_lines.append('            uniform token[] xformOpOrder = ["xformOp:rotateXYZ", "xformOp:translate"]')
        else:
            scene_lines.append('            uniform token[] xformOpOrder = ["xformOp:translate"]')
        scene_lines.append("        }")
    scene_lines.append("    }")

    scene_lines.append("}")
    scene_lines.append("")

    extra_files: dict[str, bytes] = {"prototypes.usda": "\n".join(prototypes_lines).encode("utf-8")}
    for packaged, data in texture_bytes_by_packaged_path.items():
        extra_files[packaged] = data

    return ("\n".join(scene_lines), extra_files)


def _try_usdzip(usda_path: Path, usdz_path: Path) -> bool:
    usdzip = shutil.which("usdzip")
    if usdzip is None:
        return False
    try:
        usdz_abs = usdz_path.resolve()
        subprocess.run(
            [usdzip, str(usdz_abs), usda_path.name],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(usda_path.parent),
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _try_usdzconvert(usda_path: Path, usdz_path: Path) -> bool:
    usdzconvert = shutil.which("usdzconvert")
    if usdzconvert is None:
        xcrun = shutil.which("xcrun")
        if xcrun is not None:
            try:
                found = subprocess.run(
                    [xcrun, "--find", "usdzconvert"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).stdout.strip()
            except subprocess.CalledProcessError:
                found = ""
            if found:
                usdzconvert = found
    if usdzconvert is None:
        return False

    try:
        subprocess.run(
            [usdzconvert, str(usda_path), str(usdz_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _write_usdz_fallback_zip(
    usdz_path: Path,
    usda_text: str,
    *,
    extra_files: dict[str, bytes] | None = None,
) -> None:
    with zipfile.ZipFile(usdz_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("scene.usda", usda_text)
        if extra_files is not None:
            for relpath in sorted(extra_files.keys()):
                zf.writestr(relpath, extra_files[relpath])


def _write_scene_tree(root_dir: Path, usda_text: str, *, extra_files: dict[str, bytes] | None = None) -> list[str]:
    scene_path = root_dir / "scene.usda"
    scene_path.write_text(usda_text, encoding="utf-8")
    inputs: list[str] = ["scene.usda"]
    if extra_files is None:
        return inputs
    for relpath in sorted(extra_files.keys()):
        dst = root_dir / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(extra_files[relpath])
        inputs.append(relpath)
    return inputs


def _pack_with_usdzip(usdzip: str, *, root_dir: Path, usdz_path: Path, inputs: list[str]) -> None:
    usdz_abs = usdz_path.resolve()
    subprocess.run(
        [usdzip, str(usdz_abs), *inputs],
        cwd=str(root_dir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write_usdz(usdz_path: Path, usda_text: str, *, extra_files: dict[str, bytes] | None = None) -> None:
    usdz_path.parent.mkdir(parents=True, exist_ok=True)

    if extra_files:
        with tempfile.TemporaryDirectory(prefix="enderterm_usdz_") as td:
            root_dir = Path(td)
            inputs = _write_scene_tree(root_dir, usda_text, extra_files=extra_files)
            usdzip = shutil.which("usdzip")
            if usdzip is not None:
                _pack_with_usdzip(usdzip, root_dir=root_dir, usdz_path=usdz_path, inputs=inputs)
                return

        # Fallback: non-compliant but often viewable USDZ (zip, stored).
        _write_usdz_fallback_zip(usdz_path, usda_text, extra_files=extra_files)
        return

    with tempfile.TemporaryDirectory(prefix="enderterm_usd_") as td:
        root_dir = Path(td)
        _write_scene_tree(root_dir, usda_text, extra_files=None)
        usda_path = root_dir / "scene.usda"

        if _try_usdzip(usda_path, usdz_path):
            return

        if _try_usdzconvert(usda_path, usdz_path):
            return

        # Fallback: write a minimal USDZ (zip, stored). This is often sufficient for Quick Look.
        _write_usdz_fallback_zip(usdz_path, usda_text)


def _open_texture_source_for_conversion(*, textured: bool, minecraft_jar: Path | None) -> TextureSource | None:
    if not textured:
        return None
    jar_path = minecraft_jar or find_minecraft_client_jar()
    if jar_path is None:
        raise SystemExit("textured mode requires a Minecraft client jar; pass --minecraft-jar or set $MINECRAFT_JAR")
    return TextureSource(jar_path)


def _structure_for_conversion(
    root: nbtlib.Compound,
    *,
    mode: str,
    auto_threshold: int,
) -> Structure:
    return apply_render_mode(parse_structure(root), mode, auto_threshold)


def _write_structure_usdz(
    structure: Structure,
    out_path: Path,
    *,
    texture_source: TextureSource | None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if texture_source is not None:
        usda_text, extra_files = structure_to_usda_textured(structure, texture_source)
        write_usdz(out_path, usda_text, extra_files=extra_files)
        return
    usda_text = structure_to_usda_text(structure)
    write_usdz(out_path, usda_text)


def _convert_root_to_usdz(
    root: nbtlib.Compound,
    out_path: Path,
    *,
    mode: str,
    auto_threshold: int,
    texture_source: TextureSource | None,
) -> None:
    structure = _structure_for_conversion(root, mode=mode, auto_threshold=auto_threshold)
    _write_structure_usdz(structure, out_path, texture_source=texture_source)


@contextmanager
def _conversion_texture_source(
    *,
    textured: bool,
    minecraft_jar: Path | None,
) -> Generator[TextureSource | None, None, None]:
    texture_source = _open_texture_source_for_conversion(textured=textured, minecraft_jar=minecraft_jar)
    try:
        yield texture_source
    finally:
        if texture_source is not None:
            texture_source.close()


def _iter_structure_paths_in_datapack_dir(datapack_path: Path) -> Iterable[tuple[str, Path]]:
    data_dir = datapack_path / "data"
    if not data_dir.is_dir():
        return
    for namespace_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        for folder in ("structures", "structure"):
            structures_dir = namespace_dir / folder
            if not structures_dir.is_dir():
                continue
            for nbt_path in sorted(structures_dir.rglob("*.nbt")):
                rel_inside_structures = nbt_path.relative_to(structures_dir)
                out_rel = str(Path(namespace_dir.name) / rel_inside_structures.with_suffix(".usdz"))
                yield (out_rel, nbt_path)


def _iter_structure_entries_in_datapack_zip(zip_file: zipfile.ZipFile) -> Iterable[tuple[str, str]]:
    names = sorted(n for n in zip_file.namelist() if n.endswith(".nbt") and not n.endswith("/"))
    for name in names:
        parts = PurePosixPath(name).parts
        # Support zips that wrap the datapack inside a top-level folder.
        for i, part in enumerate(parts):
            if part != "data":
                continue
            if i + 2 >= len(parts):
                continue
            namespace = parts[i + 1]
            if parts[i + 2] not in {"structures", "structure"}:
                continue
            rel_inside_structures = PurePosixPath(*parts[i + 3 :])
            if not rel_inside_structures.as_posix().endswith(".nbt"):
                continue
            out_rel = str(Path(namespace) / Path(rel_inside_structures.as_posix()).with_suffix(".usdz"))
            yield (out_rel, name)
            break


def _convert_zip_datapack_structures_to_usdz(
    datapack_path: Path,
    output_dir: Path,
    *,
    mode: str,
    auto_threshold: int,
    texture_source: TextureSource | None,
) -> tuple[int, int]:
    ok = 0
    fail = 0
    with zipfile.ZipFile(datapack_path, "r") as zf:
        for out_rel, name in _iter_structure_entries_in_datapack_zip(zf):
            try:
                data = zf.read(name)
                root = load_nbt_bytes(data)
                _convert_root_to_usdz(
                    root,
                    output_dir / out_rel,
                    mode=mode,
                    auto_threshold=auto_threshold,
                    texture_source=texture_source,
                )
                ok += 1
            except Exception:
                fail += 1
    return (ok, fail)


def _convert_directory_datapack_structures_to_usdz(
    datapack_path: Path,
    output_dir: Path,
    *,
    mode: str,
    auto_threshold: int,
    texture_source: TextureSource | None,
) -> tuple[int, int]:
    ok = 0
    fail = 0
    for out_rel, nbt_path in _iter_structure_paths_in_datapack_dir(datapack_path):
        try:
            root = load_nbt(nbt_path)
            _convert_root_to_usdz(
                root,
                output_dir / out_rel,
                mode=mode,
                auto_threshold=auto_threshold,
                texture_source=texture_source,
            )
            ok += 1
        except Exception:
            fail += 1
    return (ok, fail)


def convert_datapack_structures_to_usdz(
    datapack_path: Path,
    output_dir: Path,
    *,
    mode: str,
    auto_threshold: int,
    textured: bool,
    minecraft_jar: Path | None,
) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)

    with _conversion_texture_source(textured=textured, minecraft_jar=minecraft_jar) as texture_source:
        if datapack_path.is_file() and datapack_path.suffix.lower() in {".zip", ".jar"}:
            return _convert_zip_datapack_structures_to_usdz(
                datapack_path,
                output_dir,
                mode=mode,
                auto_threshold=auto_threshold,
                texture_source=texture_source,
            )
        return _convert_directory_datapack_structures_to_usdz(
            datapack_path,
            output_dir,
            mode=mode,
            auto_threshold=auto_threshold,
            texture_source=texture_source,
        )
