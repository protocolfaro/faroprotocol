"""
dale_play_github.py — Push del histórico JSON de cada show a GitHub.
Guarda en dale-play/shows/{show_id}.json en protocolfaro/faroprotocol (branch main).
"""
from __future__ import annotations
import base64, json, logging, os
from datetime import datetime, timezone

from dale_play_config import GH_OWNER, GH_REPO, GH_BRANCH, GH_SHOWS_PATH, GH_REPORTES_PATH

log = logging.getLogger(__name__)


def _hdrs() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN not set")
    return {
        "Authorization":       f"Bearer {token}",
        "Accept":              "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def push_show_snapshot(show_id: str, show_data: dict) -> dict:
    """
    Upserts dale-play/shows/{show_id}.json en GitHub.
    Excluye report_png (path local) del payload.
    Retorna {"action": "created"|"updated", "path": str, "url": str}
    """
    import requests

    path    = f"{GH_SHOWS_PATH}/{show_id}.json"
    api_url = (
        f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents/{path}"
    )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # GET para obtener SHA si el archivo ya existe
    sha = None
    try:
        r = requests.get(api_url, headers=_hdrs(),
                         params={"ref": GH_BRANCH}, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        log.warning("dale_play_github: GET check failed: %s", e)

    # Payload: omitir campos binarios
    payload = {k: v for k, v in show_data.items() if k != "report_png"}
    payload["updated_at"] = ts

    content = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, indent=2).encode()
    ).decode()

    body: dict = {
        "message": (
            f"dale-play: {show_data.get('artist','?')} · "
            f"{show_data.get('show_date','?')} [{ts}]"
        ),
        "content": content,
        "branch":  GH_BRANCH,
    }
    if sha:
        body["sha"] = sha

    r = requests.put(api_url, headers=_hdrs(), json=body, timeout=30)
    r.raise_for_status()

    commit_url = r.json().get("commit", {}).get("html_url", "")
    action     = "updated" if sha else "created"
    log.info("dale_play_github: %s %s → %s", action, path, commit_url[-60:])

    return {"action": action, "path": path, "url": commit_url}


def push_png_to_github(show_id: str, png_local_path: str) -> str:
    """
    Sube el PNG del reporte a GitHub en dale-play/reportes/.
    Retorna la URL raw de GitHub para descarga directa.
    """
    import requests

    if not os.path.exists(png_local_path):
        raise FileNotFoundError(f"PNG no encontrado: {png_local_path}")

    gh_path = f"{GH_REPORTES_PATH}/reporte_{show_id}.png"
    api_url = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents/{gh_path}"
    ts      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sha = None
    try:
        r = requests.get(api_url, headers=_hdrs(),
                         params={"ref": GH_BRANCH}, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        log.warning("dale_play_github push_png: GET check failed: %s", e)

    with open(png_local_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()

    body: dict = {
        "message": f"dale-play: reporte PNG {show_id} [{ts}]",
        "content": content,
        "branch":  GH_BRANCH,
    }
    if sha:
        body["sha"] = sha

    r = requests.put(api_url, headers=_hdrs(), json=body, timeout=60)
    r.raise_for_status()

    raw_url = (
        f"https://raw.githubusercontent.com/{GH_OWNER}/{GH_REPO}"
        f"/{GH_BRANCH}/{gh_path}"
    )
    action = "updated" if sha else "created"
    log.info("dale_play_github: PNG %s → %s", action, raw_url)
    return raw_url
