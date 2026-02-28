from __future__ import annotations

"""Small 3D math + transform helpers (no OpenGL imports)."""

import math


_ROTATION_AXES = {"x", "y", "z"}
_ROTATION_COS_EPSILON = 1e-6
_ZERO_NORMAL_EPSILON = 1e-12

Vec3 = tuple[float, float, float]


def _apply_axis_rescale(axis: str, x: float, y: float, z: float, scale: float) -> Vec3:
    if scale == 1.0:
        return (x, y, z)
    if axis == "x":
        return (x, y * scale, z * scale)
    if axis == "y":
        return (x * scale, y, z * scale)
    return (x * scale, y * scale, z)


def _rotate_about_axis(axis: str, x: float, y: float, z: float, cos_theta: float, sin_theta: float) -> Vec3:
    if axis == "x":
        return (x, y * cos_theta - z * sin_theta, y * sin_theta + z * cos_theta)
    if axis == "y":
        return (x * cos_theta + z * sin_theta, y, -x * sin_theta + z * cos_theta)
    return (x * cos_theta - y * sin_theta, x * sin_theta + y * cos_theta, z)


def _parse_rotation_origin(origin_obj: object) -> Vec3 | None:
    if not (isinstance(origin_obj, list) and len(origin_obj) == 3):
        return None
    try:
        ox = float(origin_obj[0]) / 16.0 - 0.5
        oy = float(origin_obj[1]) / 16.0 - 0.5
        oz = float(origin_obj[2]) / 16.0 - 0.5
    except (TypeError, ValueError):
        return None
    return (ox, oy, oz)


def _parse_rotation_spec(rot_obj: object) -> tuple[str, Vec3, float, bool] | None:
    if not isinstance(rot_obj, dict):
        return None
    axis = rot_obj.get("axis")
    angle = rot_obj.get("angle")
    if not (isinstance(axis, str) and axis in _ROTATION_AXES):
        return None
    origin = _parse_rotation_origin(rot_obj.get("origin"))
    if origin is None:
        return None
    try:
        angle_deg = float(angle)
    except (TypeError, ValueError):
        return None
    return (axis, origin, angle_deg, bool(rot_obj.get("rescale", False)))


def _rescale_factor(cos_theta: float, *, rescale: bool) -> float:
    if rescale and abs(cos_theta) > _ROTATION_COS_EPSILON:
        return 1.0 / cos_theta
    return 1.0


def _translate_from_origin(point: Vec3, origin: Vec3) -> Vec3:
    return (point[0] - origin[0], point[1] - origin[1], point[2] - origin[2])


def _translate_to_origin(point: Vec3, origin: Vec3) -> Vec3:
    return (point[0] + origin[0], point[1] + origin[1], point[2] + origin[2])


def _apply_element_rotation(points: list[tuple[float, float, float]], rot_obj: object) -> list[tuple[float, float, float]]:
    rotation = _parse_rotation_spec(rot_obj)
    if rotation is None:
        return points
    axis, origin, angle_deg, rescale = rotation

    # Model-element rotations use the JSON angle sign directly (right-hand rule).
    theta = math.radians(angle_deg)
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    scale = _rescale_factor(cos_theta, rescale=rescale)

    out: list[tuple[float, float, float]] = []
    for point in points:
        x, y, z = _translate_from_origin(point, origin)
        x, y, z = _apply_axis_rescale(axis, x, y, z, scale)
        x2, y2, z2 = _rotate_about_axis(axis, x, y, z, cos_theta, sin_theta)
        out.append(_translate_to_origin((x2, y2, z2), origin))
    return out


def _vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vec_cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _vec_normalize(v: Vec3) -> Vec3:
    nx, ny, nz = v
    mag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if mag <= _ZERO_NORMAL_EPSILON:
        return (0.0, 0.0, 0.0)
    inv = 1.0 / mag
    return (nx * inv, ny * inv, nz * inv)


def _tri_normal(p0: tuple[float, float, float], p1: tuple[float, float, float], p2: tuple[float, float, float]) -> tuple[float, float, float]:
    edge_a = _vec_sub(p1, p0)
    edge_b = _vec_sub(p2, p0)
    return _vec_normalize(_vec_cross(edge_a, edge_b))


Mat4 = tuple[
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
]


def _mat4_from_flat(values: list[float] | tuple[float, ...]) -> Mat4:
    if len(values) != 16:
        raise ValueError("mat4 requires exactly 16 values")
    return tuple(float(v) for v in values)  # type: ignore[return-value]


def _mat4_identity() -> Mat4:
    return _mat4_from_flat(
        (
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        )
    )


def _mat4_mul(a: Mat4, b: Mat4) -> Mat4:
    out: list[float] = [0.0] * 16
    for r in range(4):
        r0 = r * 4
        for c in range(4):
            out[r0 + c] = (
                a[r0 + 0] * b[0 * 4 + c]
                + a[r0 + 1] * b[1 * 4 + c]
                + a[r0 + 2] * b[2 * 4 + c]
                + a[r0 + 3] * b[3 * 4 + c]
            )
    return _mat4_from_flat(out)


def _mat4_translate(tx: float, ty: float, tz: float) -> Mat4:
    return _mat4_from_flat(
        (
            1.0,
            0.0,
            0.0,
            tx,
            0.0,
            1.0,
            0.0,
            ty,
            0.0,
            0.0,
            1.0,
            tz,
            0.0,
            0.0,
            0.0,
            1.0,
        )
    )


def _mat4_scale(sx: float, sy: float, sz: float) -> Mat4:
    return _mat4_from_flat(
        (
            sx,
            0.0,
            0.0,
            0.0,
            0.0,
            sy,
            0.0,
            0.0,
            0.0,
            0.0,
            sz,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        )
    )


def _mat4_apply_point(m: Mat4, p: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = p
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def _mat4_from_quat_xyzw(q: tuple[float, float, float, float]) -> Mat4:
    x, y, z, w = q
    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z
    return _mat4_from_flat(
        (
            1.0 - 2.0 * (yy + zz),
            2.0 * (xy - wz),
            2.0 * (xz + wy),
            0.0,
            2.0 * (xy + wz),
            1.0 - 2.0 * (xx + zz),
            2.0 * (yz - wx),
            0.0,
            2.0 * (xz - wy),
            2.0 * (yz + wx),
            1.0 - 2.0 * (xx + yy),
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        )
    )


def _nbt_float_n(obj: object, n: int) -> tuple[float, ...] | None:
    if not (isinstance(obj, list) and len(obj) == n):
        return None
    out: list[float] = []
    for v in obj:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            return None
    return tuple(out)


def _nbt_to_plain(obj: object) -> object:
    # Convert `nbtlib` tags to plain Python types so they're safe to pass through
    # multiprocessing queues (notably `nbtlib.List[Foo]` isn't picklable).
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool, bytes)):
        return obj
    if isinstance(obj, (bytearray, memoryview)):
        return bytes(obj)
    if isinstance(obj, dict):
        out: dict[str, object] = {}
        for k, v in obj.items():
            out[str(k)] = _nbt_to_plain(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_nbt_to_plain(v) for v in obj]
    try:
        return [_nbt_to_plain(v) for v in obj]  # type: ignore[operator]
    except Exception:
        return str(obj)
