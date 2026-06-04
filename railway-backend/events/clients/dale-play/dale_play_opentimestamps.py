"""dale_play_opentimestamps.py — Bitcoin blockchain anchoring via OpenTimestamps.

Submits the SHA-256 certificate hash to OTS calendar servers at the moment
of certification. Stores partial proof (.ots bytes, base64) in Supabase.

Timeline: calendar commitment is instant; Bitcoin block confirmation ~1 hour.
Verification: any OTS client can upgrade and verify the stored proof.
  ots upgrade proof.ots && ots verify proof.ots cert_hash.txt
  or: paste base64 at https://opentimestamps.org/info
"""
from __future__ import annotations
import base64, logging
from typing import Optional

log = logging.getLogger(__name__)

_OTS_CALENDARS = [
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.opentimestamps.org",
]
_TIMEOUT = 15  # seconds per server


def stamp_certificate(cert_hash_hex: str) -> Optional[bytes]:
    """POST raw SHA-256 bytes to OTS calendar. Returns partial proof bytes or None."""
    try:
        import requests
    except ImportError:
        log.warning("OTS: requests not available")
        return None

    hash_bytes = bytes.fromhex(cert_hash_hex.lower())
    for url in _OTS_CALENDARS:
        try:
            r = requests.post(
                f"{url}/digest",
                data=hash_bytes,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Accept":       "application/octet-stream",
                },
                timeout=_TIMEOUT,
            )
            if r.status_code == 200 and r.content:
                log.info("OTS: anchored via %s (%d proof bytes)", url, len(r.content))
                return r.content
            log.warning("OTS: %s → HTTP %s", url, r.status_code)
        except Exception as e:
            log.warning("OTS: %s failed: %s", url, e)

    log.error("OTS: all calendar servers unreachable — cert not anchored to blockchain")
    return None


def stamp_to_base64(cert_hash_hex: str) -> Optional[str]:
    """stamp_certificate + base64 for JSON/Supabase storage. None if stamping fails."""
    proof = stamp_certificate(cert_hash_hex)
    return base64.b64encode(proof).decode() if proof is not None else None
