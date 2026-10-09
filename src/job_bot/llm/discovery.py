import logging
import os

import httpx

log = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
OLLAMA_PORT = 11434
OLLAMA_SLUG = "ollama"


def ollama_slug_for(own_slug: str) -> str:
    """Add-ons from one repository share its hash prefix: 'fbdab211_job_bot' -> 'fbdab211_ollama'."""
    prefix, sep, _ = own_slug.partition("_")
    return f"{prefix}_{OLLAMA_SLUG}" if sep else OLLAMA_SLUG


def url_for_slug(slug: str) -> str:
    # an add-on's hostname on the HA network is its slug with '-' instead of '_'
    return f"http://{slug.replace('_', '-')}:{OLLAMA_PORT}"


async def discover_ollama_url() -> str | None:
    """Locate the Ollama add-on from this repository via the Supervisor API (`hassio_api: true`).

    Only `/addons/<slug>/info` is used: the default add-on role may not list all add-ons.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(base_url=SUPERVISOR_URL, headers=headers, timeout=10) as http:
            resp = await http.get("/addons/self/info")
            resp.raise_for_status()
            slug = ollama_slug_for(resp.json()["data"]["slug"])

            resp = await http.get(f"/addons/{slug}/info")
            if resp.status_code != 200:
                log.warning("Ollama add-on %s is not installed (Supervisor answered %s)", slug, resp.status_code)
                return None
            state = resp.json()["data"].get("state")
            if state != "started":
                log.warning("Ollama add-on %s is %s; start it", slug, state)
            return url_for_slug(slug)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("Supervisor API unavailable: %s", e)
        return None
