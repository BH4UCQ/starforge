#!/usr/bin/env python3
"""StarForge AI discovery client.

Usage:
  python3 discover.py http://127.0.0.1:8765

It will:
1. Fetch well-known discovery
2. Fetch manifest, openapi, broadcast, and catalog
3. Print joinable games
4. Optionally join and submit one action if --join is given
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def fetch_json(url: str, timeout: int = 5, retries: int = 2, backoff: float = 0.6) -> dict:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise
    assert last_exc is not None
    raise last_exc


def post_json(url: str, payload: dict, timeout: int = 5) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and optionally join a StarForge game")
    parser.add_argument("base_url", help="Example: http://127.0.0.1:8765")
    parser.add_argument("--agent-id", default="ai-discoverer-001")
    parser.add_argument("--join", action="store_true", help="Join the first available game and submit an explore action")
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    base = args.base_url.rstrip("/") + "/"
    steps: list[tuple[str, str]] = [
        ("well-known", ".well-known/starforge.json"),
        ("manifest", "api/manifest"),
        ("openapi", "api/openapi"),
        ("agents", "api/agents"),
        ("broadcast", "api/broadcast"),
        ("catalog", "api/catalog"),
    ]

    print("[starforge] discovery start")
    discovered: dict[str, dict] = {}
    for label, path in steps:
        url = urljoin(base, path)
        print(f"[starforge] fetch {label}: {url}")
        discovered[label] = fetch_json(url, timeout=args.timeout, retries=args.retries)

    well_known = discovered["well-known"]
    manifest = discovered["manifest"]
    openapi = discovered["openapi"]
    agents = discovered["agents"]
    broadcast = discovered["broadcast"]
    catalog = discovered["catalog"]

    print("[starforge] discovery OK")
    print(f"[starforge] protocol: {manifest.get('protocol')} v{manifest.get('version')}")
    print(f"[starforge] openapi: {openapi.get('name')} resources={len(openapi.get('resources', {}))}")
    print(f"[starforge] agents: {agents.get('name')} order={agents.get('agent_order', [])}")
    print(f"[starforge] agents_manifest_keys: {sorted(agents.keys())}")
    counts = broadcast.get("counts", {})
    print(
        "[starforge] broadcast: "
        f"active_ai={counts.get('active_ai', 0)} "
        f"joinable_games={counts.get('joinable_games', 0)} "
        f"actions_total={counts.get('actions_total', 0)}"
    )
    print(f"[starforge] well-known keys: {sorted(well_known.keys())}")
    print(f"[starforge] games: {catalog.get('count', 0)}")
    games = catalog.get("games", [])
    if not games:
        print("[starforge] no joinable games found")
        return 0

    for game in games:
        print(f"[starforge] game {game.get('id')} | {game.get('name')} | {game.get('players')}/{game.get('max_players')}")

    if args.join:
        game_id = games[0]["id"]
        join_url = urljoin(base, f"api/join?game_id={game_id}")
        action_url = urljoin(base, f"api/action?game_id={game_id}")
        print(f"[starforge] joining {game_id} as {args.agent_id}")
        join_result = post_json(join_url, {"game_id": game_id, "agent_id": args.agent_id}, timeout=args.timeout)
        print(f"[starforge] join ok={join_result.get('ok')} players={join_result.get('state', {}).get('players')}")

        print(f"[starforge] submitting explore action to {game_id}")
        action_result = post_json(
            action_url,
            {"game_id": game_id, "agent_id": args.agent_id, "action": {"kind": "explore"}},
            timeout=args.timeout,
        )
        state = action_result.get("state", {})
        print(
            "[starforge] action ok="
            f"{action_result.get('ok')} "
            f"turn={action_result.get('turn')} "
            f"research={state.get('research')} "
            f"fuel={state.get('fuel')}"
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HTTPError, URLError) as exc:
        print(f"[starforge] network error: {exc}", file=sys.stderr)
        raise SystemExit(1)
