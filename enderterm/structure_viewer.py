from __future__ import annotations

"""Pyglet/OpenGL structure viewer entrypoint (extracted from nbttool_impl)."""

from importlib import import_module as _import_module

from enderterm.minecraft_jar import (
    load_configured_minecraft_jar_path,
    save_configured_minecraft_jar_path,
    validate_minecraft_client_jar,
)
from enderterm.ui_anim import _termui_theme_from_store

_impl = _import_module("enderterm.nbttool_impl")
for _k, _v in _impl.__dict__.items():
    if _k in {"__name__", "__loader__", "__package__", "__spec__", "__file__", "__cached__"}:
        continue
    globals().setdefault(_k, _v)


def view_structure_opengl(  # pragma: no cover
    structure: Structure,
    *,
    mode: str,
    auto_threshold: int,
    textured: bool,
    minecraft_jar: Path | None,
    test_banner: str | None = None,
) -> None:
    try:
        import pyglet
        from pyglet import gl
        from pyglet.gl import GLfloat, gluPerspective  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"OpenGL viewer requires pyglet (pip install pyglet). Import error: {e}") from e

    # Prefer the shared workspace font dir (../../font) so updates take effect
    # immediately without re-copying assets; fall back to bundled fonts.
    workspace_font_dir = Path(__file__).resolve().parents[2] / "font"
    font_candidates = [
        workspace_font_dir / "term_mixed.ttf",
        workspace_font_dir / "english.ttf",
        workspace_font_dir / "ender.otf",
        workspace_font_dir / "term.ttc",
        Path(__file__).resolve().parent / "assets" / "fonts" / "term_mixed.ttf",
        Path(__file__).resolve().parent / "assets" / "fonts" / "term.ttc",
        Path(__file__).resolve().parent / "assets" / "fonts" / "Glass_TTY_VT220.ttf",
    ]
    for font_path in font_candidates:
        if font_path.is_file():
            try:
                pyglet.font.add_file(str(font_path))
            except Exception:
                pass

    structure = apply_render_mode(structure, mode, auto_threshold)
    param_store = load_default_param_store()
    test_banner_text = str(test_banner).strip() if isinstance(test_banner, str) else ""
    test_build = _enderterm_version()

    blocks = structure.blocks

    if blocks:
        xs = [b.pos[0] for b in blocks]
        ys = [b.pos[1] for b in blocks]
        zs = [b.pos[2] for b in blocks]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)
        center_x = (min_x + max_x + 1) / 2.0
        center_y = (min_y + max_y + 1) / 2.0
        center_z = (min_z + max_z + 1) / 2.0
        size_x = max_x - min_x + 1
        size_y = max_y - min_y + 1
        size_z = max_z - min_z + 1
    else:
        sx, sy, sz = structure.size
        center_x = sx / 2.0
        center_y = sy / 2.0
        center_z = sz / 2.0
        size_x, size_y, size_z = sx, sy, sz

    diag = math.sqrt(float(size_x * size_x + size_y * size_y + size_z * size_z))
    initial_distance = max(2.0, diag * 1.25)

    def _rot_xy(v: tuple[float, float, float], *, rx_deg: int, ry_deg: int) -> tuple[float, float, float]:
        if rx_deg == 0 and ry_deg == 0:
            return v
        x, y, z = v
        rx = math.radians(-rx_deg)
        ry = math.radians(-ry_deg)
        # Rotate around X.
        cy = math.cos(rx)
        sy = math.sin(rx)
        y2 = y * cy - z * sy
        z2 = y * sy + z * cy
        y, z = y2, z2
        # Rotate around Y.
        cx = math.cos(ry)
        sx_ = math.sin(ry)
        x2 = x * cx + z * sx_
        z2 = -x * sx_ + z * cx
        return (x2, y, z2)

    face_normals = FACE_NORMALS
    neighbor_delta = FACE_NEIGHBOR_DELTA
    unit_face_quads = _UNIT_CUBE_FACE_QUADS
    unit_face_uv_tri = _UNIT_CUBE_FACE_UV_TRI

    class NoTextureGroup(pyglet.graphics.Group):
        def set_state(self) -> None:
            gl.glDisable(gl.GL_TEXTURE_2D)

        def unset_state(self) -> None:
            gl.glEnable(gl.GL_TEXTURE_2D)

    batch = pyglet.graphics.Batch()
    no_tex_group = NoTextureGroup()
    test_banner_shape_batch = pyglet.graphics.Batch()
    test_banner_text_batch = pyglet.graphics.Batch()

    jar_path: Path | None = None
    texture_source: TextureSource | None = None
    if textured:
        jar_path = minecraft_jar or find_minecraft_client_jar()
        if jar_path is None:
            raise SystemExit(
                "textured view requires a Minecraft client jar; pass --minecraft-jar or set $MINECRAFT_JAR"
            )
        texture_source = TextureSource(jar_path)

    cfg_jar_path = load_configured_minecraft_jar_path()
    cfg_jar_error: str | None = None
    if cfg_jar_path is not None:
        if not cfg_jar_path.is_file():
            cfg_jar_error = f"Configured Minecraft jar not found: {cfg_jar_path}"
        else:
            err = validate_minecraft_client_jar(cfg_jar_path)
            if err is not None:
                cfg_jar_error = f"Configured Minecraft jar invalid: {err}"

    startup_jar_banner_text = ""
    startup_jar_banner_kind = "warn"
    if cfg_jar_error:
        startup_jar_banner_kind = "error"
        if jar_path is not None and jar_path.is_file():
            startup_jar_banner_text = (
                f"{cfg_jar_error}\nUsing fallback jar: {jar_path}\n"
                "Drag-drop a valid Minecraft client .jar into this window (or onto the app icon) to fix."
            )
        else:
            startup_jar_banner_text = (
                f"{cfg_jar_error}\nDrag-drop a valid Minecraft client .jar into this window (or onto the app icon) to fix."
            )
    elif texture_source is None:
        startup_jar_banner_kind = "warn"
        startup_jar_banner_text = (
            "Textures are disabled (no Minecraft client .jar configured).\n"
            "Drag-drop a Minecraft client .jar into this window (or onto the app icon) to enable textures."
        )

    tex_cache: dict[str, pyglet.image.Texture] = {}
    group_cache: dict[str, pyglet.graphics.Group] = {}

    def load_tex_from_jar(source: TextureSource, jar_rel: str) -> pyglet.image.Texture | None:
        cached = tex_cache.get(jar_rel)
        if cached is not None:
            return cached
        if not source.has(jar_rel):
            return None
        data = source.read(jar_rel)
        img = pyglet.image.load(jar_rel.rsplit("/", 1)[-1], file=io.BytesIO(data))
        tex = img.get_texture()
        tex.mag_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
        tex.min_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
        try:
            tex.wrap_s = gl.GL_REPEAT  # type: ignore[attr-defined]
            tex.wrap_t = gl.GL_REPEAT  # type: ignore[attr-defined]
        except Exception:
            pass
        tex_cache[jar_rel] = tex
        return tex

    # Pre-bake faces grouped by texture (or by color).
    def build_geometry(source: TextureSource | None) -> None:
        nonlocal batch
        batch = pyglet.graphics.Batch()
        tex_cache.clear()
        group_cache.clear()
        resolver = MinecraftResourceResolver(source) if (textured and source is not None) else None
        solids: set[tuple[int, int, int]] = set()
        for b in blocks:
            base_id = _block_id_base(b.block_id)
            if base_id in {"minecraft:jigsaw", "minecraft:structure_void"}:
                continue
            if resolver is None:
                solids.add(b.pos)
                continue
            bm = resolver.resolve_block_model(b.block_id)
            if bm is None or _block_model_is_full_cube(bm):
                solids.add(b.pos)
        verts_by_tex: dict[str, list[float]] = {}
        norms_by_tex: dict[str, list[float]] = {}
        uvs_by_tex: dict[str, list[float]] = {}
        cols_by_tex: dict[str, list[int]] = {}
        colored_verts: list[float] = []
        colored_norms: list[float] = []
        colored_cols: list[int] = []
        colormap_cache: dict[str, tuple[int, int, bytes]] = {}

        def _sample_colormap(jar_rel: str, *, temperature: float, humidity: float) -> tuple[int, int, int]:
            if source is None or not source.has(jar_rel):
                return (255, 255, 255)
            cached = colormap_cache.get(jar_rel)
            if cached is None:
                data = source.read(jar_rel)
                img = pyglet.image.load(jar_rel.rsplit("/", 1)[-1], file=io.BytesIO(data))
                raw = img.get_image_data().get_data("RGBA", img.width * 4)
                cached = (img.width, img.height, raw)
                colormap_cache[jar_rel] = cached
            w, h, raw = cached
            x = max(0, min(w - 1, int((1.0 - temperature) * float(w - 1))))
            y = max(0, min(h - 1, int((1.0 - humidity) * float(h - 1))))
            idx = (y * w + x) * 4
            return (raw[idx], raw[idx + 1], raw[idx + 2])

        def _tint_rgb(block_state_id: str, tintindex: int) -> tuple[int, int, int]:
            if tintindex < 0:
                return (255, 255, 255)
            base, props = _parse_block_state_id(block_state_id)
            if base == "minecraft:redstone_wire":
                try:
                    power = int(props.get("power", "0"))
                except ValueError:
                    power = 0
                power = max(0, min(15, power))
                if power == 0:
                    return (77, 0, 0)  # 0.3
                f = power / 15.0
                r = 0.4 + 0.6 * f
                g = max(0.0, f * f * 0.7 - 0.5)
                b = max(0.0, f * f * 0.6 - 0.7)
                return (int(r * 255), int(g * 255), int(b * 255))

            if base == "minecraft:grass_block" or base.endswith("_grass") or base in {"minecraft:grass", "minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern", "minecraft:large_fern"}:
                return _sample_colormap(
                    "assets/minecraft/textures/colormap/grass.png",
                    temperature=0.8,
                    humidity=0.4,
                )
            if base.endswith("_leaves") or base in {"minecraft:vine", "minecraft:bamboo"} or base.endswith("_stem") or base.startswith("minecraft:attached_"):
                return _sample_colormap(
                    "assets/minecraft/textures/colormap/foliage.png",
                    temperature=0.8,
                    humidity=0.4,
                )
            if "cauldron" in base:
                return (63, 118, 228)  # default water color (0x3F76E4)
            return (255, 255, 255)

        for block in blocks:
            x, y, z = block.pos
            tx = x + 0.5 - center_x
            ty = y + 0.5 - center_y
            tz = z + 0.5 - center_z

            appearance = None
            block_model = None
            if resolver is not None:
                base_id = _block_id_base(block.block_id)
                force_color = base_id == "minecraft:jigsaw"
                if not force_color:
                    appearance = resolver.resolve_block_appearance(block.block_id)
                    block_model = resolver.resolve_block_model(block.block_id)
            rx = appearance.rotate_x_deg if appearance is not None else 0
            ry = appearance.rotate_y_deg if appearance is not None else 0

            if block_model is not None and source is not None and resolver is not None:
                solid_block = _block_model_is_full_cube(block_model)
                for part in block_model.parts:
                    rx_p = int(part.rotate_x_deg)
                    ry_p = int(part.rotate_y_deg)
                    internal_cull = resolver.internal_face_cull_for_model(part.model_ref, part.model)
                    for idx_el, el in enumerate(part.model.elements or []):
                        if not isinstance(el, dict):
                            continue
                        frm = el.get("from")
                        to = el.get("to")
                        if not (isinstance(frm, list) and isinstance(to, list) and len(frm) == 3 and len(to) == 3):
                            continue
                        try:
                            fx, fy, fz = (float(frm[0]), float(frm[1]), float(frm[2]))
                            txe, tye, tze = (float(to[0]), float(to[1]), float(to[2]))
                        except (TypeError, ValueError):
                            continue
                        xmin_el = min(fx, txe) / 16.0 - 0.5
                        xmax_el = max(fx, txe) / 16.0 - 0.5
                        ymin_el = min(fy, tye) / 16.0 - 0.5
                        ymax_el = max(fy, tye) / 16.0 - 0.5
                        zmin_el = min(fz, tze) / 16.0 - 0.5
                        zmax_el = max(fz, tze) / 16.0 - 0.5

                        faces_obj = el.get("faces")
                        if not isinstance(faces_obj, dict):
                            continue
                        frm_t = (fx, fy, fz)
                        to_t = (txe, tye, tze)
                        rot_el = el.get("rotation")

                        for face in FACE_DIRS:
                            if face in internal_cull.get(idx_el, ()):
                                continue
                            face_def = faces_obj.get(face)
                            if not isinstance(face_def, dict):
                                continue

                            face_on_boundary = False
                            if face == "north":
                                face_on_boundary = abs(zmin_el + 0.5) < 1e-6
                            elif face == "south":
                                face_on_boundary = abs(zmax_el - 0.5) < 1e-6
                            elif face == "west":
                                face_on_boundary = abs(xmin_el + 0.5) < 1e-6
                            elif face == "east":
                                face_on_boundary = abs(xmax_el - 0.5) < 1e-6
                            elif face == "down":
                                face_on_boundary = abs(ymin_el + 0.5) < 1e-6
                            elif face == "up":
                                face_on_boundary = abs(ymax_el - 0.5) < 1e-6

                            # Cull neighbor faces for full cubes; for non-cubes,
                            # cull only when the element face lies on the block boundary.
                            if solid_block or face_on_boundary:
                                n_rot = _rot_xy(face_normals[face], rx_deg=rx_p, ry_deg=ry_p)
                                nx, ny, nz = n_rot
                                dx = 1 if nx > 0.5 else (-1 if nx < -0.5 else 0)
                                dy = 1 if ny > 0.5 else (-1 if ny < -0.5 else 0)
                                dz = 1 if nz > 0.5 else (-1 if nz < -0.5 else 0)
                                if (x + dx, y + dy, z + dz) in solids:
                                    continue

                            raw_tex = face_def.get("texture")
                            tex_ref: str | None = raw_tex if isinstance(raw_tex, str) and raw_tex else None
                            tex_resolved = (
                                resolver._resolve_texture_ref(tex_ref, part.model.textures) if tex_ref is not None else None
                            )
                            jar_rel = resolver._texture_ref_to_jar_rel(tex_resolved) if tex_resolved else None
                            if jar_rel is None or not source.has(jar_rel):
                                jar_rel = None

                            uv_rect: tuple[float, float, float, float] | None = None
                            uv_obj = face_def.get("uv")
                            if isinstance(uv_obj, list) and len(uv_obj) == 4:
                                try:
                                    uv_rect = (float(uv_obj[0]), float(uv_obj[1]), float(uv_obj[2]), float(uv_obj[3]))
                                except (TypeError, ValueError):
                                    uv_rect = None
                            if uv_rect is None:
                                uv_rect = _default_uv_rect_for_face(face, frm=frm_t, to=to_t)

                            rot_deg = face_def.get("rotation", 0)
                            rot_deg = int(rot_deg) if isinstance(rot_deg, (int, float)) else 0

                            tint = (255, 255, 255)
                            tint_obj = face_def.get("tintindex")
                            if isinstance(tint_obj, (int, float)):
                                tint = _tint_rgb(block.block_id, int(tint_obj))

                            quad = _element_face_points(
                                face,
                                xmin=xmin_el,
                                xmax=xmax_el,
                                ymin=ymin_el,
                                ymax=ymax_el,
                                zmin=zmin_el,
                                zmax=zmax_el,
                            )
                            tri_uv = _uv_tri_for_face_rect(face, uv_rect, rotation_deg=rot_deg, quad_points=quad)
                            quad = _apply_element_rotation(quad, rot_el)
                            quad_r = [_rot_xy(p, rx_deg=rx_p, ry_deg=ry_p) for p in quad]
                            quad_w = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad_r]
                            p0, p1, p2, p3 = quad_w
                            normal = _tri_normal(p0, p1, p2)
                            tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

                            if jar_rel is not None:
                                tex = load_tex_from_jar(source, jar_rel)
                                if tex is not None:
                                    verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
                                    norms_by_tex.setdefault(jar_rel, []).extend([*normal, *normal, *normal, *normal, *normal, *normal])
                                    uvs_by_tex.setdefault(jar_rel, []).extend(tri_uv)
                                    cols_by_tex.setdefault(jar_rel, []).extend([*tint, *tint, *tint, *tint, *tint, *tint])
                                    continue

                            r, g, b = _stable_rgb(block.color_key)
                            col = (int(r * 255), int(g * 255), int(b * 255))
                            colored_verts.extend(tri_verts)
                            colored_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                            colored_cols.extend([*col, *col, *col, *col, *col, *col])

                continue

            for face in FACE_DIRS:
                dx, dy, dz = neighbor_delta[face]
                if (x + dx, y + dy, z + dz) in solids:
                    continue

                quad = unit_face_quads[face]
                if base_id == "minecraft:jigsaw":
                    quad = [(px * 1.5, py * 1.5, pz * 1.5) for (px, py, pz) in quad]
                normal = face_normals[face]
                normal_r = _rot_xy(normal, rx_deg=rx, ry_deg=ry)

                quad_r = [_rot_xy(p, rx_deg=rx, ry_deg=ry) for p in quad]
                quad_w = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad_r]

                p0, p1, p2, p3 = quad_w
                tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

                if appearance is not None and source is not None:
                    jar_rel = appearance.face_texture_png_by_dir.get(face) or ""
                    if jar_rel:
                        tex = load_tex_from_jar(source, jar_rel)
                        if tex is not None:
                            verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
                            norms_by_tex.setdefault(jar_rel, []).extend(
                                [*normal_r, *normal_r, *normal_r, *normal_r, *normal_r, *normal_r]
                            )
                            uvs_by_tex.setdefault(jar_rel, []).extend(unit_face_uv_tri[face])
                            cols_by_tex.setdefault(jar_rel, []).extend([255, 255, 255] * 6)
                            continue

                r, g, b = _stable_rgb(block.color_key)
                col = (int(r * 255), int(g * 255), int(b * 255))
                colored_verts.extend(tri_verts)
                colored_norms.extend([*normal_r, *normal_r, *normal_r, *normal_r, *normal_r, *normal_r])
                colored_cols.extend([*col, *col, *col, *col, *col, *col])

        def _display_entity_matrix(nbt: dict[str, object], *, pos: tuple[float, float, float]) -> Mat4:
            ex, ey, ez = pos
            m = _mat4_translate(ex - center_x, ey - center_y, ez - center_z)

            tr_obj = nbt.get("transformation")
            if isinstance(tr_obj, dict):
                t3 = _nbt_float_n(tr_obj.get("translation"), 3) or (0.0, 0.0, 0.0)
                lq4 = _nbt_float_n(tr_obj.get("left_rotation"), 4) or (0.0, 0.0, 0.0, 1.0)
                s3 = _nbt_float_n(tr_obj.get("scale"), 3) or (1.0, 1.0, 1.0)
                rq4 = _nbt_float_n(tr_obj.get("right_rotation"), 4) or (0.0, 0.0, 0.0, 1.0)
                m = _mat4_mul(m, _mat4_translate(float(t3[0]), float(t3[1]), float(t3[2])))
                m = _mat4_mul(m, _mat4_from_quat_xyzw((float(lq4[0]), float(lq4[1]), float(lq4[2]), float(lq4[3]))))
                m = _mat4_mul(m, _mat4_scale(float(s3[0]), float(s3[1]), float(s3[2])))
                m = _mat4_mul(m, _mat4_from_quat_xyzw((float(rq4[0]), float(rq4[1]), float(rq4[2]), float(rq4[3]))))
                return m

            tr_list = nbt.get("transformation")
            vals = _nbt_float_n(tr_list, 16)
            if vals is not None and len(vals) == 16:
                m = _mat4_mul(
                    m,
                    (
                        float(vals[0]),
                        float(vals[1]),
                        float(vals[2]),
                        float(vals[3]),
                        float(vals[4]),
                        float(vals[5]),
                        float(vals[6]),
                        float(vals[7]),
                        float(vals[8]),
                        float(vals[9]),
                        float(vals[10]),
                        float(vals[11]),
                        float(vals[12]),
                        float(vals[13]),
                        float(vals[14]),
                        float(vals[15]),
                    ),
                )
            return m

        # Entities: render "display entities" as actual geometry where possible.
        for ent in structure.entities:
            ent_id_obj = ent.nbt.get("id")
            if ent_id_obj is None:
                ent_id_obj = ent.nbt.get("Id")
            ent_id = str(ent_id_obj) if ent_id_obj else ""
            if ent_id == "minecraft:block_display":
                bs_obj = ent.nbt.get("block_state")
                if bs_obj is None:
                    bs_obj = ent.nbt.get("blockState")
                bs_id = _block_state_id(bs_obj) if bs_obj is not None else None
                if not bs_id:
                    continue
                m = _display_entity_matrix(ent.nbt, pos=ent.pos)
                block_model = resolver.resolve_block_model(bs_id) if resolver is not None else None
                if resolver is not None and source is not None and block_model is not None:
                    for part in block_model.parts:
                        rx_p = int(part.rotate_x_deg)
                        ry_p = int(part.rotate_y_deg)
                        for el in part.model.elements or []:
                            if not isinstance(el, dict):
                                continue
                            frm = el.get("from")
                            to = el.get("to")
                            if not (isinstance(frm, list) and isinstance(to, list) and len(frm) == 3 and len(to) == 3):
                                continue
                            try:
                                fx, fy, fz = (float(frm[0]), float(frm[1]), float(frm[2]))
                                txe, tye, tze = (float(to[0]), float(to[1]), float(to[2]))
                            except (TypeError, ValueError):
                                continue
                            xmin_el = min(fx, txe) / 16.0 - 0.5
                            xmax_el = max(fx, txe) / 16.0 - 0.5
                            ymin_el = min(fy, tye) / 16.0 - 0.5
                            ymax_el = max(fy, tye) / 16.0 - 0.5
                            zmin_el = min(fz, tze) / 16.0 - 0.5
                            zmax_el = max(fz, tze) / 16.0 - 0.5

                            faces_obj = el.get("faces")
                            if not isinstance(faces_obj, dict):
                                continue
                            frm_t = (fx, fy, fz)
                            to_t = (txe, tye, tze)
                            rot_el = el.get("rotation")

                            for face in FACE_DIRS:
                                face_def = faces_obj.get(face)
                                if not isinstance(face_def, dict):
                                    continue

                                raw_tex = face_def.get("texture")
                                tex_ref: str | None = raw_tex if isinstance(raw_tex, str) and raw_tex else None
                                tex_resolved = (
                                    resolver._resolve_texture_ref(tex_ref, part.model.textures) if tex_ref is not None else None
                                )
                                jar_rel = resolver._texture_ref_to_jar_rel(tex_resolved) if tex_resolved else None
                                if jar_rel is None or not source.has(jar_rel):
                                    jar_rel = None

                                uv_rect: tuple[float, float, float, float] | None = None
                                uv_obj = face_def.get("uv")
                                if isinstance(uv_obj, list) and len(uv_obj) == 4:
                                    try:
                                        uv_rect = (float(uv_obj[0]), float(uv_obj[1]), float(uv_obj[2]), float(uv_obj[3]))
                                    except (TypeError, ValueError):
                                        uv_rect = None
                                if uv_rect is None:
                                    uv_rect = _default_uv_rect_for_face(face, frm=frm_t, to=to_t)

                                rot_deg = face_def.get("rotation", 0)
                                rot_deg = int(rot_deg) if isinstance(rot_deg, (int, float)) else 0

                                tint = (255, 255, 255)
                                tint_obj = face_def.get("tintindex")
                                if isinstance(tint_obj, (int, float)):
                                    tint = _tint_rgb(source, bs_id, int(tint_obj))

                                quad = _element_face_points(
                                    face,
                                    xmin=xmin_el,
                                    xmax=xmax_el,
                                    ymin=ymin_el,
                                    ymax=ymax_el,
                                    zmin=zmin_el,
                                    zmax=zmax_el,
                                )
                                tri_uv = _uv_tri_for_face_rect(face, uv_rect, rotation_deg=rot_deg, quad_points=quad)
                                quad = _apply_element_rotation(quad, rot_el)
                                quad_r = [_rot_xy(p, rx_deg=rx_p, ry_deg=ry_p) for p in quad]
                                quad_w = [_mat4_apply_point(m, p) for p in quad_r]
                                p0, p1, p2, p3 = quad_w
                                normal = _tri_normal(p0, p1, p2)
                                tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

                                if jar_rel is not None:
                                    verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
                                    norms_by_tex.setdefault(jar_rel, []).extend([*normal, *normal, *normal, *normal, *normal, *normal])
                                    uvs_by_tex.setdefault(jar_rel, []).extend(tri_uv)
                                    cols_by_tex.setdefault(jar_rel, []).extend([*tint, *tint, *tint, *tint, *tint, *tint])
                                    continue

                                r, g, b = _stable_rgb(bs_id)
                                col = (int(r * 255), int(g * 255), int(b * 255))
                                colored_verts.extend(tri_verts)
                                colored_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                                colored_cols.extend([*col, *col, *col, *col, *col, *col])
                    continue

            # Fallback: small marker cube for other entities (and for display entities
            # when we can't resolve their model).
            r, g, b = _stable_rgb(ent_id or "entity")
            col = (int(r * 255), int(g * 255), int(b * 255))
            mx, my, mz = ent.pos
            base = (mx - center_x, my - center_y, mz - center_z)
            s = 0.24
            for face in FACE_DIRS:
                quad = _UNIT_CUBE_FACE_QUADS[face]
                quad_s = [(px * s + base[0], py * s + base[1], pz * s + base[2]) for (px, py, pz) in quad]
                p0, p1, p2, p3 = quad_s
                normal = _tri_normal(p0, p1, p2)
                tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]
                colored_verts.extend(tri_verts)
                colored_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                colored_cols.extend([*col, *col, *col, *col, *col, *col])

        # One draw call per texture (plus one for colored faces).
        for jar_rel in sorted(verts_by_tex.keys()):
            tex = tex_cache.get(jar_rel)
            if tex is None:
                continue
            group = group_cache.get(jar_rel)
            if group is None:
                group = pyglet.graphics.TextureGroup(tex)
                group_cache[jar_rel] = group
            verts = verts_by_tex[jar_rel]
            norms = norms_by_tex[jar_rel]
            uvs = uvs_by_tex[jar_rel]
            cols = cols_by_tex.get(jar_rel) or [255, 255, 255] * (len(verts) // 3)
            batch.add(
                len(verts) // 3,
                gl.GL_TRIANGLES,
                group,
                ("v3f/static", verts),
                ("n3f/static", norms),
                ("t2f/static", uvs),
                ("c3B/static", cols),
            )

        if colored_verts:
            batch.add(
                len(colored_verts) // 3,
                gl.GL_TRIANGLES,
                no_tex_group,
                ("v3f/static", colored_verts),
                ("n3f/static", colored_norms),
                ("c3B/static", colored_cols),
            )

    class ViewerWindow(pyglet.window.Window):
        def __init__(self) -> None:
            super().__init__(width=1100, height=800, resizable=True, caption="EnderTerm: nbt-view")
            self.yaw = 45.0
            self.pitch = 25.0
            self.distance = initial_distance
            self.pan_x = 0.0
            self.pan_y = 0.0
            self._last_drag_buttons: int | None = None
            self._test_banner_text = str(test_banner_text)
            self._test_banner_build = str(test_build)
            self._test_banner_last_layout: tuple[int, int, str, str] = (-1, -1, "", "")
            self.test_banner_bg = pyglet.shapes.Rectangle(
                0,
                0,
                1,
                1,
                color=(0, 0, 0),
                batch=test_banner_shape_batch,
            )
            self.test_banner_bg.opacity = 210
            self.test_banner_bg.visible = bool(self._test_banner_text)
            self.test_banner_label = pyglet.text.Label(
                "",
                x=0,
                y=0,
                anchor_x="left",
                anchor_y="top",
                font_size=14,
                color=(235, 235, 245, 255),
                multiline=True,
                width=100,
                batch=test_banner_text_batch,
            )
            self.test_banner_label.visible = bool(self._test_banner_text)

            # Jar alert (TermUI overlay panel over the 3D view).
            from enderterm.termui import TerminalRenderer

            self._jar_alert_text = str(startup_jar_banner_text)
            self._jar_alert_kind = str(startup_jar_banner_kind)
            self._jar_alert_dismissed = False
            self._jar_term_renderer = TerminalRenderer()
            self._jar_term_surface = None
            self._jar_term_font = None
            self._jar_term_scale_last = -1.0
            self._jar_term_ratio_last = -1.0
            self._jar_term_panel_rect: tuple[float, float, float, float] | None = None
            self._jar_term_dismiss_rect: tuple[float, float, float, float] | None = None

            gl.glClearColor(0.0, 0.0, 0.0, 1.0)
            gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glDepthFunc(gl.GL_LEQUAL)
            gl.glShadeModel(gl.GL_SMOOTH)

            gl.glEnable(gl.GL_LIGHTING)
            gl.glEnable(gl.GL_LIGHT0)
            gl.glEnable(gl.GL_COLOR_MATERIAL)
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_AMBIENT, (GLfloat * 4)(0.2, 0.2, 0.2, 1.0))
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_DIFFUSE, (GLfloat * 4)(0.9, 0.9, 0.9, 1.0))
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_POSITION, (GLfloat * 4)(0.35, 0.9, 0.5, 0.0))

            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glEnable(gl.GL_CULL_FACE)
            gl.glCullFace(gl.GL_BACK)
            gl.glFrontFace(gl.GL_CCW)

        def _sync_jar_termui(self, *, force: bool) -> tuple[object, object]:
            from enderterm.termui import MinecraftAsciiBitmapFont, TerminalFont, TerminalSurface, _default_minecraft_ascii_png

            try:
                ui_scale = float(param_store.get("ui.font.scale") or 1.0)
            except Exception:
                ui_scale = 1.0
            if not math.isfinite(ui_scale):
                ui_scale = 1.0
            ui_scale = max(0.5, min(3.0, float(ui_scale)))

            try:
                ratio = float(self.get_pixel_ratio())
            except Exception:
                ratio = 1.0
            if ratio <= 0.0 or not math.isfinite(ratio):
                ratio = 1.0

            if (not force) and abs(ui_scale - float(getattr(self, "_jar_term_scale_last", -1.0))) < 1e-6 and abs(
                ratio - float(getattr(self, "_jar_term_ratio_last", -1.0))
            ) < 1e-6:
                if self._jar_term_font is not None and self._jar_term_surface is not None:
                    return self._jar_term_font, self._jar_term_surface

            self._jar_term_scale_last = float(ui_scale)
            self._jar_term_ratio_last = float(ratio)

            font_size_px = max(6, int(round(14.0 * ui_scale * ratio)))
            ascii_png = _default_minecraft_ascii_png()
            if ascii_png is not None:
                self._jar_term_font = MinecraftAsciiBitmapFont(atlas_path=ascii_png, cell_px=font_size_px)
            else:
                self._jar_term_font = TerminalFont(font_name=None, font_size_px=font_size_px)

            if self._jar_term_surface is None:
                # Transparent background so we can composite above the 3D view.
                self._jar_term_surface = TerminalSurface(1, 1, default_fg=(18, 14, 22, 255), default_bg=(0, 0, 0, 0))
            return self._jar_term_font, self._jar_term_surface

        def _draw_jar_alert_termui(self) -> None:
            from enderterm.termui import TerminalSurface

            if getattr(self, "_jar_alert_dismissed", False) or not str(getattr(self, "_jar_alert_text", "") or "").strip():
                self._jar_term_panel_rect = None
                self._jar_term_dismiss_rect = None
                return

            term_font, surface = self._sync_jar_termui(force=False)
            renderer = getattr(self, "_jar_term_renderer", None)
            if renderer is None or not isinstance(surface, TerminalSurface):
                return

            try:
                ratio = float(self.get_pixel_ratio())
            except Exception:
                ratio = 1.0
            if ratio <= 0.0 or not math.isfinite(ratio):
                ratio = 1.0

            vp_w_px, vp_h_px = self.get_viewport_size()
            view_w_px = max(1, int(vp_w_px))
            view_h_px = max(1, int(vp_h_px))

            cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
            cell_h = max(1, int(getattr(term_font, "cell_h", 14)))

            max_cols = max(20, int(view_w_px // cell_w) - 4)
            cols = int(round(float(max_cols) * 0.78))
            cols = max(44, min(int(cols), int(max_cols), 120))

            msg = str(getattr(self, "_jar_alert_text", "") or "").strip()
            kind = str(getattr(self, "_jar_alert_kind", "warn") or "warn").strip().lower()
            label_kind = "ALERT" if kind == "error" else "WARNING"

            inner_w = max(1, int(cols) - 2)
            wrapped: list[str] = []
            for para in msg.splitlines() or [""]:
                words = [w for w in str(para).split(" ") if w != ""]
                if not words:
                    wrapped.append("")
                    continue
                line = ""
                for w in words:
                    if not line:
                        line = w
                        continue
                    if len(line) + 1 + len(w) <= inner_w:
                        line = f"{line} {w}"
                    else:
                        wrapped.append(line[:inner_w])
                        line = w
                if line:
                    wrapped.append(line[:inner_w])

            max_rows = max(6, int(view_h_px // cell_h) - 4)
            rows = max(6, min(int(max_rows), int(len(wrapped) + 2)))
            surface.resize(int(cols), int(rows))

            theme = _termui_theme_from_store(param_store)
            panel_bg = (theme.bg[0], theme.bg[1], theme.bg[2], 210)
            fg = theme.fg
            muted = theme.muted
            box_fg = theme.box_fg
            accent = theme.accent

            surface.default_bg = (0, 0, 0, 0)
            surface.default_fg = fg
            surface.clear()
            surface.fill_rect(0, 0, int(cols), int(rows), bg=panel_bg, fg=fg, ch=" ")
            border_fg = accent if kind == "error" else box_fg
            surface.draw_box(0, 0, int(cols), int(rows), fg=border_fg, bg=panel_bg, title=None)

            title = f"{label_kind}: Minecraft JAR"
            surface.put(2, 0, title[: max(0, int(cols) - 4)], fg=fg, bg=panel_bg)
            dismiss = "[X]"
            dismiss_x = max(1, int(cols) - len(dismiss) - 2)
            surface.put(int(dismiss_x), 0, dismiss, fg=accent, bg=panel_bg)

            for i, ln in enumerate(wrapped[: max(0, int(rows) - 2)]):
                surface.put(1, 1 + i, ln[:inner_w], fg=muted, bg=panel_bg)

            panel_w_px = int(cols) * int(cell_w)
            panel_h_px = int(rows) * int(cell_h)
            panel_x_px = max(0, (int(view_w_px) - int(panel_w_px)) // 2)
            margin_y = max(0, int(round(float(view_h_px) * 0.06)))
            panel_y_px = int(view_h_px) - int(panel_h_px) - int(margin_y)
            panel_y_px = max(0, min(int(view_h_px) - int(panel_h_px), int(panel_y_px)))
            # Avoid overlapping the top-right --test-banner, which is drawn in window points.
            try:
                tb_bg = getattr(self, "test_banner_bg", None)
                if tb_bg is not None and bool(getattr(tb_bg, "visible", False)):
                    tb_bottom_pts = float(getattr(tb_bg, "y", 0.0))
                    tb_bottom_px = int(round(tb_bottom_pts * ratio))
                    gap_px = int(round(8.0 * ratio))
                    max_panel_top_px = int(tb_bottom_px) - int(gap_px)
                    if int(panel_y_px) + int(panel_h_px) > int(max_panel_top_px):
                        panel_y_px = max(0, int(int(max_panel_top_px) - int(panel_h_px)))
            except Exception:
                pass

            self._jar_term_panel_rect = (
                float(panel_x_px) / ratio,
                float(panel_y_px) / ratio,
                float(panel_w_px) / ratio,
                float(panel_h_px) / ratio,
            )
            dismiss_x0_px = int(panel_x_px + int(dismiss_x) * int(cell_w))
            dismiss_y0_px = int(panel_y_px + (int(panel_h_px) - int(cell_h)))  # top row
            self._jar_term_dismiss_rect = (
                float(dismiss_x0_px) / ratio,
                float(dismiss_y0_px) / ratio,
                (float(len(dismiss) * int(cell_w)) / ratio),
                (float(int(cell_h)) / ratio),
            )

            gl.glEnable(gl.GL_SCISSOR_TEST)
            gl.glScissor(int(panel_x_px), int(panel_y_px), max(1, int(panel_w_px)), max(1, int(panel_h_px)))
            gl.glViewport(int(panel_x_px), int(panel_y_px), max(1, int(panel_w_px)), max(1, int(panel_h_px)))
            renderer.draw(
                surface=surface,
                font=term_font,
                vp_w_px=int(panel_w_px),
                vp_h_px=int(panel_h_px),
                param_store=None,
                rez_active=False,
                clear=False,
            )
            gl.glViewport(0, 0, max(1, int(vp_w_px)), max(1, int(vp_h_px)))
            gl.glDisable(gl.GL_SCISSOR_TEST)

        def _layout_test_banner(self) -> None:
            text = str(getattr(self, "_test_banner_text", "") or "").strip()
            if not text:
                self.test_banner_bg.visible = False
                self.test_banner_label.visible = False
                return

            build = str(getattr(self, "_test_banner_build", "") or "unknown").strip()
            if build:
                banner_text = f"EnderTerm {build}\\nTEST: {text}"
            else:
                banner_text = f"TEST: {text}"

            key = (int(self.width), int(self.height), str(build), str(text))
            if key == getattr(self, "_test_banner_last_layout", None):
                return
            self._test_banner_last_layout = key

            margin = 10
            pad = 10
            max_w = int(round(min(float(self.width) * 0.66, 720.0)))
            max_w = max(260, min(int(self.width) - (2 * margin), int(max_w)))
            inner_w = max(1, int(max_w - (2 * pad)))

            self.test_banner_label.font_size = 14
            self.test_banner_label.width = int(inner_w)
            self.test_banner_label.text = str(banner_text)

            h = int(self.test_banner_label.content_height) + (2 * pad)
            h = max(34, int(h))

            x0 = int(self.width) - margin - int(max_w)
            y1 = int(self.height) - margin
            self.test_banner_bg.x = float(x0)
            self.test_banner_bg.y = float(y1 - h)
            self.test_banner_bg.width = float(max_w)
            self.test_banner_bg.height = float(h)
            self.test_banner_bg.visible = True

            self.test_banner_label.x = float(x0 + pad)
            self.test_banner_label.y = float(y1 - pad)
            self.test_banner_label.visible = True

        def _draw_test_banner(self) -> None:
            if not getattr(self, "_test_banner_text", ""):
                return
            self._layout_test_banner()

            vp_w, vp_h = self.get_viewport_size()
            gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))
            gl.glDisable(gl.GL_LIGHTING)
            gl.glDisable(gl.GL_DEPTH_TEST)
            gl.glDepthMask(gl.GL_TRUE)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glColor4f(1.0, 1.0, 1.0, 1.0)
            gl.glMatrixMode(gl.GL_PROJECTION)
            gl.glLoadIdentity()
            gl.glOrtho(0.0, float(self.width), 0.0, float(self.height), -1.0, 1.0)
            gl.glMatrixMode(gl.GL_MODELVIEW)
            gl.glLoadIdentity()

            gl.glDisable(gl.GL_TEXTURE_2D)
            test_banner_shape_batch.draw()
            gl.glEnable(gl.GL_TEXTURE_2D)
            test_banner_text_batch.draw()

        def on_resize(self, width: int, height: int) -> None:
            # On macOS Retina, `width/height` are in logical points but the OpenGL
            # framebuffer is larger; use the viewport size in pixels.
            vp_w, vp_h = self.get_viewport_size()
            gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))

        def on_mouse_press(self, x: int, y: int, button: int, modifiers: int) -> None:
            panel = getattr(self, "_jar_term_panel_rect", None)
            if panel is not None and (not getattr(self, "_jar_alert_dismissed", False)) and bool(
                getattr(self, "_jar_alert_text", "")
            ):
                bx, by, bw, bh = panel
                if float(bx) <= float(x) <= float(bx + bw) and float(by) <= float(y) <= float(by + bh):
                    dismiss = getattr(self, "_jar_term_dismiss_rect", None)
                    if dismiss is not None:
                        dx, dy, dw, dh = dismiss
                        if float(dx) <= float(x) <= float(dx + dw) and float(dy) <= float(y) <= float(dy + dh):
                            self._jar_alert_dismissed = True
                            self._jar_term_panel_rect = None
                            self._jar_term_dismiss_rect = None
                    # Swallow clicks on the alert panel so they don't rotate/pick the world.
                    return

        def on_mouse_drag(self, x: int, y: int, dx: int, dy: int, buttons: int, modifiers: int) -> None:
            self._last_drag_buttons = buttons
            if buttons & pyglet.window.mouse.LEFT:
                self.yaw += dx * 0.35
                self.pitch -= dy * 0.35
                self.pitch = max(-89.0, min(89.0, self.pitch))
            elif buttons & pyglet.window.mouse.MIDDLE:
                fov_rad = math.radians(55.0)
                units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                self.pan_x += dx * units_per_point
                self.pan_y += dy * units_per_point

        def on_mouse_scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
            factor = 0.9 ** scroll_y
            self.distance = max(0.5, self.distance * factor)

        def on_file_drop(self, x: int, y: int, paths: list[str]) -> None:
            for raw in paths:
                p = Path(str(raw)).expanduser()
                if p.suffix.lower() != ".jar":
                    continue
                self._apply_minecraft_jar_drop(p)
                return

        def _set_jar_alert(self, text: str, *, kind: str) -> None:
            self._jar_alert_text = str(text)
            self._jar_alert_kind = str(kind)
            self._jar_alert_dismissed = False
            self._jar_term_panel_rect = None
            self._jar_term_dismiss_rect = None

        def _apply_minecraft_jar_drop(self, path: Path) -> None:
            nonlocal jar_path, texture_source, textured

            try:
                p = Path(path).expanduser().resolve()
            except Exception:
                p = Path(path).expanduser()

            err = validate_minecraft_client_jar(p)
            if err is not None:
                self._set_jar_alert(
                    f"Dropped file is not a Minecraft client jar: {err}\n"
                    "Drop a vanilla Minecraft client .jar (it contains assets/minecraft/...).",
                    kind="error",
                )
                return

            save_configured_minecraft_jar_path(p)
            try:
                os.environ["MINECRAFT_JAR"] = str(p)
            except Exception:
                pass

            if texture_source is not None:
                try:
                    texture_source.close()
                except Exception:
                    pass
                texture_source = None

            try:
                jar_path = p
                texture_source = TextureSource(jar_path)
                textured = True
                build_geometry(texture_source)
            except Exception as e:
                jar_path = None
                texture_source = None
                textured = False
                build_geometry(None)
                self._set_jar_alert(
                    f"Failed to load textures: {type(e).__name__}: {e}\n"
                    "Drag-drop a different Minecraft client .jar to try again.",
                    kind="error",
                )
                return

            # Success: hide the jar banner if it was just the "no jar" warning.
            self._set_jar_alert("", kind="warn")

        def on_key_press(self, symbol: int, modifiers: int) -> None:
            if symbol in {pyglet.window.key.ESCAPE, pyglet.window.key.Q}:
                self.close()
            if symbol == pyglet.window.key.R:
                self.yaw = 45.0
                self.pitch = 25.0
                self.distance = initial_distance
                self.pan_x = 0.0
                self.pan_y = 0.0

        def on_draw(self) -> None:
            self.clear()
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)

            gl.glMatrixMode(gl.GL_PROJECTION)
            gl.glLoadIdentity()
            aspect = float(self.width) / float(max(1, self.height))
            gluPerspective(55.0, aspect, 0.05, 5000.0)

            gl.glMatrixMode(gl.GL_MODELVIEW)
            gl.glLoadIdentity()
            gl.glTranslatef(self.pan_x, self.pan_y, -self.distance)
            gl.glRotatef(self.pitch, 1.0, 0.0, 0.0)
            gl.glRotatef(self.yaw, 0.0, 1.0, 0.0)
            gl.glColor3f(1.0, 1.0, 1.0)

            alpha_test = False
            try:
                gl.glEnable(gl.GL_ALPHA_TEST)
                gl.glAlphaFunc(gl.GL_GREATER, 0.5)
                alpha_test = True
            except Exception:
                alpha_test = False
            try:
                batch.draw()
            finally:
                if alpha_test:
                    try:
                        gl.glDisable(gl.GL_ALPHA_TEST)
                    except Exception:
                        pass
            self._draw_test_banner()
            self._draw_jar_alert_termui()

    try:
        build_geometry(texture_source)
        ViewerWindow()
        print("Controls: left-drag rotate, middle-drag pan, scroll zoom, R reset, Esc/Q quit")
        print("Textures: drag-drop a Minecraft client .jar into the window to enable (or change) textures.")
        pyglet.app.run()
    finally:
        if texture_source is not None:
            try:
                texture_source.close()
            except Exception:
                pass
