"""faro_crypto.py — PQC hybrid seal: SHAKE256-512 + SHA-256 + HMAC binding
Faro Protocol · stdlib only, no external deps
"""
from __future__ import annotations
import hashlib
import hmac as _hmac


def shake256_seal(data: bytes, bits: int = 512) -> str:
    return hashlib.shake_256(data).hexdigest(bits // 8)


def hybrid_seal(data: bytes) -> dict:
    """Returns a composite seal dict with sha256, shake256_512, and binding HMAC."""
    sha256 = hashlib.sha256(data).hexdigest()
    shake  = hashlib.shake_256(data).hexdigest(64)          # 512-bit / 64 bytes
    # HMAC binds both digests so neither can be swapped independently
    binding = _hmac.new(shake.encode(), (sha256 + shake).encode(), hashlib.sha256).hexdigest()
    return {
        "sha256":        sha256,
        "shake256_512":  shake,
        "binding_hmac":  binding,
        "algo":          "SHAKE256-512+SHA256+HMAC-binding",
    }


def verify_seal(data: bytes, seal: dict) -> bool:
    fresh = hybrid_seal(data)
    return (
        fresh["sha256"]       == seal.get("sha256") and
        fresh["shake256_512"] == seal.get("shake256_512") and
        fresh["binding_hmac"] == seal.get("binding_hmac")
    )


def seal_to_header(seal: dict, prefix: str = "") -> str:
    """One-line footer text: algo / first 24 chars of SHAKE256."""
    tag = seal["shake256_512"][:24]
    return f"{prefix}PQC:{seal['algo']} · {tag}…"


if __name__ == "__main__":
    sample = b"Faro Protocol test payload 2026"
    s = hybrid_seal(sample)
    print("sha256:       ", s["sha256"])
    print("shake256_512: ", s["shake256_512"])
    print("binding_hmac: ", s["binding_hmac"])
    print("verify ok:    ", verify_seal(sample, s))
    print("tamper check: ", verify_seal(b"tampered", s))
