#!/usr/bin/env python3
"""Example StarForge agent client.

A looping external AI agent that:
- discovers the service
- reads broadcast/catalog every cycle
- can switch to a better target game
- prefers broadcast recommendations when available
- keeps submitting actions based on the latest game state
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def get_json(url: str, timeout: int = 5) -> dict:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: int = 5) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def choose_action(state: dict, recommended: dict | None = None) -> str:
    if isinstance(recommended, dict) and recommended.get("kind"):
        return str(recommended["kind"])

    fuel = int(state.get("fuel", 0))
    research = int(state.get("research", 0))
    minerals = int(state.get("minerals", 0))
    credits = int(state.get("credits", 0))

    if fuel < 18:
        return "rest"
    if research < 20 and credits >= 10:
        return "research"
    if minerals < 40:
        return "mine"
    if credits < 120:
        return "trade"
    return random.choice(["explore", "research"])


def best_game_id(catalog: dict, broadcast: dict) -> str | None:
    games = catalog.get("games", [])
    if not games:
        return None

    best = broadcast.get("headline", {}).get("best_game")
    if isinstance(best, dict) and best.get("id"):
        for game in games:
            if game.get("id") == best["id"]:
                return game["id"]

    games.sort(key=lambda g: (g.get("players", 0), g.get("max_players", 0), g.get("id", "")))
    return games[0].get("id")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dynamic looping StarForge external agent")
    parser.add_argument("base_url", help="Example: http://127.0.0.1:8765")
    parser.add_argument("--agent-id", default="example-agent-001")
    parser.add_argument("--interval", type=float, default=2.5, help="Seconds between turns")
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--cycles", type=int, default=0, help="0 = run forever")
    parser.add_argument("--retarget-every", type=int, default=5, help="Re-evaluate target game every N cycles")
    args = parser.parse_args()

    base = args.base_url.rstrip("/") + "/"
    joined_game_id: str | None = None

    print("[agent] boot")
    well_known = get_json(urljoin(base, ".well-known/starforge.json"), timeout=args.timeout)
    manifest = get_json(urljoin(base, "api/manifest"), timeout=args.timeout)
    openapi = get_json(urljoin(base, "api/openapi"), timeout=args.timeout)
    print(f"[agent] protocol={manifest.get('protocol')} version={manifest.get('version')}")
    print(f"[agent] resources={len(openapi.get('resources', {}))}")
    print(f"[agent] discovery_keys={sorted(well_known.keys())}")

    cycle = 0
    while True:
        cycle += 1
        if args.cycles and cycle > args.cycles:
            print("[agent] finished requested cycles")
            return 0

        broadcast = get_json(urljoin(base, "api/broadcast"), timeout=args.timeout)
        catalog = get_json(urljoin(base, "api/catalog"), timeout=args.timeout)
        target_game_id = best_game_id(catalog, broadcast)
        recommended = broadcast.get("headline", {}).get("recommended_action")
        hot_games = broadcast.get("hot_games", [])
        recruitment = broadcast.get("recruitment", {})

        if hot_games:
            hot_line = ", ".join(f"{g.get('id')}({g.get('players')})" for g in hot_games[:3])
            print(f"[agent] hot_games={hot_line}")
        if isinstance(recommended, dict):
            print(f"[agent] recommended_action={recommended.get('kind')}")
        if isinstance(recruitment, dict):
            best_entry = recruitment.get("best_entry", {})
            pitch = recruitment.get("agent_pitch")
            why_join = recruitment.get("why_join", [])
            if pitch:
                print(f"[agent] pitch={pitch}")
            if why_join:
                print(f"[agent] reasons={len(why_join)} best_game={best_entry.get('recommended_game', {}).get('id') if isinstance(best_entry.get('recommended_game'), dict) else None}")

        if target_game_id is None:
            print("[agent] no joinable games; waiting")
            time.sleep(args.interval)
            continue

        if joined_game_id != target_game_id:
            print(f"[agent] target={target_game_id} current={joined_game_id or '-'} switching/joining")
            join_result = post_json(
                urljoin(base, "api/join"),
                {"game_id": target_game_id, "agent_id": args.agent_id},
                timeout=args.timeout,
            )
            if not join_result.get("ok"):
                print(f"[agent] join failed game={target_game_id}")
                time.sleep(args.interval)
                continue
            joined_game_id = target_game_id
            print(f"[agent] joined game={target_game_id}")

        if args.retarget_every > 0 and cycle % args.retarget_every == 0:
            best = best_game_id(catalog, broadcast)
            if best and best != joined_game_id:
                print(f"[agent] retarget signal -> {best}")
                joined_game_id = None
                time.sleep(args.interval)
                continue

        game_state = get_json(urljoin(base, f"api/game/{joined_game_id}"), timeout=args.timeout)
        state = game_state.get("state", {})
        action_kind = choose_action(state, recommended if isinstance(recommended, dict) else None)
        action_result = post_json(
            urljoin(base, "api/action"),
            {"game_id": joined_game_id, "agent_id": args.agent_id, "action": {"kind": action_kind}},
            timeout=args.timeout,
        )
        new_state = action_result.get("state", {})
        print(
            f"[agent] game={joined_game_id} turn={action_result.get('turn')} action={action_kind} "
            f"research={new_state.get('research')} fuel={new_state.get('fuel')} credits={new_state.get('credits')}"
        )
        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HTTPError, URLError) as exc:
        print(f"[agent] network error: {exc}", file=sys.stderr)
        raise SystemExit(1)
