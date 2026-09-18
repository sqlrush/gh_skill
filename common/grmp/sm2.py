"""国密 SM2 数字签名(GB/T 32918.2-2016,曲线 sm2p256v1),纯 Python。

给 GRMP 请求头 Signature 用:每次请求签一段几十字节的文本。不引第三方包
(交付镜像的 Python 依赖是客户白名单管的);正确性靠三层对照钉住——标准附录的
固定 k 向量、与 gmssl 互签互验、OpenSSL 3 生成的 PEM 私钥与 DER 签名能解能验
(tests/test_sm2_units.py)。

私钥文本接受两种形态:64 位 hex(可带 0x)、PEM(PKCS#8 或 SEC1 ECPrivateKey);
公钥接受 hex(X‖Y,可带 04 前缀)与 PEM(SubjectPublicKeyInfo)。签名编码 raw(r‖s 64 字节)
或 DER;文本化 hex 或 base64——客户 Java 侧默认 DER,Python 库默认 raw,都得能出。
"""
from __future__ import annotations

import base64
import binascii
import re
import secrets
from typing import Iterator, List, Optional, Tuple

from .sm3 import sm3_digest

# ---------------------------------------------------------------- 曲线参数(sm2p256v1)
P = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
A = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC
B = 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93
N = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
GX = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
GY = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0
DEFAULT_USER_ID = b"1234567812345678"     # 国标默认 ID,Java(BouncyCastle/hutool)与 OpenSSL 的默认值都是它

Point = Optional[Tuple[int, int]]        # None = 无穷远点


class Sm2Error(Exception):
    """密钥或签名的形态不对——都是配置问题,要报得能看懂。"""


# ---------------------------------------------------------------- 点运算(仿射坐标)

def _inv(x: int, m: int) -> int:
    return pow(x, -1, m)


def _add(p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + A) * _inv(2 * y1, P) % P
    else:
        lam = (y2 - y1) * _inv(x2 - x1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k: int, point: Point) -> Point:
    result: Point = None
    addend = point
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result


def _on_curve(point: Tuple[int, int]) -> bool:
    x, y = point
    return 0 < x < P and 0 < y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def public_key(d: int) -> Tuple[int, int]:
    point = _mul(d, (GX, GY))
    if point is None:
        raise Sm2Error("私钥不合法(d·G 为无穷远点)")
    return point


def generate_private_key() -> int:
    return secrets.randbelow(N - 1) + 1


# ---------------------------------------------------------------- 签名 / 验签

def _za(user_id: bytes, pub: Tuple[int, int]) -> bytes:
    entl = len(user_id) * 8
    if entl >= 1 << 16:
        raise Sm2Error("用户 ID 过长")
    parts = [entl.to_bytes(2, "big"), user_id] + [v.to_bytes(32, "big") for v in (A, B, GX, GY, pub[0], pub[1])]
    return sm3_digest(b"".join(parts))


def _e(msg: bytes, user_id: bytes, pub: Tuple[int, int]) -> int:
    return int.from_bytes(sm3_digest(_za(user_id, pub) + msg), "big")


def sign(d: int, msg: bytes, user_id: bytes = DEFAULT_USER_ID, k: Optional[int] = None) -> Tuple[int, int]:
    """返回 (r, s)。k 只在测试对照标准向量时指定;生产永远随机。"""
    if not 0 < d < N:
        raise Sm2Error("私钥不在 [1, n-1] 内")
    e = _e(msg, user_id, public_key(d))
    while True:
        kk = k if k is not None else secrets.randbelow(N - 1) + 1
        x1, _ = _mul(kk, (GX, GY))
        r = (e + x1) % N
        if r == 0 or r + kk == N:
            if k is not None:
                raise Sm2Error("指定的 k 不可用")
            continue
        s = _inv(1 + d, N) * (kk - r * d) % N
        if s == 0:
            if k is not None:
                raise Sm2Error("指定的 k 不可用")
            continue
        return r, s


def verify(pub: Tuple[int, int], msg: bytes, user_id: bytes, r: int, s: int) -> bool:
    if not (0 < r < N and 0 < s < N) or not _on_curve(pub):
        return False
    t = (r + s) % N
    if t == 0:
        return False
    point = _add(_mul(s, (GX, GY)), _mul(t, pub))
    if point is None:
        return False
    return (_e(msg, user_id, pub) + point[0]) % N == r


# ---------------------------------------------------------------- 签名编码

def encode_signature(r: int, s: int, fmt: str) -> bytes:
    if fmt == "raw":
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")
    if fmt == "der":
        return _der_seq(_der_int(r) + _der_int(s))
    raise Sm2Error("签名格式只能是 raw 或 der,不是 %r" % fmt)


def decode_signature(data: bytes, fmt: str) -> Tuple[int, int]:
    if fmt == "raw":
        if len(data) != 64:
            raise Sm2Error("raw 签名应为 64 字节,实际 %d" % len(data))
        return int.from_bytes(data[:32], "big"), int.from_bytes(data[32:], "big")
    if fmt == "der":
        items = list(_der_iter(data))
        if len(items) != 1 or items[0][0] != 0x30:
            raise Sm2Error("DER 签名不是一个 SEQUENCE")
        ints = list(_der_iter(items[0][1]))
        if len(ints) != 2 or any(tag != 0x02 for tag, _ in ints):
            raise Sm2Error("DER 签名里不是两个 INTEGER")
        return int.from_bytes(ints[0][1], "big"), int.from_bytes(ints[1][1], "big")
    raise Sm2Error("签名格式只能是 raw 或 der,不是 %r" % fmt)


def to_text(sig: bytes, encoding: str) -> str:
    if encoding == "hex":
        return sig.hex()
    if encoding == "base64":
        return base64.b64encode(sig).decode("ascii")
    raise Sm2Error("签名编码只能是 hex 或 base64,不是 %r" % encoding)


def from_text(text: str, encoding: str) -> bytes:
    try:
        if encoding == "hex":
            return bytes.fromhex(text.strip())
        if encoding == "base64":
            return base64.b64decode(text.strip(), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise Sm2Error("签名文本不是合法的 %s:%s" % (encoding, exc)) from exc
    raise Sm2Error("签名编码只能是 hex 或 base64,不是 %r" % encoding)


# ---------------------------------------------------------------- 密钥文本解析

_HEX_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]+)$")


def parse_private_key(text: str) -> int:
    """hex(64 位)或 PEM(PKCS#8 / SEC1)→ d。"""
    text = text.strip()
    if text.startswith("-----BEGIN"):
        d = _find_ec_private_scalar(_pem_body(text))
        if d is None:
            raise Sm2Error("PEM 里找不到 EC 私钥(应为 PKCS#8 或 EC PRIVATE KEY)")
    else:
        m = _HEX_RE.match(text)
        if not m or len(m.group(1)) != 64:
            raise Sm2Error("私钥应为 64 位十六进制或 PEM 文本")
        d = int(m.group(1), 16)
    if not 0 < d < N:
        raise Sm2Error("私钥不在 [1, n-1] 内")
    return d


def parse_public_key(text: str) -> Tuple[int, int]:
    """hex(X‖Y,可带 04)或 PEM(SubjectPublicKeyInfo)→ (x, y)。"""
    text = text.strip()
    if text.startswith("-----BEGIN"):
        raw = _find_bit_string_point(_pem_body(text))
        if raw is None:
            raise Sm2Error("PEM 里找不到 EC 公钥点")
    else:
        m = _HEX_RE.match(text)
        if not m:
            raise Sm2Error("公钥应为十六进制或 PEM 文本")
        raw = bytes.fromhex(m.group(1))
        if len(raw) == 65 and raw[0] == 0x04:
            raw = raw[1:]
    if len(raw) != 64:
        raise Sm2Error("公钥应为 64 字节(X‖Y),实际 %d" % len(raw))
    point = (int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    if not _on_curve(point):
        raise Sm2Error("公钥不在 SM2 曲线上")
    return point


def _pem_body(text: str) -> bytes:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("-----")]
    try:
        return base64.b64decode("".join(lines), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise Sm2Error("PEM 内容不是合法 base64:%s" % exc) from exc


# ---------------------------------------------------------------- 最小 DER

def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _der_int(v: int) -> bytes:
    body = v.to_bytes((v.bit_length() + 7) // 8 or 1, "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return b"\x02" + _der_len(len(body)) + body


def _der_seq(content: bytes) -> bytes:
    return b"\x30" + _der_len(len(content)) + content


def _der_iter(data: bytes) -> Iterator[Tuple[int, bytes]]:
    i = 0
    while i < len(data):
        tag = data[i]
        i += 1
        if i >= len(data):
            raise Sm2Error("DER 截断")
        length = data[i]
        i += 1
        if length & 0x80:
            count = length & 0x7F
            length = int.from_bytes(data[i:i + count], "big")
            i += count
        yield tag, data[i:i + length]
        i += length


def _find_ec_private_scalar(der: bytes) -> Optional[int]:
    """ECPrivateKey = SEQUENCE { INTEGER 1, OCTET STRING d, ... };PKCS#8 把它包在一个 OCTET STRING 里。"""
    for tag, content in _der_iter(der):
        if tag == 0x30:
            items: List[Tuple[int, bytes]] = list(_der_iter(content))
            if len(items) >= 2 and items[0] == (0x02, b"\x01") and items[1][0] == 0x04 and len(items[1][1]) == 32:
                return int.from_bytes(items[1][1], "big")
            found = _find_ec_private_scalar(content)
            if found is not None:
                return found
        elif tag == 0x04:
            found = _find_ec_private_scalar(content) if content[:1] == b"\x30" else None
            if found is not None:
                return found
    return None


def _find_bit_string_point(der: bytes) -> Optional[bytes]:
    for tag, content in _der_iter(der):
        if tag == 0x03 and len(content) == 66 and content[:2] == b"\x00\x04":
            return content[2:]
        if tag == 0x30:
            found = _find_bit_string_point(content)
            if found is not None:
                return found
    return None
