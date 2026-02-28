from __future__ import annotations

"""Minecraft cube/element face geometry + UV mapping helpers.

This module intentionally has no OpenGL/pyglet imports and can be imported by
test code and non-render subsystems.
"""

from functools import lru_cache
from typing import Literal, Sequence

TextureFace = Literal["up", "down", "north", "south", "west", "east"]

FACE_DIRS: tuple[TextureFace, ...] = ("north", "south", "west", "east", "down", "up")
FACE_NORMALS: dict[TextureFace, tuple[float, float, float]] = {
    "north": (0.0, 0.0, -1.0),
    "south": (0.0, 0.0, 1.0),
    "west": (-1.0, 0.0, 0.0),
    "east": (1.0, 0.0, 0.0),
    "down": (0.0, -1.0, 0.0),
    "up": (0.0, 1.0, 0.0),
}
FACE_NEIGHBOR_DELTA: dict[TextureFace, tuple[int, int, int]] = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
    "down": (0, -1, 0),
    "up": (0, 1, 0),
}


def _element_face_points(
    face: TextureFace,
    *,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
) -> list[tuple[float, float, float]]:
    if face == "north":  # -Z
        return [(xmax, ymin, zmin), (xmin, ymin, zmin), (xmin, ymax, zmin), (xmax, ymax, zmin)]
    if face == "south":  # +Z
        return [(xmin, ymin, zmax), (xmax, ymin, zmax), (xmax, ymax, zmax), (xmin, ymax, zmax)]
    if face == "west":  # -X
        return [(xmin, ymin, zmin), (xmin, ymin, zmax), (xmin, ymax, zmax), (xmin, ymax, zmin)]
    if face == "east":  # +X
        return [(xmax, ymin, zmax), (xmax, ymin, zmin), (xmax, ymax, zmin), (xmax, ymax, zmax)]
    if face == "down":  # -Y
        return [(xmin, ymin, zmin), (xmax, ymin, zmin), (xmax, ymin, zmax), (xmin, ymin, zmax)]
    if face == "up":  # +Y
        return [(xmin, ymax, zmax), (xmax, ymax, zmax), (xmax, ymax, zmin), (xmin, ymax, zmin)]
    raise ValueError(face)


_UNIT_CUBE_BOUNDS = (-0.5, 0.5)
_UNIT_CUBE_UV_QUAD_DEFAULT: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)


def _cube_face_quad_points(
    face: TextureFace, *, xmin: float, xmax: float, ymin: float, ymax: float, zmin: float, zmax: float
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    quad = _element_face_points(face, xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax, zmin=zmin, zmax=zmax)
    if len(quad) != 4:
        raise ValueError(face)
    p0, p1, p2, p3 = quad
    return (p0, p1, p2, p3)


def _cube_face_uv_quad(
    face: TextureFace,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]:
    if face == "north":
        # Preserve legacy north-face UV orientation when switching to canonical CCW
        # vertex winding for -Z (see `_element_face_points`).
        u0, u1, u2, u3 = _UNIT_CUBE_UV_QUAD_DEFAULT
        return (u1, u0, u3, u2)
    return _UNIT_CUBE_UV_QUAD_DEFAULT


def _cube_face_uv_tri(face: TextureFace) -> tuple[float, ...]:
    quad = _UNIT_CUBE_FACE_QUADS.get(face)
    if quad is None:
        u0, u1, u2, u3 = _cube_face_uv_quad(face)
        return (*u0, *u1, *u2, *u0, *u2, *u3)
    # Generate cube UVs via the same mapping code used for block-model elements.
    return _uv_tri_for_face_rect(face, (0.0, 0.0, 16.0, 16.0), quad_points=quad)


_UNIT_CUBE_FACE_QUADS: dict[TextureFace, tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = {
    face: _cube_face_quad_points(
        face,
        xmin=_UNIT_CUBE_BOUNDS[0],
        xmax=_UNIT_CUBE_BOUNDS[1],
        ymin=_UNIT_CUBE_BOUNDS[0],
        ymax=_UNIT_CUBE_BOUNDS[1],
        zmin=_UNIT_CUBE_BOUNDS[0],
        zmax=_UNIT_CUBE_BOUNDS[1],
    )
    for face in FACE_DIRS
}
_UNIT_CUBE_FACE_UV_TRI: dict[TextureFace, tuple[float, ...]] = {}


def _default_uv_rect_for_face(face: str, *, frm: tuple[float, float, float], to: tuple[float, float, float]) -> tuple[float, float, float, float]:
    fx, fy, fz = frm
    tx, ty, tz = to
    if face in {"north", "south"}:
        return (fx, 16.0 - ty, tx, 16.0 - fy)
    if face in {"west", "east"}:
        return (fz, 16.0 - ty, tz, 16.0 - fy)
    if face == "down":
        return (fx, 16.0 - tz, tx, 16.0 - fz)
    if face == "up":
        return (fx, fz, tx, tz)
    return (0.0, 0.0, 16.0, 16.0)


@lru_cache(maxsize=256)
def _uv_quad_from_rect_cached(
    u1: float, v1: float, u2: float, v2: float, steps: int
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]:
    umin = float(u1) / 16.0
    umax = float(u2) / 16.0
    # Minecraft UVs are in image space (origin top-left, +v down). Flip V for GL/USD.
    vtop = 1.0 - (float(v1) / 16.0)
    vbot = 1.0 - (float(v2) / 16.0)
    q0 = (umin, vtop)
    q1 = (umax, vtop)
    q2 = (umax, vbot)
    q3 = (umin, vbot)
    if steps == 1:
        return (q3, q0, q1, q2)
    if steps == 2:
        return (q2, q3, q0, q1)
    if steps == 3:
        return (q1, q2, q3, q0)
    return (q0, q1, q2, q3)


def _uv_quad_from_rect(uv: tuple[float, float, float, float], *, rotation_deg: int = 0) -> list[tuple[float, float]]:
    u1, v1, u2, v2 = uv
    steps = int(rotation_deg // 90) % 4 if rotation_deg else 0
    q0, q1, q2, q3 = _uv_quad_from_rect_cached(float(u1), float(v1), float(u2), float(v2), steps)
    # Keep API behavior: return a mutable list for callers/tests.
    return [q0, q1, q2, q3]


_FACE_UV_AXES: dict[TextureFace, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "south": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),  # +Z
    "north": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),  # -Z
    "west": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),  # -X
    "east": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),  # +X
    "up": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),  # +Y
    "down": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),  # -Y
}


def _face_uv_axes(face: TextureFace) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    # Return (axis_u, axis_v) for mapping quad points to uv corners.
    # The axes define which way is "right" and "up" on each face in block model space.
    try:
        return _FACE_UV_AXES[face]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ValueError(face) from exc


def _uv_tri_for_face_rect(
    face: TextureFace,
    uv_rect: tuple[float, float, float, float],
    *,
    rotation_deg: int = 0,
    quad_points: Sequence[tuple[float, float, float]] | None = None,
) -> tuple[float, ...]:
    u1, v1, u2, v2 = uv_rect
    steps = int(rotation_deg // 90) % 4 if rotation_deg else 0
    uvq = _uv_quad_from_rect_cached(float(u1), float(v1), float(u2), float(v2), steps)
    if quad_points is None:
        u0, u1, u2, u3 = uvq
        return (*u0, *u1, *u2, *u0, *u2, *u3)

    if len(quad_points) != 4:
        u0, u1, u2, u3 = uvq
        return (*u0, *u1, *u2, *u0, *u2, *u3)

    p0, p1, p2, p3 = quad_points
    if face == "south" or face == "north":
        u0, v0 = p0[0], p0[1]
        u1, v1 = p1[0], p1[1]
        u2, v2 = p2[0], p2[1]
        u3, v3 = p3[0], p3[1]
    elif face == "west":
        u0, v0 = p0[2], p0[1]
        u1, v1 = p1[2], p1[1]
        u2, v2 = p2[2], p2[1]
        u3, v3 = p3[2], p3[1]
    elif face == "east":
        u0, v0 = -p0[2], p0[1]
        u1, v1 = -p1[2], p1[1]
        u2, v2 = -p2[2], p2[1]
        u3, v3 = -p3[2], p3[1]
    elif face == "up":
        u0, v0 = p0[0], -p0[2]
        u1, v1 = p1[0], -p1[2]
        u2, v2 = p2[0], -p2[2]
        u3, v3 = p3[0], -p3[2]
    elif face == "down":
        u0, v0 = p0[0], p0[2]
        u1, v1 = p1[0], p1[2]
        u2, v2 = p2[0], p2[2]
        u3, v3 = p3[0], p3[2]
    else:
        raise ValueError(face)

    u_min = u0
    u_max = u0
    if u1 < u_min:
        u_min = u1
    elif u1 > u_max:
        u_max = u1
    if u2 < u_min:
        u_min = u2
    elif u2 > u_max:
        u_max = u2
    if u3 < u_min:
        u_min = u3
    elif u3 > u_max:
        u_max = u3
    u_mid = (u_min + u_max) * 0.5

    v_min = v0
    v_max = v0
    if v1 < v_min:
        v_min = v1
    elif v1 > v_max:
        v_max = v1
    if v2 < v_min:
        v_min = v2
    elif v2 > v_max:
        v_max = v2
    if v3 < v_min:
        v_min = v3
    elif v3 > v_max:
        v_max = v3
    v_mid = (v_min + v_max) * 0.5

    # `uvq` is in corner order: top-left, top-right, bottom-right, bottom-left.
    q0, q1, q2, q3 = uvq

    if v0 > v_mid:
        a0 = q1 if u0 > u_mid else q0
    else:
        a0 = q2 if u0 > u_mid else q3

    if v1 > v_mid:
        a1 = q1 if u1 > u_mid else q0
    else:
        a1 = q2 if u1 > u_mid else q3

    if v2 > v_mid:
        a2 = q1 if u2 > u_mid else q0
    else:
        a2 = q2 if u2 > u_mid else q3

    if v3 > v_mid:
        a3 = q1 if u3 > u_mid else q0
    else:
        a3 = q2 if u3 > u_mid else q3

    return (*a0, *a1, *a2, *a0, *a2, *a3)


_UNIT_CUBE_FACE_UV_TRI = {face: _cube_face_uv_tri(face) for face in FACE_DIRS}
