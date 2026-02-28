from __future__ import annotations

"""Pool expansion + rez worker helpers (extracted from legacy nbttool_impl)."""

from importlib import import_module as _import_module

_impl = _import_module("enderterm.nbttool_impl")
for _k, _v in _impl.__dict__.items():
    if _k in {"__name__", "__loader__", "__package__", "__spec__", "__file__", "__cached__"}:
        continue
    globals().setdefault(_k, _v)


T = TypeVar("T")


def _choose_weighted(rng: random.Random, candidates: list[tuple[T, int]]) -> T | None:
    total = 0
    for _, w in candidates:
        total += max(0, w)
    if total <= 0:
        return None
    r = rng.randrange(total)
    acc = 0
    for value, w in candidates:
        w = max(0, w)
        acc += w
        if r < acc:
            return value
    return candidates[-1][0] if candidates else None


def _apply_rule_processor_to_block(
    block: BlockInstance,
    *,
    location_state_id: str,
    processor: RuleProcessor,
    seed: int,
    processor_index: int,
) -> BlockInstance | None:
    input_state_id = block.block_id
    input_base = _block_id_base(input_state_id)
    location_base = _block_id_base(location_state_id)

    def matches_test(
        *,
        state_id: str,
        base: str,
        predicate_type: str,
        blocks_base: frozenset[str],
        block_states: frozenset[str],
        probability: float | None,
        salt: str,
        rule_index: int,
    ) -> bool:
        if predicate_type == "minecraft:always_true":
            return True
        if predicate_type in {"minecraft:block_match", "minecraft:random_block_match"}:
            if base not in blocks_base:
                return False
            if predicate_type == "minecraft:random_block_match":
                prob = probability if probability is not None else 1.0
                rseed = _stable_seed(seed, salt, processor_index, rule_index, block.pos)
                if random.Random(rseed).random() >= prob:
                    return False
            return True
        if predicate_type in {"minecraft:blockstate_match", "minecraft:random_blockstate_match"}:
            if state_id not in block_states:
                return False
            if predicate_type == "minecraft:random_blockstate_match":
                prob = probability if probability is not None else 1.0
                rseed = _stable_seed(seed, salt, processor_index, rule_index, block.pos)
                if random.Random(rseed).random() >= prob:
                    return False
            return True
        return False

    for rule_index, rule in enumerate(processor.rules):
        if not matches_test(
            state_id=input_state_id,
            base=input_base,
            predicate_type=rule.input_type,
            blocks_base=rule.input_blocks_base,
            block_states=rule.input_block_states,
            probability=rule.input_probability,
            salt="in",
            rule_index=rule_index,
        ):
            continue
        if not matches_test(
            state_id=location_state_id,
            base=location_base,
            predicate_type=rule.location_type,
            blocks_base=rule.location_blocks_base,
            block_states=rule.location_block_states,
            probability=rule.location_probability,
            salt="loc",
            rule_index=rule_index,
        ):
            continue

        out_id = rule.output_state_id
        out_base = _block_id_base(out_id)
        if out_base in {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void"}:
            return None
        return BlockInstance(pos=block.pos, block_id=out_id, color_key=out_id)
    return block


def _apply_processor_pipeline_to_blocks(
    blocks_by_pos: dict[Vec3i, BlockInstance],
    *,
    pipeline: ProcessorPipeline,
    seed: int,
    context: tuple[object, ...],
    existing_blocks_by_pos: dict[Vec3i, BlockInstance] | None,
    progress: Callable[[float], None] | None = None,
) -> dict[Vec3i, BlockInstance]:
    out = dict(blocks_by_pos)

    total_stages = max(1, len(pipeline.processors))
    last_progress_emit_s = 0.0
    min_progress_emit_s = 1.0 / 24.0
    max_progress_frac = 0.0

    def maybe_progress(frac: float) -> None:
        nonlocal last_progress_emit_s, max_progress_frac
        if progress is None:
            return
        frac = max(0.0, min(1.0, frac))
        if frac < max_progress_frac:
            frac = max_progress_frac
        else:
            max_progress_frac = frac
        now = time.monotonic()
        if now - last_progress_emit_s < min_progress_emit_s:
            return
        last_progress_emit_s = now
        try:
            progress(frac)
        except Exception:
            return

    for proc_index, spec in enumerate(pipeline.processors):
        stage_base = float(proc_index) / float(total_stages)
        stage_span = 1.0 / float(total_stages)
        stage_seed = _stable_seed(seed, "proc", proc_index, *context)

        if isinstance(spec, RuleProcessor):
            items = list(out.items())
            total_items = max(1, len(items))
            for item_index, (pos, block) in enumerate(items, start=1):
                if _block_id_base(block.block_id) == "minecraft:jigsaw":
                    continue
                existing = existing_blocks_by_pos.get(pos) if existing_blocks_by_pos is not None else None
                location_state_id = existing.block_id if existing is not None else "minecraft:air"
                updated = _apply_rule_processor_to_block(
                    block,
                    location_state_id=location_state_id,
                    processor=spec,
                    seed=stage_seed,
                    processor_index=proc_index,
                )
                if updated is None:
                    out.pop(pos, None)
                else:
                    out[pos] = updated
                if (item_index & 0xFF) == 0:
                    maybe_progress(stage_base + stage_span * (float(item_index) / float(total_items)))
            maybe_progress(stage_base + stage_span)
            continue

        if spec.limit <= 0:
            maybe_progress(stage_base + stage_span)
            continue

        candidates: list[tuple[Vec3i, BlockInstance | None]] = []
        total_items = max(1, len(out))
        for item_index, (pos, block) in enumerate(out.items(), start=1):
            if _block_id_base(block.block_id) == "minecraft:jigsaw":
                continue
            existing = existing_blocks_by_pos.get(pos) if existing_blocks_by_pos is not None else None
            location_state_id = existing.block_id if existing is not None else "minecraft:air"
            updated = _apply_rule_processor_to_block(
                block,
                location_state_id=location_state_id,
                processor=spec.delegate,
                seed=stage_seed,
                processor_index=proc_index,
            )
            if updated is None:
                candidates.append((pos, None))
            elif updated.block_id != block.block_id:
                candidates.append((pos, updated))
            if (item_index & 0xFF) == 0:
                maybe_progress(stage_base + stage_span * (float(item_index) / float(total_items)))

        if not candidates:
            maybe_progress(stage_base + stage_span)
            continue

        candidates.sort(key=lambda item: item[0])
        cap_seed = _stable_seed(seed, "cap", proc_index, *context)
        cap_rng = random.Random(cap_seed)
        cap_rng.shuffle(candidates)

        for pos, updated in candidates[: spec.limit]:
            if updated is None:
                out.pop(pos, None)
            else:
                out[pos] = updated

        maybe_progress(stage_base + stage_span)

    return out


def _placed_template_blocks(
    tmpl: StructureTemplate,
    *,
    rotation_y: int,
    translation: Vec3i,
    pipeline: ProcessorPipeline,
    apply_processors: bool = True,
    seed: int,
    context: tuple[object, ...],
    existing_blocks_by_pos: dict[Vec3i, BlockInstance],
    progress: Callable[[float], None] | None = None,
) -> dict[Vec3i, BlockInstance]:
    blocks_by_pos: dict[Vec3i, BlockInstance] = {}
    total_blocks = max(1, len(tmpl.blocks))
    place_span = 0.18 if (apply_processors and pipeline.processors) else 1.0
    for i, b in enumerate(tmpl.blocks, start=1):
        base = _block_id_base(b.block_id)
        if base in pipeline.ignore_base and base != "minecraft:jigsaw":
            continue
        rp = _rotate_y_pos(b.pos, rotation_y)
        wp = (rp[0] + translation[0], rp[1] + translation[1], rp[2] + translation[2])
        blocks_by_pos[wp] = BlockInstance(pos=wp, block_id=b.block_id, color_key=b.color_key)
        if progress is not None and (i & 0xFF) == 0:
            try:
                progress(place_span * (float(i) / float(total_blocks)))
            except Exception:
                pass

    if not apply_processors or not pipeline.processors:
        if progress is not None:
            try:
                progress(1.0)
            except Exception:
                pass
        return blocks_by_pos

    def proc_progress(frac: float) -> None:
        if progress is None:
            return
        try:
            progress(0.18 + 0.82 * max(0.0, min(1.0, frac)))
        except Exception:
            return

    out = _apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=seed,
        context=context,
        existing_blocks_by_pos=existing_blocks_by_pos,
        progress=proc_progress,
    )
    if progress is not None:
        try:
            progress(1.0)
        except Exception:
            pass
    return out


def build_jigsaw_expanded_structure(
    base: StructureTemplate,
    *,
    seeds: list[int],
    index: JigsawDatapackIndex,
    terrain_preset: str | None = None,
    terrain_seed: int = 0,
    terrain_origin_x: int = 0,
    terrain_origin_z: int = 0,
    terrain_anchor_off: int | None = None,
    terrain_base_y: int | None = None,
    terrain_amp: int | None = None,
    terrain_scale: float | None = None,
    terrain_octaves: int | None = None,
    terrain_lacunarity: float | None = None,
    terrain_h: float | None = None,
    terrain_ridged_offset: float | None = None,
    terrain_ridged_gain: float | None = None,
    throttle_sleep_ms: float = 0.0,
    throttle_every: int = 0,
    progress: Callable[[float, str], None] | None = None,
    piece_callback: Callable[[list[BlockInstance], str], None] | None = None,
    initial_structure: Structure | None = None,
    initial_state: JigsawExpansionState | None = None,
    initial_report: Iterable[str] | None = None,
    level_offset: int = 0,
    total_depth: int | None = None,
) -> tuple[Structure, list[str], JigsawExpansionState]:
    terrain_preset = terrain_preset if isinstance(terrain_preset, str) and terrain_preset else None
    if terrain_preset == "space":
        terrain_preset = None
    terrain_origin_x_value = int(terrain_origin_x) if isinstance(terrain_origin_x, int) else 0
    terrain_origin_z_value = int(terrain_origin_z) if isinstance(terrain_origin_z, int) else 0
    terrain_max_delta_value = ENV_HEIGHT_MAX_DELTA
    if isinstance(terrain_amp, int):
        terrain_max_delta_value = max(ENV_HEIGHT_MAX_DELTA, int(terrain_amp))

    terrain_anchor_x = 0
    terrain_anchor_z = 0
    terrain_anchor_off_value = 0
    if terrain_preset is not None:
        anchor_positions = [b.pos for b in base.blocks if _block_id_base(b.block_id) != "minecraft:structure_void"]
        if anchor_positions:
            xs = [p[0] for p in anchor_positions]
            zs = [p[2] for p in anchor_positions]
            terrain_anchor_x = (min(xs) + max(xs)) // 2
            terrain_anchor_z = (min(zs) + max(zs)) // 2
        if isinstance(terrain_anchor_off, int):
            terrain_anchor_off_value = int(terrain_anchor_off)
        else:
            terrain_anchor_off_value = env_height_offset(
                preset=terrain_preset,
                seed=int(terrain_seed),
                x=int(terrain_anchor_x) + int(terrain_origin_x_value),
                z=int(terrain_anchor_z) + int(terrain_origin_z_value),
                amp=terrain_amp,
                scale=terrain_scale,
                octaves=terrain_octaves,
                lacunarity=terrain_lacunarity,
                h=terrain_h,
                ridged_offset=terrain_ridged_offset,
                ridged_gain=terrain_ridged_gain,
            )

    def _terrain_delta_y(x: int, z: int, *, anchor_x: int, anchor_z: int, anchor_off: int) -> int:
        if terrain_preset is None:
            return 0
        off = env_height_offset(
            preset=terrain_preset,
            seed=int(terrain_seed),
            x=int(x) + int(terrain_origin_x_value),
            z=int(z) + int(terrain_origin_z_value),
            amp=terrain_amp,
            scale=terrain_scale,
            octaves=terrain_octaves,
            lacunarity=terrain_lacunarity,
            h=terrain_h,
            ridged_offset=terrain_ridged_offset,
            ridged_gain=terrain_ridged_gain,
        )
        dy = clamp_terrain_delta(int(off) - int(anchor_off), max_delta=int(terrain_max_delta_value))
        min_dy = int(WORLD_MIN_Y) - int(terrain_base_y_value)
        if dy < min_dy:
            dy = int(min_dy)
        return dy

    def _project_blocks_for_terrain_matching(
        placed: dict[Vec3i, BlockInstance],
        *,
        anchor_x: int,
        anchor_z: int,
        anchor_off: int,
    ) -> dict[Vec3i, BlockInstance]:
        if terrain_preset is None or not placed:
            return placed
        out: dict[Vec3i, BlockInstance] = {}
        for (x, y, z), b in placed.items():
            dy = _terrain_delta_y(x, z, anchor_x=anchor_x, anchor_z=anchor_z, anchor_off=anchor_off)
            if dy:
                wp = (int(x), int(y) + int(dy), int(z))
            else:
                wp = (int(x), int(y), int(z))
            out[wp] = BlockInstance(pos=wp, block_id=b.block_id, color_key=b.color_key)
        return out

    def _project_pos_i(pos: Vec3i, *, anchor_x: int, anchor_z: int, anchor_off: int) -> Vec3i:
        if terrain_preset is None:
            return pos
        x, y, z = pos
        dy = _terrain_delta_y(x, z, anchor_x=anchor_x, anchor_z=anchor_z, anchor_off=anchor_off)
        return (int(x), int(y) + int(dy), int(z))

    def _project_pos_f(pos: tuple[float, float, float], *, anchor_x: int, anchor_z: int, anchor_off: int) -> tuple[float, float, float]:
        if terrain_preset is None:
            return pos
        x, y, z = pos
        dy = _terrain_delta_y(int(math.floor(x)), int(math.floor(z)), anchor_x=anchor_x, anchor_z=anchor_z, anchor_off=anchor_off)
        return (float(x), float(y) + float(dy), float(z))

    if initial_structure is not None and initial_state is not None:
        blocks_by_pos: dict[Vec3i, BlockInstance] = {b.pos: b for b in initial_structure.blocks}
        block_entities_by_pos: dict[Vec3i, BlockEntityInstance] = {
            be.pos: be for be in initial_structure.block_entities
        }
        entities: list[EntityInstance] = [*initial_structure.entities]
        all_connectors: list[JigsawConnector] = [*initial_state.connectors]
        consumed: set[Vec3i] = set(initial_state.consumed)
        dead_end: set[Vec3i] = set(initial_state.dead_end)
        piece_bounds: list[tuple[int, int, int, int, int, int]] = list(getattr(initial_state, "piece_bounds", ()))
        if not piece_bounds and blocks_by_pos:
            xs = [p[0] for p in blocks_by_pos.keys()]
            ys = [p[1] for p in blocks_by_pos.keys()]
            zs = [p[2] for p in blocks_by_pos.keys()]
            piece_bounds = [(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))]
        report: list[str] = list(initial_report or ())
    else:
        blocks_by_pos = {b.pos: b for b in base.blocks}
        block_entities_by_pos = {be.pos: be for be in base.block_entities}
        entities = [*base.entities]
        all_connectors = [*base.connectors]
        consumed = set()
        dead_end = set()
        sx, sy, sz = base.size
        piece_bounds = [(0, 0, 0, int(sx) - 1, int(sy) - 1, int(sz) - 1)]
        report = list(initial_report or ())

    # In vanilla worldgen, any remaining jigsaw blocks are replaced with their
    # declared `final_state`. Do this up front so connectors never "pop away"
    # when the final structure is applied; use Ender Vision to inspect sockets.
    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, all_connectors)

    terrain_base_y_value = 0
    if terrain_preset is not None:
        if isinstance(terrain_base_y, int):
            terrain_base_y_value = int(terrain_base_y)
        elif blocks_by_pos:
            solids: set[Vec3i] = set()
            for p, b in blocks_by_pos.items():
                base_id = _block_id_base(b.block_id)
                if base_id in {"minecraft:jigsaw", "minecraft:structure_void"}:
                    continue
                solids.add((int(p[0]), int(p[1]), int(p[2])))
            if solids:
                counts: dict[int, int] = {}
                for x, y, z in solids:
                    if (int(x), int(y) - 1, int(z)) not in solids:
                        iy = int(y)
                        counts[iy] = int(counts.get(iy, 0)) + 1
                if counts:
                    max_count = max(counts.values())
                    min_support = max(8, int(round(float(max_count) * 0.35)))
                    candidates = [y for (y, c) in counts.items() if int(c) >= int(min_support)]
                    if candidates:
                        # Prefer a higher dominant layer if there are multiple bottoms.
                        terrain_base_y_value = int(max(candidates))
                    else:
                        best_y, _best = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
                        terrain_base_y_value = int(best_y)

    throttle_sleep_s = max(0.0, float(throttle_sleep_ms)) / 1000.0
    throttle_every_value = max(0, int(throttle_every))
    throttle_ctr = 0

    def throttle_tick() -> None:
        nonlocal throttle_ctr
        if throttle_sleep_s <= 1e-9 or throttle_every_value <= 0:
            return
        throttle_ctr += 1
        if throttle_ctr < throttle_every_value:
            return
        throttle_ctr = 0
        try:
            time.sleep(throttle_sleep_s)
        except Exception:
            return

    last_emit_s = 0.0
    min_emit_s = 1.0 / 24.0
    max_emit_frac = 0.0

    def emit(frac: float, msg: str, *, force: bool = False) -> None:
        nonlocal last_emit_s, max_emit_frac
        if progress is None:
            return
        frac = max(0.0, min(1.0, frac))
        if frac < max_emit_frac:
            frac = max_emit_frac
        else:
            max_emit_frac = frac
        now = time.monotonic()
        if not force and now - last_emit_s < min_emit_s:
            return
        last_emit_s = now
        try:
            progress(frac, msg)
        except Exception:
            return

    def is_open(conn: JigsawConnector) -> bool:
        return conn.pool not in {"", "minecraft:empty"} and conn.target not in {"", "minecraft:empty"}

    def is_collidable(block_state_id: str) -> bool:
        return _block_id_base(block_state_id) not in {"minecraft:jigsaw", "minecraft:structure_void"}

    def _bounds_intersect(a: tuple[int, int, int, int, int, int], b: tuple[int, int, int, int, int, int]) -> bool:
        ax0, ay0, az0, ax1, ay1, az1 = a
        bx0, by0, bz0, bx1, by1, bz1 = b
        return not (
            ax1 < bx0
            or bx1 < ax0
            or ay1 < by0
            or by1 < ay0
            or az1 < bz0
            or bz1 < az0
        )

    def _piece_bounds_for(
        tmpl: StructureTemplate,
        *,
        rotation_y: int,
        translation: Vec3i,
        proj_anchor: tuple[int, int] | None,
        proj_anchor_off: int,
    ) -> tuple[int, int, int, int, int, int]:
        sx, sy, sz = tmpl.size
        max_x = max(0, int(sx) - 1)
        max_y = max(0, int(sy) - 1)
        max_z = max(0, int(sz) - 1)
        corners: tuple[Vec3i, ...] = (
            (0, 0, 0),
            (max_x, 0, 0),
            (0, 0, max_z),
            (max_x, 0, max_z),
            (0, max_y, 0),
            (max_x, max_y, 0),
            (0, max_y, max_z),
            (max_x, max_y, max_z),
        )
        xs: list[int] = []
        ys: list[int] = []
        zs: list[int] = []
        for c in corners:
            rp = _rotate_y_pos(c, rotation_y)
            wp = (rp[0] + translation[0], rp[1] + translation[1], rp[2] + translation[2])
            if proj_anchor is not None:
                wp = _project_pos_i(wp, anchor_x=proj_anchor[0], anchor_z=proj_anchor[1], anchor_off=proj_anchor_off)
            xs.append(int(wp[0]))
            ys.append(int(wp[1]))
            zs.append(int(wp[2]))
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def _collides_piece_bounds(bounds: tuple[int, int, int, int, int, int]) -> bool:
        for pb in piece_bounds:
            if _bounds_intersect(bounds, pb):
                return True
        return False

    frontier = [c for c in all_connectors if is_open(c)]

    work_levels = max(1, len(seeds))
    global_total = total_depth if total_depth is not None else max(1, level_offset + len(seeds))
    for local_level, seed in enumerate(seeds, start=1):
        global_level = level_offset + local_level

        new_connectors: list[JigsawConnector] = []
        parents = [c for c in frontier if c.pos not in consumed and c.pos not in dead_end]
        parents.sort(key=lambda c: (c.pos, c.pool, c.target))
        # Pseudo-randomize connector scan order (stable for a given level seed),
        # so rezzing doesn't always "sweep" in the same spatial order.
        try:
            order_rng = random.Random(_stable_seed(seed, "connector_order", global_level))
            order_rng.shuffle(parents)
        except Exception:
            pass
        if not parents:
            report.append(f"L{global_level}: no open connectors")
            break
        report.append(f"L{global_level}: seed=0x{seed:08x} open={len(parents)}")

        rng = random.Random(seed)
        emit(float(local_level - 1) / float(work_levels), f"rezzing L{global_level}/{global_total}…", force=True)

        total_parents = max(1, len(parents))
        for parent_index, parent in enumerate(parents):
            if parent.pos in consumed or parent.pos in dead_end:
                continue

            parent_max_local = 0.0

            def emit_parent(local: float, msg: str, *, force: bool = False) -> None:
                nonlocal parent_max_local
                local = max(0.0, min(1.0, local))
                if local < parent_max_local:
                    local = parent_max_local
                else:
                    parent_max_local = local
                frac = (
                    float(local_level - 1)
                    + (float(parent_index) + local) / float(total_parents)
                ) / float(work_levels)
                emit(frac, msg, force=force)

            want_front = _vec_neg(parent.front)
            want_top = parent.top

            chosen_elem: PoolElement | None = None
            chosen_place: (
                tuple[
                    JigsawConnector,
                    int,
                    Vec3i,
                    dict[Vec3i, BlockInstance],
                    ProcessorPipeline,
                    tuple[int, int] | None,
                    int,
                    tuple[int, int, int, int, int, int],
                ]
                | None
            ) = None
            pool_used = parent.pool
            placements_by_loc: dict[
                str,
                list[
                    tuple[
                        JigsawConnector,
                        int,
                        Vec3i,
                        dict[Vec3i, BlockInstance],
                        ProcessorPipeline,
                        PoolElement,
                        tuple[int, int] | None,
                        int,
                        tuple[int, int, int, int, int, int],
                    ]
                ],
            ] = {}
            tried_pools: list[str] = []

            def _short_loc(s: str, *, max_len: int = 56) -> str:
                if len(s) <= max_len:
                    return s
                keep = max(1, max_len - 1)
                return "…" + s[-keep:]

            emit_parent(
                0.0,
                f"L{global_level}/{global_total} {parent_index + 1}/{total_parents} searching {parent.target}",
                force=True,
            )

            search_span = 0.98
            fallback_ratio = 0.12

            for depth_index in range(16):
                if pool_used in tried_pools:
                    break
                tried_pools.append(pool_used)

                pool_base = search_span * (1.0 - (fallback_ratio**depth_index))
                pool_seg = search_span * (1.0 - fallback_ratio) * (fallback_ratio**depth_index)

                pool_def = index.load_pool(pool_used)
                pool_elems = pool_def.elements
                if not pool_elems:
                    if pool_def.fallback in {"", "minecraft:empty"}:
                        break
                    pool_used = pool_def.fallback
                    continue

                placements_by_loc.clear()
                weighted_candidates: list[tuple[PoolElement, int]] = []

                pool_total = max(1, len(pool_elems))
                for elem_index, elem in enumerate(pool_elems):
                    scan_msg = (
                        f"L{global_level}/{global_total} {parent_index + 1}/{total_parents} "
                        f"scan {pool_used}  {_short_loc(elem.location_id)}"
                    )
                    emit_parent(pool_base + pool_seg * (float(elem_index) / float(pool_total)), scan_msg)
                    tmpl = index.load_template(elem.location_id)
                    if tmpl is None:
                        emit_parent(pool_base + pool_seg * (float(elem_index + 1) / float(pool_total)), scan_msg)
                        continue
                    matches = [c for c in tmpl.connectors if c.name == parent.target]
                    if not matches:
                        emit_parent(pool_base + pool_seg * (float(elem_index + 1) / float(pool_total)), scan_msg)
                        continue

                    pipeline = index.load_processor_list(elem.processors)

                    local_placements: list[
                        tuple[
                            JigsawConnector,
                            int,
                            Vec3i,
                            dict[Vec3i, BlockInstance],
                            ProcessorPipeline,
                            PoolElement,
                            tuple[int, int] | None,
                            int,
                            tuple[int, int, int, int, int, int],
                        ]
                    ] = []
                    total_candidates = max(1, len(matches) * 4)
                    done_candidates = 0
                    for child_local in matches:
                        require_top = parent.joint != "rollable" and child_local.joint != "rollable"
                        for q in (0, 1, 2, 3):
                            throttle_tick()
                            cand_i = done_candidates
                            done_candidates += 1
                            scan_start = (float(elem_index) + (float(cand_i) / float(total_candidates))) / float(pool_total)
                            emit_parent(pool_base + pool_seg * scan_start, scan_msg)

                            child_front_rot = _rotate_y_vec(child_local.front, q)
                            if child_front_rot != want_front:
                                continue
                            if require_top and _rotate_y_vec(child_local.top, q) != want_top:
                                continue

                            child_world_pos = _vec_add(parent.pos, parent.front)
                            child_conn_pos_rot = _rotate_y_pos(child_local.pos, q)
                            proj_anchor = None
                            proj_anchor_off = 0
                            if terrain_preset is not None and elem.projection == "terrain_matching":
                                proj_anchor = (int(terrain_anchor_x), int(terrain_anchor_z))
                                proj_anchor_off = int(terrain_anchor_off_value)
                                # Terrain-matching blocks are projected in Y after placement.
                                # To keep pieces aligned to the same heightfield as the environment
                                # (and avoid cumulative vertical drift), preserve the parent's
                                # "unprojected" Y at the connector:
                                #   y_unproj = y - dy(x,z)
                                dy_parent = _terrain_delta_y(
                                    int(parent.pos[0]),
                                    int(parent.pos[2]),
                                    anchor_x=int(proj_anchor[0]),
                                    anchor_z=int(proj_anchor[1]),
                                    anchor_off=int(proj_anchor_off),
                                )
                                dy_child = _terrain_delta_y(
                                    int(child_world_pos[0]),
                                    int(child_world_pos[2]),
                                    anchor_x=int(proj_anchor[0]),
                                    anchor_z=int(proj_anchor[1]),
                                    anchor_off=int(proj_anchor_off),
                                )
                                base_unproj_y = int(terrain_base_y_value)
                                if parent.projection == "terrain_matching":
                                    base_unproj_y = int(parent.pos[1]) - int(dy_parent)
                                child_world_pos = (
                                    int(child_world_pos[0]),
                                    int(base_unproj_y) + int(dy_child),
                                    int(child_world_pos[2]),
                                )
                                ty = int(child_world_pos[1]) - int(child_conn_pos_rot[1]) - int(dy_child)
                            else:
                                ty = int(child_world_pos[1]) - int(child_conn_pos_rot[1])
                            t = (
                                int(child_world_pos[0]) - int(child_conn_pos_rot[0]),
                                int(ty),
                                int(child_world_pos[2]) - int(child_conn_pos_rot[2]),
                            )

                            bounds = _piece_bounds_for(
                                tmpl,
                                rotation_y=q,
                                translation=t,
                                proj_anchor=proj_anchor,
                                proj_anchor_off=proj_anchor_off,
                            )
                            if _collides_piece_bounds(bounds):
                                scan_end = (float(elem_index) + (float(cand_i + 1) / float(total_candidates))) / float(pool_total)
                                emit_parent(pool_base + pool_seg * scan_end, scan_msg)
                                continue

                            place_context = (
                                global_level,
                                pool_used,
                                parent.pos,
                                parent.pool,
                                parent.target,
                                elem.location_id,
                                elem.processors,
                                q,
                                t,
                            )
                            place_seed = _stable_seed(seed, "place", *place_context)

                            def place_progress(frac: float) -> None:
                                scan_t = (
                                    float(elem_index)
                                    + ((float(cand_i) + max(0.0, min(1.0, frac))) / float(total_candidates))
                                ) / float(pool_total)
                                emit_parent(pool_base + pool_seg * scan_t, scan_msg)

                            placed_blocks = _placed_template_blocks(
                                tmpl,
                                rotation_y=q,
                                translation=t,
                                pipeline=pipeline,
                                apply_processors=False,
                                seed=place_seed,
                                context=place_context,
                                existing_blocks_by_pos=blocks_by_pos,
                                progress=place_progress,
                            )
                            placed_blocks_coll = placed_blocks
                            if proj_anchor is not None:
                                placed_blocks_coll = _project_blocks_for_terrain_matching(
                                    placed_blocks,
                                    anchor_x=proj_anchor[0],
                                    anchor_z=proj_anchor[1],
                                    anchor_off=proj_anchor_off,
                                )
                            scan_end = (float(elem_index) + (float(cand_i + 1) / float(total_candidates))) / float(pool_total)
                            emit_parent(pool_base + pool_seg * scan_end, scan_msg)

                            collides = False
                            for b in placed_blocks_coll.values():
                                existing = blocks_by_pos.get(b.pos)
                                if existing is None:
                                    continue
                                if not is_collidable(existing.block_id) or not is_collidable(b.block_id):
                                    continue
                                collides = True
                                break
                            if collides:
                                continue
                            local_placements.append(
                                (child_local, q, t, placed_blocks, pipeline, elem, proj_anchor, proj_anchor_off, bounds)
                            )

                    if not local_placements:
                        emit_parent(pool_base + pool_seg * (float(elem_index + 1) / float(pool_total)), scan_msg)
                        continue
                    placements_by_loc[elem.location_id] = local_placements
                    weighted_candidates.append((elem, elem.weight))
                    emit_parent(pool_base + pool_seg * (float(elem_index + 1) / float(pool_total)), scan_msg)

                remaining_candidates = list(weighted_candidates)
                while remaining_candidates:
                    cand = _choose_weighted(rng, remaining_candidates)
                    if cand is None:
                        break
                    placements = list(placements_by_loc.get(cand.location_id) or [])
                    if not placements:
                        remaining_candidates = [(e, w) for (e, w) in remaining_candidates if e.location_id != cand.location_id]
                        continue
                    rng.shuffle(placements)
                    placed_ok = None
                    for child_local, q, t, placed_blocks, pipeline, placement_elem, proj_anchor, proj_anchor_off, bounds in placements:
                        blocks_final = placed_blocks
                        if pipeline.processors:
                            place_context = (
                                global_level,
                                pool_used,
                                parent.pos,
                                parent.pool,
                                parent.target,
                                placement_elem.location_id,
                                placement_elem.processors,
                                q,
                                t,
                            )
                            place_seed = _stable_seed(seed, "place", *place_context)

                            def proc_progress(frac: float) -> None:
                                emit_parent(
                                    0.85 + 0.13 * max(0.0, min(1.0, frac)),
                                    f"processing {_short_loc(placement_elem.location_id)}",
                                )

                            blocks_final = _apply_processor_pipeline_to_blocks(
                                dict(blocks_final),
                                pipeline=pipeline,
                                seed=place_seed,
                                context=place_context,
                                existing_blocks_by_pos=blocks_by_pos,
                                progress=proc_progress,
                            )
                        if proj_anchor is not None:
                            blocks_final = _project_blocks_for_terrain_matching(
                                blocks_final,
                                anchor_x=proj_anchor[0],
                                anchor_z=proj_anchor[1],
                                anchor_off=proj_anchor_off,
                            )

                        collides = False
                        for b in blocks_final.values():
                            existing = blocks_by_pos.get(b.pos)
                            if existing is None:
                                continue
                            if not is_collidable(existing.block_id) or not is_collidable(b.block_id):
                                continue
                            collides = True
                            break
                        if collides:
                            continue
                        placed_ok = (child_local, q, t, blocks_final, pipeline, proj_anchor, proj_anchor_off, bounds)
                        chosen_elem = placement_elem
                        break

                    if placed_ok is not None:
                        chosen_place = placed_ok
                        break

                    # This element had collision-free placements pre-process, but all
                    # candidates collided after processors/projection. Try a different element.
                    remaining_candidates = [(e, w) for (e, w) in remaining_candidates if e.location_id != cand.location_id]

                if chosen_elem is not None and chosen_place is not None:
                    break
                if pool_def.fallback in {"", "minecraft:empty"}:
                    break
                pool_used = pool_def.fallback

            if chosen_elem is None:
                tried = f" (tried {','.join(tried_pools)})" if tried_pools else ""
                report.append(f"  {parent.pos}: pool {parent.pool} target {parent.target} -> no compatible pieces{tried}")
                emit_parent(
                    1.0,
                    f"L{global_level}/{global_total} {parent_index + 1}/{total_parents} no match",
                )
                # This connector was exhaustively searched and had no compatible placements.
                # Future levels only add more blocks/connectors; they can't make this connector valid later.
                dead_end.add(parent.pos)
                continue

            if chosen_place is None:
                continue
            child_local, q, t, placed_blocks, pipeline, proj_anchor, proj_anchor_off, chosen_bounds = chosen_place
            tmpl = index.load_template(chosen_elem.location_id)
            if tmpl is None:
                continue
            child_world_pos = _vec_add(parent.pos, parent.front)
            if proj_anchor is not None and chosen_elem.projection == "terrain_matching":
                dy_parent = _terrain_delta_y(
                    int(parent.pos[0]),
                    int(parent.pos[2]),
                    anchor_x=int(proj_anchor[0]),
                    anchor_z=int(proj_anchor[1]),
                    anchor_off=int(proj_anchor_off),
                )
                dy_child = _terrain_delta_y(
                    int(child_world_pos[0]),
                    int(child_world_pos[2]),
                    anchor_x=int(proj_anchor[0]),
                    anchor_z=int(proj_anchor[1]),
                    anchor_off=int(proj_anchor_off),
                )
                base_unproj_y = int(terrain_base_y_value)
                if parent.projection == "terrain_matching":
                    base_unproj_y = int(parent.pos[1]) - int(dy_parent)
                child_world_pos = (
                    int(child_world_pos[0]),
                    int(base_unproj_y) + int(dy_child),
                    int(child_world_pos[2]),
                )

            # Merge blocks.
            merged_blocks: list[BlockInstance] = []
            for b in placed_blocks.values():
                wp = b.pos
                existing = blocks_by_pos.get(wp)
                if existing is None:
                    blocks_by_pos[wp] = b
                    merged_blocks.append(b)
                    continue
                if _block_id_base(existing.block_id) == "minecraft:jigsaw" and _block_id_base(b.block_id) != "minecraft:jigsaw":
                    blocks_by_pos[wp] = b
                    # Don't report replacements for live-preview; adding overlapping geometry would z-fight.

            # Merge block entities (best-effort). Block entity NBT is only valid if
            # processors didn't swap the base block type.
            if tmpl.block_entities:
                tmpl_block_id_by_pos: dict[Vec3i, str] = {b.pos: b.block_id for b in tmpl.blocks}
                for be in tmpl.block_entities:
                    orig_id = tmpl_block_id_by_pos.get(be.pos)
                    if orig_id is None:
                        continue
                    rp = _rotate_y_pos(be.pos, q)
                    wp = (rp[0] + t[0], rp[1] + t[1], rp[2] + t[2])
                    if proj_anchor is not None:
                        wp = _project_pos_i(wp, anchor_x=proj_anchor[0], anchor_z=proj_anchor[1], anchor_off=proj_anchor_off)
                    if wp not in placed_blocks:
                        continue
                    placed = blocks_by_pos.get(wp)
                    if placed is None:
                        continue
                    base_placed = _block_id_base(placed.block_id)
                    if base_placed != _block_id_base(orig_id):
                        continue
                    if base_placed in {"minecraft:jigsaw", "minecraft:structure_void"}:
                        continue
                    block_entities_by_pos[wp] = BlockEntityInstance(pos=wp, nbt=be.nbt)

            # Merge entities.
            if tmpl.entities:
                for ent in tmpl.entities:
                    rp = _rotate_y_vec_f(ent.pos, q)
                    wp = (rp[0] + float(t[0]), rp[1] + float(t[1]), rp[2] + float(t[2]))
                    if proj_anchor is not None:
                        wp = _project_pos_f(
                            wp,
                            anchor_x=proj_anchor[0],
                            anchor_z=proj_anchor[1],
                            anchor_off=proj_anchor_off,
                        )
                    entities.append(EntityInstance(pos=wp, nbt=ent.nbt))

            # Transform connectors.
            piece_connectors: list[JigsawConnector] = []
            for c in tmpl.connectors:
                rp = _rotate_y_pos(c.pos, q)
                wp = (rp[0] + t[0], rp[1] + t[1], rp[2] + t[2])
                if proj_anchor is not None:
                    wp = _project_pos_i(wp, anchor_x=proj_anchor[0], anchor_z=proj_anchor[1], anchor_off=proj_anchor_off)
                wc = JigsawConnector(
                    pos=wp,
                    front=_rotate_y_vec(c.front, q),
                    top=_rotate_y_vec(c.top, q),
                    projection=str(chosen_elem.projection or "rigid"),
                    pool=c.pool,
                    target=c.target,
                    name=c.name,
                    final_state=c.final_state,
                    joint=c.joint,
                    source=c.source,
                )
                new_connectors.append(wc)
                piece_connectors.append(wc)

            # Hide sockets during normal rendering; Ender Vision shows them.
            if piece_connectors:
                apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, piece_connectors)

            # When a connection is made, Minecraft replaces the two consumed
            # jigsaw blocks with their declared `final_state`.
            def _apply_final_state(pos: Vec3i, final_state: str) -> None:
                bid = _block_id_from_jigsaw_final_state(final_state)
                if not bid:
                    return
                base_bid = _block_id_base(bid)
                if base_bid in {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void"}:
                    blocks_by_pos.pop(pos, None)
                    block_entities_by_pos.pop(pos, None)
                    return
                blocks_by_pos[pos] = BlockInstance(pos=pos, block_id=bid, color_key=bid)
                block_entities_by_pos.pop(pos, None)

            _apply_final_state(parent.pos, parent.final_state)
            _apply_final_state(child_world_pos, child_local.final_state)

            piece_bounds.append(chosen_bounds)

            consumed.add(parent.pos)
            consumed.add((child_world_pos[0], child_world_pos[1], child_world_pos[2]))
            extras = []
            if pool_used != parent.pool:
                extras.append(f"used_pool={pool_used}")
            if chosen_elem.projection and chosen_elem.projection != "rigid":
                extras.append(f"proj={chosen_elem.projection}")
            if chosen_elem.processors and chosen_elem.processors not in {"minecraft:empty", ""}:
                extras.append(f"proc={chosen_elem.processors}")
            unhandled_proc = set(pipeline.unhandled_processors)
            unhandled_pred: set[str] = set()
            for spec in pipeline.processors:
                if isinstance(spec, RuleProcessor):
                    unhandled_pred.update(spec.unhandled_predicates)
                else:
                    unhandled_pred.update(spec.delegate.unhandled_predicates)
            if unhandled_proc:
                extras.append(f"unhandled_proc={','.join(sorted(unhandled_proc))}")
            if unhandled_pred:
                extras.append(f"unhandled_pred={','.join(sorted(unhandled_pred))}")
            extra_s = f" ({' '.join(extras)})" if extras else ""
            report.append(
                f"  {parent.pos}: {parent.pool} target={parent.target} -> {chosen_elem.location_id} rotY={q*90} t={t}{extra_s}"
            )
            if piece_callback is not None and merged_blocks:
                try:
                    if piece_connectors:
                        merged_blocks = [
                            blocks_by_pos[b.pos]
                            for b in merged_blocks
                            if b.pos in blocks_by_pos
                        ]
                    piece_callback(merged_blocks, chosen_elem.location_id)
                except Exception:
                    pass
            emit_parent(
                1.0,
                f"L{global_level}/{global_total} {parent_index + 1}/{total_parents} {_short_loc(chosen_elem.location_id)}",
            )

        all_connectors.extend(new_connectors)
        apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, new_connectors)
        frontier = [c for c in all_connectors if is_open(c) and c.pos not in consumed and c.pos not in dead_end]

    emit(1.0, "rezzing done", force=True)

    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, all_connectors)

    if blocks_by_pos:
        xs = [p[0] for p in blocks_by_pos.keys()]
        ys = [p[1] for p in blocks_by_pos.keys()]
        zs = [p[2] for p in blocks_by_pos.keys()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)
        size = (max_x - min_x + 1, max_y - min_y + 1, max_z - min_z + 1)
    else:
        size = base.size

    merged = Structure(
        size=size,
        blocks=tuple(blocks_by_pos.values()),
        block_entities=tuple(sorted(block_entities_by_pos.values(), key=lambda be: be.pos)),
        entities=tuple(
            sorted(
                entities,
                key=lambda e: (e.pos[0], e.pos[1], e.pos[2], str(e.nbt.get("id", ""))),
            )
        ),
    )
    state = JigsawExpansionState(
        connectors=tuple(all_connectors),
        consumed=frozenset(consumed),
        dead_end=frozenset(dead_end),
        piece_bounds=tuple(piece_bounds),
    )
    return (merged, report, state)


def _rez_worker_main(
    out_q: "multiprocessing.queues.Queue[object]",
    *,
    gen: int,
    datapack_path: Path,
    work_pack_dir: Path | None = None,
    template_id: str,
    seeds: list[int],
    env_preset: str = "space",
    env_height_seed: int = 0,
    env_height_anchor_off: int = 0,
    env_height_origin_x: int = 0,
    env_height_origin_z: int = 0,
    env_height_amp: int | None = None,
    env_height_scale: float | None = None,
    env_height_octaves: int | None = None,
    env_height_lacunarity: float | None = None,
    env_height_h: float | None = None,
    env_height_ridged_offset: float | None = None,
    env_height_ridged_gain: float | None = None,
    env_surface_y: int = 0,
    rez_throttle_sleep_ms: float = 0.0,
    rez_throttle_every: int = 0,
    rez_pieces_per_s: float = 0.0,
    initial_structure: Structure | None = None,
    initial_state: JigsawExpansionState | None = None,
    initial_report: list[str] | None = None,
    level_offset: int = 0,
    total_depth: int | None = None,
) -> None:
    zip_file: zipfile.ZipFile | None = None

    def safe_put(msg: object) -> None:
        try:
            out_q.put_nowait(msg)
        except Exception:
            return

    try:
        # Always be polite: this worker should not steal UI responsiveness.
        # On POSIX, bump niceness to lower CPU scheduling priority.
        try:
            import os

            if hasattr(os, "nice"):
                os.nice(10)
        except Exception:
            pass

        if datapack_path.is_file() and datapack_path.suffix.lower() in {".zip", ".jar"}:
            zip_file = zipfile.ZipFile(datapack_path, "r")
        dp_source = DatapackSource(datapack_path, zip_file)
        if work_pack_dir is not None:
            try:
                pack_stack = PackStack(work_dir=Path(work_pack_dir), vendors=[dp_source])
                jigsaw_index = JigsawDatapackIndex(pack_stack.source)
            except Exception:
                jigsaw_index = JigsawDatapackIndex(dp_source)
        else:
            jigsaw_index = JigsawDatapackIndex(dp_source)
        base = jigsaw_index.load_template(template_id)
        if base is None:
            safe_put(("error", gen, f"missing base template: {template_id}"))
            return

        def cb(frac: float, msg: str) -> None:
            safe_put(("progress", gen, float(frac), str(msg)))

        import math as _math
        import time as _time

        piece_next_t = _time.monotonic()
        piece_interval_s = 0.0
        try:
            hz = float(rez_pieces_per_s)
            if _math.isfinite(hz) and hz > 1e-6:
                piece_interval_s = 1.0 / hz
        except Exception:
            piece_interval_s = 0.0

        def piece_cb(blocks: list[BlockInstance], loc: str) -> None:
            nonlocal piece_next_t
            if not blocks:
                return
            if piece_interval_s > 0.0:
                now = _time.monotonic()
                if now < piece_next_t:
                    try:
                        _time.sleep(piece_next_t - now)
                    except Exception:
                        pass
                now2 = _time.monotonic()
                piece_next_t = max(piece_next_t + piece_interval_s, now2)
            payload = [(b.pos, b.block_id, b.color_key) for b in blocks]
            safe_put(("piece", gen, payload, str(loc)))

        structure, report, state = build_jigsaw_expanded_structure(
            base,
            seeds=seeds,
            index=jigsaw_index,
            terrain_preset=str(env_preset),
            terrain_seed=int(env_height_seed),
            terrain_origin_x=int(env_height_origin_x),
            terrain_origin_z=int(env_height_origin_z),
            terrain_anchor_off=int(env_height_anchor_off),
            terrain_base_y=int(env_surface_y),
            terrain_amp=int(env_height_amp) if isinstance(env_height_amp, int) else None,
            terrain_scale=float(env_height_scale) if isinstance(env_height_scale, (int, float)) else None,
            terrain_octaves=int(env_height_octaves) if isinstance(env_height_octaves, int) else None,
            terrain_lacunarity=float(env_height_lacunarity) if isinstance(env_height_lacunarity, (int, float)) else None,
            terrain_h=float(env_height_h) if isinstance(env_height_h, (int, float)) else None,
            terrain_ridged_offset=float(env_height_ridged_offset) if isinstance(env_height_ridged_offset, (int, float)) else None,
            terrain_ridged_gain=float(env_height_ridged_gain) if isinstance(env_height_ridged_gain, (int, float)) else None,
            throttle_sleep_ms=float(rez_throttle_sleep_ms),
            throttle_every=int(rez_throttle_every),
            progress=cb,
            piece_callback=piece_cb,
            initial_structure=initial_structure,
            initial_state=initial_state,
            initial_report=initial_report,
            level_offset=level_offset,
            total_depth=total_depth,
        )
        safe_put(("result", gen, structure, report, state))
    except Exception as e:
        safe_put(("error", gen, f"{type(e).__name__}: {e}"))
    finally:
        try:
            if zip_file is not None:
                zip_file.close()
        except Exception:
            pass
