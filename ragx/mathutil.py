"""Small 3D math helpers (4x4 row-major matrices, quaternions).

Conventions:
- Matrices are tuples/lists of 16 floats, row-major: m[row*4+col].
- Points are (x, y, z) tuples; transformation is M @ column-vector.
- Quaternions are (x, y, z, w), glTF order.
"""

from __future__ import annotations

import math

Matrix = tuple[float, ...]
Vec3 = tuple[float, float, float]

IDENTITY: Matrix = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def mat_mul(a: Matrix, b: Matrix) -> Matrix:
    out = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[row * 4 + col] = (
                a[row * 4 + 0] * b[0 * 4 + col]
                + a[row * 4 + 1] * b[1 * 4 + col]
                + a[row * 4 + 2] * b[2 * 4 + col]
                + a[row * 4 + 3] * b[3 * 4 + col]
            )
    return tuple(out)


def transform_point(m: Matrix, p: Vec3) -> Vec3:
    x, y, z = p
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def transform_direction(m: Matrix, p: Vec3) -> Vec3:
    x, y, z = p
    return (
        m[0] * x + m[1] * y + m[2] * z,
        m[4] * x + m[5] * y + m[6] * z,
        m[8] * x + m[9] * y + m[10] * z,
    )


def translation(t: Vec3) -> Matrix:
    return (
        1.0, 0.0, 0.0, t[0],
        0.0, 1.0, 0.0, t[1],
        0.0, 0.0, 1.0, t[2],
        0.0, 0.0, 0.0, 1.0,
    )


def scaling(s: Vec3) -> Matrix:
    return (
        s[0], 0.0, 0.0, 0.0,
        0.0, s[1], 0.0, 0.0,
        0.0, 0.0, s[2], 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def mat3_to_mat4(m3: tuple[float, ...], column_major: bool = True) -> Matrix:
    """Expand a 3x3 matrix (as stored in RSM files, column-major) to 4x4."""
    if column_major:
        a, b, c, d, e, f, g, h, i = m3
        # stored columns: (a,b,c), (d,e,f), (g,h,i)
        return (
            a, d, g, 0.0,
            b, e, h, 0.0,
            c, f, i, 0.0,
            0.0, 0.0, 0.0, 1.0,
        )
    a, b, c, d, e, f, g, h, i = m3
    return (
        a, b, c, 0.0,
        d, e, f, 0.0,
        g, h, i, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def axis_angle_matrix(axis: Vec3, angle: float) -> Matrix:
    x, y, z = axis
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12 or angle == 0.0:
        return IDENTITY
    x, y, z = x / length, y / length, z / length
    c = math.cos(angle)
    s = math.sin(angle)
    t = 1.0 - c
    return (
        t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0,
        t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0,
        t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def axis_angle_quat(axis: Vec3, angle: float) -> tuple[float, float, float, float]:
    x, y, z = axis
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12 or angle == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    half = angle / 2.0
    s = math.sin(half) / length
    return (x * s, y * s, z * s, math.cos(half))


def euler_rotation_matrix_zxy(rx: float, ry: float, rz: float) -> Matrix:
    """korangar's RSW object rotation: Rz(-rz) * Rx(-rx) * Ry(ry), radians."""
    return mat_mul(mat_mul(rot_z(-rz), rot_x(-rx)), rot_y(ry))


def rot_x(a: float) -> Matrix:
    c, s = math.cos(a), math.sin(a)
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, c, -s, 0.0,
        0.0, s, c, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def rot_y(a: float) -> Matrix:
    c, s = math.cos(a), math.sin(a)
    return (
        c, 0.0, s, 0.0,
        0.0, 1.0, 0.0, 0.0,
        -s, 0.0, c, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def rot_z(a: float) -> Matrix:
    c, s = math.cos(a), math.sin(a)
    return (
        c, -s, 0.0, 0.0,
        s, c, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def mat3_transpose4(m: Matrix) -> Matrix:
    """Transpose of the rotation part only (translation cleared)."""
    return (
        m[0], m[4], m[8], 0.0,
        m[1], m[5], m[9], 0.0,
        m[2], m[6], m[10], 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def matrix_to_quat(m: Matrix) -> tuple[float, float, float, float]:
    """Rotation part of a (pure-rotation) matrix to quaternion (x,y,z,w)."""
    m00, m01, m02 = m[0], m[1], m[2]
    m10, m11, m12 = m[4], m[5], m[6]
    m20, m21, m22 = m[8], m[9], m[10]
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    return (x, y, z, w)


def quat_to_matrix(q: tuple[float, float, float, float]) -> Matrix:
    """Quaternion (x,y,z,w) to a 4x4 row-major rotation matrix."""
    x, y, z, w = quat_normalize(q)
    return (
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0,
        2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0,
        2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def quat_normalize(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, z, w = q
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / length, y / length, z / length, w / length)


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def mirror_z_matrix(m: Matrix) -> Matrix:
    """Conjugate a transform by diag(1,1,-1): converts RO render space
    (left-handed, +Z north) to glTF space (right-handed, -Z north)."""
    out = list(m)
    out[2] = -out[2]    # m[0][2]
    out[6] = -out[6]    # m[1][2]
    out[8] = -out[8]    # m[2][0]
    out[9] = -out[9]    # m[2][1]
    out[11] = -out[11]  # translation z
    return tuple(out)


def mirror_z_quat(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, z, w = q
    return (-x, -y, z, w)


def mirror_z_point(p: Vec3) -> Vec3:
    return (p[0], p[1], -p[2])


def rot_x180_point(p: Vec3) -> Vec3:
    """RO model space (Y down) -> glTF space for baked RSM1 geometry:
    the Z-mirror (RO->glTF) combined with the Y-flip (Y-down->Y-up) is a
    pure 180-degree rotation about X — no reflection, winding preserved."""
    return (p[0], -p[1], -p[2])


def rot_x180_quat(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, z, w = q
    return (x, -y, -z, w)


def normalize(v: Vec3) -> Vec3:
    x, y, z = v
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        return (0.0, 1.0, 0.0)
    return (x / length, y / length, z / length)


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
