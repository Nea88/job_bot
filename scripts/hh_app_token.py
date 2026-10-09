"""Get an hh.ru application token for vacancy search (anonymous search hits a captcha).

1. Register an application on https://dev.hh.ru (any redirect URI) and wait for approval.
2. uv run python scripts/hh_app_token.py <client_id> <client_secret>
3. Put the printed token into hh_access_token (HA add-on option or HH_ACCESS_TOKEN in .env).

The token does not expire; requesting a new one revokes the previous token,
and hh allows a new request at most once per 5 minutes.
"""

import sys

import httpx


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    client_id, client_secret = sys.argv[1:]
    resp = httpx.post(
        "https://hh.ru/oauth/token",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"hh.ru answered {resp.status_code}: {resp.text}")
    print(resp.json()["access_token"])


if __name__ == "__main__":
    main()
