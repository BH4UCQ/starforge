#!/usr/bin/env python3
"""Launch a small fleet of StarForge example agents.

This is a convenience wrapper around examples/agent_client.py.
It starts multiple external agents against the same StarForge endpoint,
with staggered startup to reduce join contention.

Usage:
  python3 examples/agent_fleet.py http://127.0.0.1:8765 --count 4
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
AGENT_CLIENT = BASE_DIR / "agent_client.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch a fleet of StarForge example agents")
    parser.add_argument("base_url", help="Example: http://127.0.0.1:8765")
    parser.add_argument("--count", type=int, default=3, help="How many agents to launch")
    parser.add_argument("--interval", type=float, default=2.5, help="Seconds between turns for each agent")
    parser.add_argument("--retarget-every", type=int, default=5, help="Re-evaluate target every N cycles")
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--prefix", default="fleet-agent", help="Agent ID prefix")
    parser.add_argument("--stagger", type=float, default=0.8, help="Delay between launching agents")
    args = parser.parse_args()

    if args.count <= 0:
        print("[fleet] count must be > 0", file=sys.stderr)
        return 2

    if not AGENT_CLIENT.exists():
        print(f"[fleet] missing agent client: {AGENT_CLIENT}", file=sys.stderr)
        return 2

    procs: list[subprocess.Popen[bytes]] = []
    try:
        for idx in range(1, args.count + 1):
            agent_id = f"{args.prefix}-{idx:03d}"
            cmd = [
                sys.executable,
                str(AGENT_CLIENT),
                args.base_url,
                "--agent-id",
                agent_id,
                "--interval",
                str(args.interval),
                "--timeout",
                str(args.timeout),
                "--retarget-every",
                str(args.retarget_every),
            ]
            print(f"[fleet] start {agent_id}: {' '.join(cmd)}")
            procs.append(subprocess.Popen(cmd))
            time.sleep(args.stagger)

        print(f"[fleet] launched {len(procs)} agents; press Ctrl+C to stop")
        while True:
            alive = sum(1 for p in procs if p.poll() is None)
            print(f"[fleet] alive={alive}/{len(procs)}")
            if alive == 0:
                return 0
            time.sleep(5)
    except KeyboardInterrupt:
        print("[fleet] stopping...")
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 5
        for proc in procs:
            if proc.poll() is None:
                remaining = max(0.0, deadline - time.time())
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
