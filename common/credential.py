from __future__ import annotations

import os
import pathlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#from .config import _NAME_RE, ensure_dir, state_dir
try:
    from .config import _NAME_RE, ensure_dir, state_dir
except ImportError:
    from config import _NAME_RE, ensure_dir, state_dir

_KEY_SIZE = 32  # AES-256
_NONCE_SIZE = 12  # GCM standard nonce


class CredentialError(Exception):
    """Raised on missing/corrupted credentials or invalid names."""


def _key_path() -> pathlib.Path:
    return state_dir() / "key"


def _load_key() -> bytes:
    """Read the machine-local key, generating one atomically on first use."""
    path = _key_path()
    if path.exists():
        key = path.read_bytes()
        if len(key) != _KEY_SIZE:
            raise CredentialError(
                f"key {path}: want {_KEY_SIZE} bytes, got {len(key)}"
            )
        return key

    ensure_dir()
    fresh = os.urandom(_KEY_SIZE)
    # O_EXCL makes creation atomic: exactly one concurrent caller wins.
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the race; use the winner's key.
        return _load_key()
    try:
        os.write(fd, fresh)
    finally:
        os.close(fd)
    return fresh


def load_secret(name: str) -> str:
    """Return the decrypted credential for a connection name."""
    if not name or not _NAME_RE.match(name):
        raise CredentialError(f"invalid credential name {name!r}")

    env = os.environ.get("GSDB_PASSWORD") or os.environ.get("GDAA_PASSWORD")
    if env:
        return env

    key = _load_key()
    path = state_dir() / "credentials" / f"{name}.enc"
    try:
        sealed = path.read_bytes()
    except FileNotFoundError as exc:
        raise CredentialError(
            f"no stored credential for {name!r}: run `connect add {name} ...` first"
        ) from exc

    if len(sealed) < _NONCE_SIZE + 16:  # nonce + GCM tag
        raise CredentialError(f"credential {path}: corrupted (too short)")

    nonce, ciphertext = sealed[:_NONCE_SIZE], sealed[_NONCE_SIZE:]
    try:
        # AAD == name, matching Go's gcm.Seal(..., []byte(name)).
        plain = AESGCM(key).decrypt(nonce, ciphertext, name.encode())
    except Exception as exc:  # cryptography raises InvalidTag etc.
        raise CredentialError(f"decrypt credential {path}: {exc}") from exc
    return plain.decode()


def save_secret(name: str, secret: str) -> None:
    """Encrypt and store a credential (used by the connect skill)."""
    if not name or not _NAME_RE.match(name):
        raise CredentialError(f"invalid credential name {name!r}")

    key = _load_key()
    nonce = os.urandom(_NONCE_SIZE)
    sealed = nonce + AESGCM(key).encrypt(nonce, secret.encode(), name.encode())

    cred_dir = ensure_dir() / "credentials"
    cred_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = cred_dir / f"{name}.enc"
    path.write_bytes(sealed)
    os.chmod(path, 0o600)
