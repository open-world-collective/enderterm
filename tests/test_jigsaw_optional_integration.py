from __future__ import annotations

import os
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.optional
def test_expand_one_level_against_minecraft_jar(nbttool: ModuleType, tmp_path: Path) -> None:
    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()

    with zipfile.ZipFile(jar_path, "r") as zf:
        dp_source = nbttool.DatapackSource(jar_path, zf)
        stack = nbttool.PackStack(work_dir=tmp_path / "work-pack", vendors=[dp_source])
        index = nbttool.JigsawDatapackIndex(stack.source)

        jigsaw_structures = nbttool.list_worldgen_jigsaw_structures(stack)
        assert jigsaw_structures

        # Find a start piece with at least one open connector.
        base = None
        for structure_id in jigsaw_structures[:50]:
            obj = stack.source.read_json(nbttool.canonical_worldgen_structure_json(structure_id)) or {}
            start_pool = obj.get("start_pool")
            if not isinstance(start_pool, str) or not start_pool:
                continue
            pool_def = index.load_pool(start_pool)
            for elem in pool_def.elements[:50]:
                tmpl = index.load_template(elem.location_id)
                if tmpl is None:
                    continue
                open_conns = [
                    c
                    for c in tmpl.connectors
                    if c.pool not in {"", "minecraft:empty"} and c.target not in {"", "minecraft:empty"}
                ]
                if not open_conns:
                    continue
                # Keep the test fast: expand from only a couple of connectors.
                base = nbttool.StructureTemplate(
                    template_id=tmpl.template_id,
                    size=tmpl.size,
                    blocks=tmpl.blocks,
                    connectors=tuple(open_conns[:2]),
                    block_entities=tmpl.block_entities,
                    entities=tmpl.entities,
                )
                break
            if base is not None:
                break

        if base is None:
            pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for a quick expansion.")

        out, report, state = nbttool.build_jigsaw_expanded_structure(
            base,
            seeds=[0x12345678],
            index=index,
        )

        assert report
        assert isinstance(out.blocks, tuple)
        assert isinstance(state.connectors, tuple)
