from __future__ import annotations

import os
import re
import pathlib
from dataclasses import dataclass, replace

import yaml

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_VALID_SSLMODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)

_VALID_TYPES = frozenset({"opengauss", "gaussdb"})

# grmp：不直连数据库，改走 GRMP 兼容中间件的两个 HTTP 接口。
# 此时 host/port 指向中间件端点，data_ip 是中间件用来路由到目标实例的键。
_VALID_DRIVERS = frozenset({"gsql", "pg8000", "grmp"})


@dataclass(frozen=True)
class Connection:
    """One named database target (immutable)."""

    name: str
    type: str
    host: str
    port: int
    database: str
    user: str
    sslmode: str = ""
    driver: str = "gsql"
    # 仅 driver=grmp 时使用：中间件按它路由到目标高斯实例
    data_ip: str = ""

    def with_sslmode(self, sslmode: str) -> "Connection":
        """Return a new Connection with sslmode replaced (no mutation)."""
        return replace(self, sslmode=sslmode)


class ConfigError(Exception):
    """Raised on malformed config or connection definitions."""


def validate(conn: Connection) -> None:
    """Fail fast on malformed connection definitions (boundary input)."""
    if not conn.name or not _NAME_RE.match(conn.name):
        raise ConfigError(
            f"connection name {conn.name!r}: must start with a lowercase "
            f"letter or digit and contain only [a-z0-9_-]"
        )
    if conn.type not in _VALID_TYPES:
        raise ConfigError(f"type {conn.type!r}: must be opengauss or gaussdb")
    if not conn.host:
        raise ConfigError("host is required")
    if not isinstance(conn.port, int) or conn.port < 1 or conn.port > 65535:
        raise ConfigError(f"port {conn.port}: out of range")
    if not conn.database:
        raise ConfigError("database is required")
    if not conn.user:
        raise ConfigError("user is required")
    if conn.sslmode and conn.sslmode not in _VALID_SSLMODES:
        raise ConfigError(
            f"sslmode {conn.sslmode!r}: must be one of "
            f"disable/allow/prefer/require/verify-ca/verify-full"
        )
    if conn.driver not in _VALID_DRIVERS:
        raise ConfigError(
            f"driver {conn.driver!r}: must be one of "
            f"{'/'.join(sorted(_VALID_DRIVERS))}"
        )
    if conn.driver == "grmp" and not conn.data_ip:
        raise ConfigError(
            f"connection {conn.name!r}: driver grmp requires data_ip "
            f"(中间件按它路由到目标实例，缺了会在第一次调用时才失败)"
        )


def state_dir() -> pathlib.Path:

    base = os.environ.get("GSDB_HOME") or os.environ.get("GDAA_HOME")
    if base:
        return pathlib.Path(base)
    return pathlib.Path("/workspace/.opencode/skills/common")

#    base = os.environ.get("GSDB_HOME")
#    if base:
#        return pathlib.Path(base)
#    else:
#        os.environ["GSDB_HOME"]="/workspace/.config/opencode/skills/common"

    #return pathlib.Path.home() / ".gdaa"
#    return "/workspace/.config/opencode/skills/common"



def ensure_dir() -> pathlib.Path:
    """Return the state directory, creating it with 0700 if absent."""
    base = state_dir()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    return base


def _config_path() -> pathlib.Path:
    return state_dir() / "config.yaml"


def load() -> list[Connection]:
    """Read config.yaml; a missing file yields an empty list.

    config.yaml is external (user-editable) input, so every connection is
    validated after parsing.
    """
    path = _config_path()
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"parse {path}: {exc}") from exc

    conns: list[Connection] = []
    for item in raw.get("connections", []) or []:
        conn = Connection(
            name=item.get("name", ""),
            type=item.get("type", ""),
            host=item.get("host", ""),
            port=item.get("port", 0),
            database=item.get("database", ""),
            user=item.get("user", ""),
            sslmode=item.get("sslmode", "") or "",
            driver=item.get("driver", "gsql") or "gsql",
            data_ip=item.get("data_ip", "") or "",
        )
        validate(conn)
        conns.append(conn)
    return conns


def find(name: str) -> Connection:
    """Return the named connection or raise ConfigError if absent."""
    for conn in load():
        if conn.name == name:
            return conn
    raise ConfigError(
        f"no connection named {name!r}: run `connect add {name} ...` first "
        f"(or check `connect list`)"
    )
