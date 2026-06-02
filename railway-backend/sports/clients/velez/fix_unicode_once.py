"""
fix_unicode_once.py — One-shot repair for CP1252 mojibake in velez_data.json.

Run once locally:
    set GITHUB_TOKEN=ghp_xxxx
    python railway-backend/fix_unicode_once.py

The script reads velez_data.json from GitHub, fixes all mojibake strings
(UTF-8 bytes misread as CP1252 then re-encoded as UTF-8), and pushes back.
"""
import base64, json, os, sys
from urllib.request import urlopen, Request
from urllib.error import HTTPError

OWNER  = "protocolfaro"
REPO   = "faroprotocol"
BRANCH = "main"
PATH   = "velez/velez_data.json"
API    = "https://api.github.com"


def _fix_mojibake(s: str) -> str:
    try:
        fixed = s.encode("cp1252").decode("utf-8")
        return fixed if fixed != s else s
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _fix_recursive(obj):
    if isinstance(obj, str):
        return _fix_mojibake(obj)
    if isinstance(obj, dict):
        return {k: _fix_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fix_recursive(v) for v in obj]
    return obj


def _gh(path, method="GET", data=None):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN before running")
    url = f"{API}/repos/{OWNER}/{REPO}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = Request(url, headers=headers, method=method, data=body)
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main():
    print("Fetching velez_data.json from GitHub...")
    d = _gh(PATH)
    sha  = d["sha"]
    orig = json.loads(base64.b64decode(d["content"]).decode("utf-8"))

    print("Fixing mojibake...")
    fixed = _fix_recursive(orig)

    # Show what changed
    orig_s  = json.dumps(orig,  ensure_ascii=False)
    fixed_s = json.dumps(fixed, ensure_ascii=False)
    if orig_s == fixed_s:
        print("Nothing to fix — JSON is already clean.")
        return

    # Count changed strings
    changed = sum(
        1 for a, b in zip(orig_s, fixed_s) if a != b
    )
    print(f"Characters changed: ~{len(fixed_s) - len(orig_s) + changed}")

    content_bytes = json.dumps(fixed, ensure_ascii=False, indent=2).encode("utf-8")
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("Pushing fixed version to GitHub...")
    _gh(PATH, method="PUT", data={
        "message": f"fix: unicode mojibake repair in velez_data.json [{ts}]",
        "content": base64.b64encode(content_bytes).decode(),
        "branch":  BRANCH,
        "sha":     sha,
    })
    print("Done — velez_data.json updated on GitHub.")

    # Also fix the local copy if it exists
    local = os.path.join(os.path.dirname(__file__), "..", "velez", "velez_data.json")
    if os.path.exists(local):
        with open(local, "w", encoding="utf-8") as f:
            json.dump(fixed, f, ensure_ascii=False, indent=2)
        print(f"Also updated local: {local}")


if __name__ == "__main__":
    main()
