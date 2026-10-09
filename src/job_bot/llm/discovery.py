import logging
import os

import httpx

log = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
OLLAMA_PORT = 11434


def pick_ollama_url(addons: list[dict]) -> str | None:
    """Find an installed Ollama add-on; its hostname on the HA network is the slug with '-' for '_'."""
    candidates = [a for a in addons if a.get("slug", "").endswith("ollama") and a.get("installed", True)]
    candidates.sort(key=lambda a: a.get("state") != "started")
    if not candidates:
        return None
    return f"http://{candidates[0]['slug'].replace('_', '-')}:{OLLAMA_PORT}"


async def discover_ollama_url() -> str | None:
    """Ask the HA Supervisor which Ollama add-on is installed. Needs `hassio_api: true`."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(f"{SUPERVISOR_URL}/addons", headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Supervisor API unavailable: %s", e)
        return None
    return pick_ollama_url(resp.json().get("data", {}).get("addons", []))
