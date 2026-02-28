from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType


def test_open_in_viewer_is_noop_when_path_missing(nbttool: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_popen(argv: list[str], **_kwargs: object) -> object:
        calls.append([str(x) for x in argv])
        return object()

    monkeypatch.setattr(nbttool.subprocess, "Popen", fake_popen)
    nbttool.open_in_viewer(Path("/definitely/does/not/exist"))
    assert calls == []


def test_open_in_viewer_uses_platform_launchers(nbttool: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_popen(argv: list[str], **_kwargs: object) -> object:
        calls.append([str(x) for x in argv])
        return object()

    def fake_which(cmd: str) -> str | None:
        if cmd in {"qlmanage", "xdg-open"}:
            return cmd
        return None

    monkeypatch.setattr(nbttool.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(nbttool.shutil, "which", fake_which)

    d = tmp_path / "d"
    d.mkdir()
    f = tmp_path / "f.txt"
    f.write_text("x")

    monkeypatch.setattr(nbttool.sys, "platform", "darwin")
    nbttool.open_in_viewer(d)
    nbttool.open_in_viewer(f)

    monkeypatch.setattr(nbttool.sys, "platform", "linux")
    nbttool.open_in_viewer(f)

    assert calls == [["open", str(d)], ["qlmanage", "-p", str(f)], ["xdg-open", str(f)]]


def test_tween_ease_and_done(nbttool: ModuleType) -> None:
    t = nbttool.Tween(start_t=10.0, duration_s=2.0, start=0.0, end=1.0, ease=nbttool.ease_smoothstep)
    assert t.value(10.0) == 0.0
    assert 0.0 < t.value(11.0) < 1.0
    assert t.value(12.0) == 1.0
    assert t.done(11.9) is False
    assert t.done(12.0) is True


def test_apply_element_rotation_handles_invalid_and_y_axis(nbttool: ModuleType) -> None:
    pts = [(0.5, 0.0, 0.0)]
    assert nbttool._apply_element_rotation(pts, None) == pts
    assert nbttool._apply_element_rotation(pts, {"axis": "y"}) == pts

    rot = {"origin": [8, 8, 8], "axis": "y", "angle": 90, "rescale": False}
    out = nbttool._apply_element_rotation(pts, rot)
    assert len(out) == 1
    x, y, z = out[0]
    assert y == 0.0
    assert abs(x) < 1e-6
    assert abs(z + 0.5) < 1e-6

    # Negative Z rotation should tilt +Y toward +X (used by wall torch models).
    rot_z = {"origin": [8, 8, 8], "axis": "z", "angle": -90, "rescale": False}
    out_z = nbttool._apply_element_rotation([(0.0, 0.5, 0.0)], rot_z)
    assert len(out_z) == 1
    xz, yz, zz = out_z[0]
    assert abs(zz) < 1e-6
    assert abs(xz - 0.5) < 1e-6
    assert abs(yz) < 1e-6

    rot_rescale = {"origin": [8, 8, 8], "axis": "x", "angle": 45, "rescale": True}
    out2 = nbttool._apply_element_rotation([(0.0, 0.5, 0.0)], rot_rescale)
    assert len(out2) == 1
    assert all(math.isfinite(v) for v in out2[0])

    # Near singular cosine must not trigger rescale blowups.
    rot_rescale_90 = {"origin": [8, 8, 8], "axis": "x", "angle": 90, "rescale": True}
    out3 = nbttool._apply_element_rotation([(0.0, 0.5, 0.0)], rot_rescale_90)
    assert len(out3) == 1
    x3, y3, z3 = out3[0]
    assert abs(x3) < 1e-6
    assert abs(y3) < 1e-6
    assert abs(z3 - 0.5) < 1e-6


def test_uv_helpers_support_rotation_and_quad_points(nbttool: ModuleType) -> None:
    uv = nbttool._default_uv_rect_for_face("up", frm=(0.0, 0.0, 0.0), to=(16.0, 16.0, 16.0))
    assert uv == (0.0, 0.0, 16.0, 16.0)

    quad0 = nbttool._uv_quad_from_rect((0.0, 0.0, 16.0, 16.0), rotation_deg=0)
    quad1 = nbttool._uv_quad_from_rect((0.0, 0.0, 16.0, 16.0), rotation_deg=90)
    assert quad0 != quad1
    assert quad1[0] == quad0[3]
    # Repeated calls should not share mutable list state.
    baseline_corner = nbttool._uv_quad_from_rect((0.0, 0.0, 16.0, 16.0), rotation_deg=0)[0]
    quad0[0] = (9.0, 9.0)
    assert nbttool._uv_quad_from_rect((0.0, 0.0, 16.0, 16.0), rotation_deg=0)[0] == baseline_corner

    tri = nbttool._uv_tri_for_face_rect("south", (0.0, 0.0, 16.0, 16.0))
    assert len(tri) == 12

    pts = [
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    tri2 = nbttool._uv_tri_for_face_rect("south", (0.0, 0.0, 16.0, 16.0), quad_points=pts)
    assert len(tri2) == 12
    assert all(0.0 <= float(v) <= 1.0 for v in tri2)
    pts_int = [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    tri3 = nbttool._uv_tri_for_face_rect("south", (0.0, 0.0, 16.0, 16.0), quad_points=pts_int)
    assert tri3 == tri2

    with pytest.raises(ValueError):
        nbttool._face_uv_axes("nope")


def test_face_uv_axes_matches_expected_orientation(nbttool: ModuleType) -> None:
    expected = {
        "south": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        "north": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        "west": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
        "east": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
        "up": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
        "down": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    }
    for face, axes in expected.items():
        assert nbttool._face_uv_axes(face) == axes


def test_uv_tri_for_face_rect_rejects_invalid_face_with_quad_points(nbttool: ModuleType) -> None:
    pts = [(-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)]
    with pytest.raises(ValueError):
        nbttool._uv_tri_for_face_rect("nope", (0.0, 0.0, 16.0, 16.0), quad_points=pts)


def test_uv_tri_for_face_rect_midpoint_ties_match_reference(nbttool: ModuleType) -> None:
    # p1 has u exactly on midpoint for south-face projection; strict `>` tie
    # behavior should still match the legacy/reference mapping.
    quad_points = [
        (-1.0, -1.0, 0.5),
        (0.0, -1.0, 0.5),
        (1.0, 1.0, 0.5),
        (-1.0, 1.0, 0.5),
    ]
    uv_rect = (0.0, 0.0, 16.0, 16.0)
    got = nbttool._uv_tri_for_face_rect("south", uv_rect, rotation_deg=0, quad_points=quad_points)
    want = _uv_tri_reference(nbttool, "south", uv_rect, rotation_deg=0, quad_points=quad_points)
    assert got == want


def _uv_tri_reference(
    nbttool: ModuleType,
    face: str,
    uv_rect: tuple[float, float, float, float],
    *,
    rotation_deg: int,
    quad_points: list[tuple[float, float, float]],
) -> tuple[float, ...]:
    uvq = nbttool._uv_quad_from_rect(uv_rect, rotation_deg=rotation_deg)
    axis_u, axis_v = nbttool._face_uv_axes(face)
    au0, au1, au2 = axis_u
    av0, av1, av2 = axis_v
    us = [(p[0] * au0) + (p[1] * au1) + (p[2] * au2) for p in quad_points]
    vs = [(p[0] * av0) + (p[1] * av1) + (p[2] * av2) for p in quad_points]
    u_mid = (min(us) + max(us)) * 0.5
    v_mid = (min(vs) + max(vs)) * 0.5

    per_vertex: list[tuple[float, float]] = []
    for u, v in zip(us, vs, strict=True):
        right = u > u_mid
        top = v > v_mid
        if top:
            per_vertex.append(uvq[1] if right else uvq[0])
        else:
            per_vertex.append(uvq[2] if right else uvq[3])

    a0, a1, a2, a3 = per_vertex
    return (*a0, *a1, *a2, *a0, *a2, *a3)


@pytest.mark.parametrize("face", ["north", "south", "west", "east", "down", "up"])
@pytest.mark.parametrize("rotation_deg", [0, 90, 180, 270])
def test_uv_tri_for_face_rect_matches_reference_mapping(nbttool: ModuleType, face: str, rotation_deg: int) -> None:
    quad_points = [
        (-0.4, -0.5, 0.5),
        (0.55, -0.45, 0.45),
        (0.5, 0.5, 0.25),
        (-0.45, 0.45, 0.65),
    ]
    uv_rect = (1.0, 2.0, 14.0, 15.0)
    got = nbttool._uv_tri_for_face_rect(face, uv_rect, rotation_deg=rotation_deg, quad_points=quad_points)
    want = _uv_tri_reference(nbttool, face, uv_rect, rotation_deg=rotation_deg, quad_points=quad_points)
    assert got == want


def test_tri_normal_and_mat4_ops(nbttool: ModuleType) -> None:
    assert nbttool._tri_normal((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    nx, ny, nz = nbttool._tri_normal((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    assert abs(nx) < 1e-6 and abs(ny) < 1e-6 and abs(nz - 1.0) < 1e-6

    ident = nbttool._mat4_identity()
    t = nbttool._mat4_translate(1.0, 2.0, 3.0)
    m = nbttool._mat4_mul(t, ident)
    assert nbttool._mat4_apply_point(m, (10.0, 20.0, 30.0)) == (11.0, 22.0, 33.0)
    assert nbttool._mat4_from_quat_xyzw((0.0, 0.0, 0.0, 1.0)) == ident


def test_nbt_helpers_convert_tags_to_plain_types(nbttool: ModuleType) -> None:
    assert nbttool._nbt_float_n([1, 2, 3], 3) == (1.0, 2.0, 3.0)
    assert nbttool._nbt_float_n([1, "x", 3], 3) is None
    assert nbttool._nbt_float_n([1, 2], 3) is None
    assert nbttool._nbt_float_n((1, 2, 3), 3) is None

    tag = nbttool.nbtlib.Compound(
        {
            "a": nbttool.nbtlib.Int(1),
            "b": nbttool.nbtlib.List[nbttool.nbtlib.Int]([nbttool.nbtlib.Int(2)]),
            "c": bytearray(b"hi"),
        }
    )
    plain = nbttool._nbt_to_plain(tag)
    assert plain == {"a": 1, "b": [2], "c": b"hi"}
