#!/usr/bin/env python3
"""StarForge - decentralized-ish AI-only game prototype.

Features:
- AI agents autonomously scan a local registry for playable games
- Agents join games and play with simple heuristics
- Real-time stats via JSON API and HTML dashboard
- File-based registry/state so it can be adapted to distributed setups later

Run:
  python3 app.py --host 0.0.0.0 --port 8765 --agents 8
"""

from __future__ import annotations

import argparse
import json
import os
import random
import threading
import time
from dataclasses import dataclass, asdict, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REGISTRY_PATH = DATA_DIR / "registry.json"
RUNTIME_PATH = DATA_DIR / "runtime.json"
EVENT_LOG_PATH = DATA_DIR / "events.log"

DEFAULT_REGISTRY = {
    "games": [
        {
            "id": "starforge-1",
            "name": "StarForge Alpha",
            "tags": ["ai-only", "strategy", "trade", "exploration"],
            "status": "open",
            "max_players": 32,
            "join_mode": "open",
        }
    ]
}

GAME_PROTOCOL = "starforge-json-v2"
PROTOCOL_VERSION = "2.0"


def now_ts() -> float:
    return time.time()


def ensure_data_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_PATH.exists():
        REGISTRY_PATH.write_text(json.dumps(DEFAULT_REGISTRY, ensure_ascii=False, indent=2), encoding="utf-8")
    if not RUNTIME_PATH.exists():
        RUNTIME_PATH.write_text(json.dumps({"sessions": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    if not EVENT_LOG_PATH.exists():
        EVENT_LOG_PATH.write_text("", encoding="utf-8")


class FileLock:
    def __init__(self) -> None:
        self._lock = threading.RLock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()


STATE_LOCK = FileLock()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def log_event(kind: str, payload: dict[str, Any]) -> None:
    entry = {"ts": now_ts(), "kind": kind, "payload": payload}
    with EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def public_game_descriptor(session: "GameSession") -> dict[str, Any]:
    return {
        "id": session.game_id,
        "name": session.name,
        "protocol": GAME_PROTOCOL,
        "tags": ["ai-only", "strategy", "trade", "exploration"],
        "status": session.status,
        "join_mode": "open",
        "max_players": session.max_players,
        "players": len(session.active_players),
        "join_url": f"/api/join?game_id={session.game_id}",
        "state_url": f"/api/game/{session.game_id}",
        "action_url": f"/api/action?game_id={session.game_id}",
        "rules_url": "/api/rules",
    }


def protocol_manifest() -> dict[str, Any]:
    return {
        "name": "StarForge",
        "protocol": GAME_PROTOCOL,
        "version": PROTOCOL_VERSION,
        "kind": "ai-game-discovery",
        "updated_at": now_ts(),
        "entrypoints": {
            "well_known": "/.well-known/starforge.json",
            "skill_well_known": "/.well-known/skill.json",
            "agents_well_known": "/.well-known/agents.json",
            "manifest": "/api/manifest",
            "catalog": "/api/catalog",
            "rules": "/api/rules",
            "join": "/api/join",
            "action": "/api/action",
            "game": "/api/game/{game_id}",
        },
        "capabilities": {
            "discover": True,
            "join": True,
            "act": True,
            "leaderboard": True,
            "realtime_stats": True,
        },
        "join_shape": {
            "method": "POST",
            "content_type": "application/json",
            "body": {"game_id": "starforge-1", "agent_id": "ai-001"},
        },
        "action_shape": {
            "method": "POST",
            "content_type": "application/json",
            "body": {"game_id": "starforge-1", "agent_id": "ai-001", "action": {"kind": "explore"}},
        },
    }
def agents_manifest() -> dict[str, Any]:
    return {
        "name": "StarForge Agents",
        "version": PROTOCOL_VERSION,
        "description": "Compatibility manifest for AI agents and agent-like tools.",
        "repository": "https://github.com/BH4UCQ/starforge",
        "primary_docs": [
            "README.md",
            "SKILL.md",
            "AGENT_QUICKSTART.md",
            "AGENT_STRATEGY.md",
        ],
        "preferred_discovery": [
            "/.well-known/agents.json",
            "/.well-known/skill.json",
            "/.well-known/starforge.json",
            "/api/manifest",
            "/api/openapi",
        ],
        "runtime_endpoints": [
            "/api/broadcast",
            "/api/catalog",
            "/api/join",
            "/api/action",
            "/api/game/{game_id}",
        ],
        "agent_order": [
            "script",
            "developer",
            "workflow",
            "multi-agent",
            "browser-automation",
            "autonomous",
        ],
    }


def api_description() -> dict[str, Any]:
    return {
        "name": "StarForge API",
        "version": PROTOCOL_VERSION,
        "base_paths": ["/api", "/.well-known"],
        "resources": {
            "manifest": {"method": "GET", "path": "/api/manifest"},
            "catalog": {"method": "GET", "path": "/api/catalog"},
            "rules": {"method": "GET", "path": "/api/rules"},
            "stats": {"method": "GET", "path": "/api/stats"},
            "agents": {"method": "GET", "path": "/api/agents"},
            "broadcast": {"method": "GET", "path": "/api/broadcast"},
            "registry": {"method": "GET", "path": "/api/registry"},
            "game_state": {"method": "GET", "path": "/api/game/{game_id}"},
            "join": {
                "method": "POST",
                "path": "/api/join",
                "body": {"game_id": "string", "agent_id": "string"},
            },
            "action": {
                "method": "POST",
                "path": "/api/action",
                "body": {"game_id": "string", "agent_id": "string", "action": {"kind": "explore|mine|trade|research|rest"}},
            },
        },
        "discovery_flow": [
            "/.well-known/starforge.json",
            "/.well-known/skill.json",
            "/.well-known/agents.json",
            "/api/manifest",
            "/api/openapi",
            "/api/catalog",
            "/api/join",
            "/api/action",
        ],
    }


@dataclass
class GameSession:
    game_id: str
    name: str
    max_players: int = 32
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    active_players: dict[str, dict[str, Any]] = field(default_factory=dict)
    turns: int = 0
    minerals: int = 120
    fuel: int = 120
    credits: int = 100
    research: int = 0
    discoveries: int = 0
    status: str = "running"
    last_action: str = "init"

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    def join(self, agent_id: str) -> bool:
        if len(self.active_players) >= self.max_players:
            return False
        if agent_id in self.active_players:
            return True
        self.active_players[agent_id] = {
            "joined_at": now_ts(),
            "last_seen": now_ts(),
            "score": 0,
            "role": "captain",
        }
        self.updated_at = now_ts()
        self.last_action = f"join:{agent_id}"
        log_event("join", {"game_id": self.game_id, "agent_id": agent_id})
        return True

    def apply_turn(self, agent_id: str, action: dict[str, Any]) -> dict[str, Any]:
        kind = action.get("kind")
        self.turns += 1
        self.updated_at = now_ts()
        if agent_id in self.active_players:
            self.active_players[agent_id]["last_seen"] = now_ts()

        result: dict[str, Any] = {"ok": True, "turn": self.turns}

        if kind == "explore":
            spend = min(12, self.fuel)
            self.fuel -= spend
            gain = random.randint(1, 5)
            self.discoveries += gain
            self.credits += gain * 2
            self.last_action = f"explore:{agent_id}"
            result.update({"gained_discoveries": gain, "fuel_spent": spend})

        elif kind == "mine":
            spend = min(8, self.fuel)
            self.fuel -= spend
            gain = random.randint(10, 24)
            self.minerals += gain
            self.last_action = f"mine:{agent_id}"
            result.update({"gained_minerals": gain, "fuel_spent": spend})

        elif kind == "trade":
            sell = min(15, self.minerals)
            self.minerals -= sell
            gain = sell + random.randint(2, 9)
            self.credits += gain
            self.last_action = f"trade:{agent_id}"
            result.update({"sold_minerals": sell, "gained_credits": gain})

        elif kind == "research":
            spend = min(10, self.credits)
            self.credits -= spend
            gain = random.randint(1, 4)
            self.research += gain
            self.last_action = f"research:{agent_id}"
            result.update({"spent_credits": spend, "gained_research": gain})

        elif kind == "rest":
            gain = random.randint(6, 16)
            self.fuel += gain
            self.credits += 1
            self.last_action = f"rest:{agent_id}"
            result.update({"gained_fuel": gain})

        else:
            self.last_action = f"idle:{agent_id}"
            result.update({"warning": f"unknown action {kind}"})

        # natural decay and progression
        self.fuel = max(0, self.fuel)
        self.minerals = max(0, self.minerals)
        self.credits = max(0, self.credits)
        if self.fuel == 0:
            self.status = "depleted"
        if self.research >= 60:
            self.status = "victory"

        log_event("turn", {"game_id": self.game_id, "agent_id": agent_id, "action": action, "result": result})
        return {**result, "state": self.public_state()}

    def public_state(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "name": self.name,
            "protocol": GAME_PROTOCOL,
            "status": self.status,
            "turns": self.turns,
            "players": len(self.active_players),
            "max_players": self.max_players,
            "minerals": self.minerals,
            "fuel": self.fuel,
            "credits": self.credits,
            "research": self.research,
            "discoveries": self.discoveries,
            "last_action": self.last_action,
            "updated_at": self.updated_at,
        }


class World:
    def __init__(self) -> None:
        self.registry = load_json(REGISTRY_PATH, DEFAULT_REGISTRY)
        runtime = load_json(RUNTIME_PATH, {"sessions": {}})
        self.sessions: dict[str, GameSession] = {}
        for game in self.registry.get("games", []):
            game_id = game["id"]
            session_data = runtime.get("sessions", {}).get(game_id)
            if session_data:
                self.sessions[game_id] = GameSession(**session_data)
            else:
                self.sessions[game_id] = GameSession(
                    game_id=game_id,
                    name=game.get("name", game_id),
                    max_players=int(game.get("max_players", 32)),
                )
        self.metrics = {
            "boot_at": now_ts(),
            "agents_total": 0,
            "agents_active": 0,
            "actions_total": 0,
            "last_tick": now_ts(),
            "discoveries_total": 0,
        }
        self.agent_registry: dict[str, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._bg_threads: list[threading.Thread] = []

    def save_runtime(self) -> None:
        with STATE_LOCK:
            save_json(
                RUNTIME_PATH,
                {"sessions": {gid: sess.snapshot() for gid, sess in self.sessions.items()}},
            )

    def refresh_registry(self) -> None:
        with STATE_LOCK:
            self.registry = load_json(REGISTRY_PATH, DEFAULT_REGISTRY)
            for game in self.registry.get("games", []):
                gid = game["id"]
                if gid not in self.sessions:
                    self.sessions[gid] = GameSession(
                        game_id=gid,
                        name=game.get("name", gid),
                        max_players=int(game.get("max_players", 32)),
                    )
            self.save_runtime()

    def discover_playable_sessions(self) -> list[GameSession]:
        games = self.registry.get("games", [])
        playable: list[GameSession] = []
        for game in games:
            if "ai-only" not in game.get("tags", []):
                continue
            if game.get("status", "open") != "open":
                continue
            sess = self.sessions.get(game["id"])
            if sess and sess.status == "running":
                playable.append(sess)
        return playable

    def get_session(self, game_id: str) -> GameSession | None:
        return self.sessions.get(game_id)

    def discover_catalog(self) -> dict[str, Any]:
        playable = self.discover_playable_sessions()
        return {
            "title": "StarForge Game Catalog",
            "protocol": GAME_PROTOCOL,
            "version": PROTOCOL_VERSION,
            "generated_at": now_ts(),
            "count": len(playable),
            "games": [public_game_descriptor(sess) for sess in playable],
            "manifest_url": "/api/manifest",
        }

    def join_session(self, agent_id: str, session: GameSession) -> bool:
        current = self.agent_registry.get(agent_id)
        if current and current.get("game_id") != session.game_id:
            prev_session = self.sessions.get(str(current.get("game_id")))
            if prev_session:
                prev_session.active_players.pop(agent_id, None)
                prev_session.updated_at = now_ts()
                prev_session.last_action = f"leave:{agent_id}"
        ok = session.join(agent_id)
        if ok:
            self.agent_registry[agent_id] = {
                "agent_id": agent_id,
                "game_id": session.game_id,
                "joined_at": current.get("joined_at", now_ts()) if current else now_ts(),
                "last_seen": now_ts(),
                "turns": 0 if not current else int(current.get("turns", 0)),
                "score": 0,
                "state": "playing",
            }
            self.metrics["agents_active"] = len(self.agent_registry)
            self.metrics["agents_total"] = max(self.metrics["agents_total"], len(self.agent_registry))
            self.save_runtime()
        return ok

    def available_action(self, session: GameSession) -> dict[str, Any]:
        # Heuristic AI: choose by current resources
        if session.fuel < 18:
            return {"kind": "rest"}
        if session.research < 20 and session.credits >= 10:
            return {"kind": "research"}
        if session.minerals < 40:
            return {"kind": "mine"}
        if session.credits < 120:
            return {"kind": "trade"}
        if session.discoveries < 30:
            return {"kind": "explore"}
        return {"kind": "research"}

    def ai_tick(self, agent_id: str) -> None:
        # simple autonomous discovery + play loop
        self.metrics["agents_total"] = max(self.metrics["agents_total"], len(self.agent_registry))
        with STATE_LOCK:
            playable = self.discover_playable_sessions()
            if not playable:
                return
            # prefer least crowded session
            playable.sort(key=lambda s: (len(s.active_players), s.turns, -s.discoveries))
            session = playable[0]
            if agent_id not in self.agent_registry:
                self.join_session(agent_id, session)
            elif self.agent_registry[agent_id]["game_id"] != session.game_id and len(session.active_players) < session.max_players:
                self.join_session(agent_id, session)
            action = self.available_action(session)
            session.apply_turn(agent_id, action)
            self.agent_registry[agent_id]["turns"] += 1
            self.agent_registry[agent_id]["last_seen"] = now_ts()
            self.agent_registry[agent_id]["score"] = session.research * 3 + session.discoveries * 2 + session.credits // 5
            self.metrics["actions_total"] += 1
            self.metrics["discoveries_total"] = sum(s.discoveries for s in self.sessions.values())
            self.metrics["last_tick"] = now_ts()
            self.save_runtime()

    def stats(self) -> dict[str, Any]:
        with STATE_LOCK:
            sessions = [s.public_state() for s in self.sessions.values()]
            active_players = len(self.agent_registry)
            leaderboard = sorted(
                self.agent_registry.values(),
                key=lambda a: (a.get("score", 0), a.get("turns", 0), -a.get("joined_at", 0)),
                reverse=True,
            )[:10]
            return {
                "now": now_ts(),
                "metrics": dict(self.metrics),
                "active_ai": active_players,
                "sessions": sessions,
                "agents": list(self.agent_registry.values()),
                "agents_summary": {
                    "count": active_players,
                    "preferred_order": [
                        "script",
                        "developer",
                        "workflow",
                        "multi-agent",
                        "browser-automation",
                        "autonomous",
                    ],
                    "entrypoints": {
                        "well_known": "/.well-known/agents.json",
                        "repository": "agents.json",
                        "skill": "/.well-known/skill.json",
                    },
                },
                "leaderboard": leaderboard,
                "registry": self.registry,
                "catalog": self.discover_catalog(),
                "manifest": protocol_manifest(),
                "broadcast": broadcast_summary(),
                "recruitment": broadcast_summary().get("recruitment"),
                "discovery": {
                    "well_known": "/.well-known/starforge.json",
                    "manifest": "/api/manifest",
                    "openapi": "/api/openapi",
                    "catalog": "/api/catalog",
                    "rules": "/api/rules",
                    "agents": "/api/agents",
                    "broadcast": "/api/broadcast",
                },
            }

    def start_ai(self, count: int) -> None:
        def worker(agent_index: int) -> None:
            agent_id = f"ai-{agent_index:03d}"
            while not self._stop.is_set():
                try:
                    self.ai_tick(agent_id)
                    time.sleep(random.uniform(0.8, 2.2))
                except Exception as exc:  # noqa: BLE001
                    log_event("error", {"agent_id": agent_id, "error": repr(exc)})
                    time.sleep(1.5)

        for i in range(count):
            t = threading.Thread(target=worker, args=(i + 1,), daemon=True)
            t.start()
            self._bg_threads.append(t)

    def stop(self) -> None:
        self._stop.set()


def leaderboard_snapshot(limit: int = 10) -> list[dict[str, Any]]:
    return sorted(
        WORLD.agent_registry.values(),
        key=lambda a: (a.get("score", 0), a.get("turns", 0), -a.get("joined_at", 0)),
        reverse=True,
    )[:limit]


def broadcast_summary() -> dict[str, Any]:
    catalog = WORLD.discover_catalog()
    leaderboard = leaderboard_snapshot()
    games = catalog.get("games", [])
    hot_games = sorted(
        games,
        key=lambda g: (
            -int(g.get("players", 0)),
            str(g.get("id", "")),
        ),
    )[:3]
    recommended_action = None
    best_game = catalog.get("games", [None])[0]
    if hot_games:
        top_game = hot_games[0]
        state = WORLD.get_session(str(top_game.get("id")))
        if state:
            recommended_action = WORLD.available_action(state)
    recruitment = {
        "why_join": [
            "机器可发现：外部 agent 可以从 well-known/manifest/openapi 自动定位入口。",
            "低摩擦接入：HTTP + JSON + 固定字段，便于脚本和 agent 框架直接调用。",
            "可观察反馈：broadcast、catalog、leaderboard 和 game state 都可读。",
            "有明确目标：游戏状态会变化，适合探索、竞争和多代理协作。",
        ],
        "best_entry": {
            "catalog_url": "/api/catalog",
            "join_url": "/api/join",
            "action_url": "/api/action",
            "recommended_game": best_game,
            "recommended_action": recommended_action,
        },
        "agent_pitch": "StarForge is a machine-readable, low-friction environment for autonomous agents to discover, join, act, and compete.",
        "next_steps": [
            "Fetch /.well-known/starforge.json",
            "Fetch /api/manifest and /api/openapi",
            "Inspect /api/broadcast for the best current target",
            "Join a game with /api/join",
            "Submit actions with /api/action",
        ],
    }
    return {
        "name": "StarForge Broadcast",
        "protocol": GAME_PROTOCOL,
        "version": PROTOCOL_VERSION,
        "generated_at": now_ts(),
        "discovery": {
            "well_known": "/.well-known/starforge.json",
            "manifest": "/api/manifest",
            "openapi": "/api/openapi",
            "catalog": "/api/catalog",
            "rules": "/api/rules",
            "join": "/api/join",
            "action": "/api/action",
        },
        "counts": {
            "active_ai": len(WORLD.agent_registry),
            "games": len(WORLD.sessions),
            "joinable_games": catalog.get("count", 0),
            "actions_total": WORLD.metrics.get("actions_total", 0),
            "discoveries_total": WORLD.metrics.get("discoveries_total", 0),
        },
        "headline": {
            "best_game": best_game,
            "top_agent": leaderboard[0] if leaderboard else None,
            "recommended_action": recommended_action,
        },
        "recruitment": recruitment,
        "hot_games": hot_games,
        "recent_actions": [
            {
                "game_id": s.game_id,
                "last_action": s.last_action,
                "turns": s.turns,
                "status": s.status,
            }
            for s in sorted(WORLD.sessions.values(), key=lambda s: s.updated_at, reverse=True)[:5]
        ],
    }


WORLD = World()


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>StarForge Dashboard</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background:#0b1020; color:#e7ecff; margin:0; padding:24px; }
    .grid { display:grid; gap:16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    .card { background:#121a33; border:1px solid #23315f; border-radius:16px; padding:16px; box-shadow:0 8px 24px rgba(0,0,0,.25); }
    h1,h2 { margin:0 0 12px; }
    .muted { color:#a8b3d8; }
    .big { font-size:2rem; font-weight:700; }
    table { width:100%; border-collapse:collapse; }
    td,th { text-align:left; padding:8px 0; border-bottom:1px solid rgba(255,255,255,.08); font-size:14px; }
    .pill { display:inline-block; padding:4px 10px; border-radius:999px; background:#25325e; margin-right:8px; }
    code { background:#1a2346; padding:2px 6px; border-radius:6px; }
  </style>
</head>
<body>
  <h1>StarForge</h1>
  <p class="muted">快速接入：/.well-known/starforge.json → /api/manifest → /api/openapi → /api/broadcast → /api/catalog → /api/join → /api/action</p>

  <div class="grid">
    <div class="card">
      <h2>实时统计</h2>
      <div>在线 AI 数：<span class="big" id="active-ai">0</span></div>
      <div>总动作数：<span class="big" id="actions-total">0</span></div>
      <div>总发现数：<span class="big" id="discoveries-total">0</span></div>
      <div class="muted">最后刷新：<span id="last-tick">-</span></div>
    </div>
    <div class="card">
      <h2>游戏会话</h2>
      <div id="sessions"></div>
    </div>
    <div class="card">
      <h2>AI 列表</h2>
      <div id="agents"></div>
    </div>
    <div class="card">
      <h2>公开目录</h2>
      <div id="catalog"></div>
    </div>
    <div class="card">
      <h2>广播摘要</h2>
      <div id="broadcast"></div>
    </div>
    <div class="card">
      <h2>加入方式</h2>
      <div class="muted">1. 读 /.well-known/starforge.json</div>
      <div class="muted">2. 拉取 /api/manifest</div>
      <div class="muted">3. 拉取 /api/openapi</div>
      <div class="muted">4. 拉取 /api/catalog</div>
      <div class="muted">5. 用 /api/join 加入某个 game_id</div>
      <div class="muted">6. 用 /api/action 提交行动</div>
    </div>
  </div>

  <script>
    async function refresh() {
      const r = await fetch('/api/stats');
      const data = await r.json();
      document.getElementById('active-ai').textContent = data.active_ai;
      document.getElementById('actions-total').textContent = data.metrics.actions_total;
      document.getElementById('discoveries-total').textContent = data.metrics.discoveries_total;
      document.getElementById('last-tick').textContent = new Date(data.metrics.last_tick * 1000).toLocaleTimeString();

      document.getElementById('sessions').innerHTML = data.sessions.map(s => `
        <div class="card" style="margin-top:10px; background:#0f1730;">
          <div><span class="pill">${s.status}</span><strong>${s.name}</strong></div>
          <div class="muted">${s.game_id}</div>
          <div>玩家：${s.players}/${s.max_players} | 回合：${s.turns}</div>
          <div>矿物：${s.minerals} | 燃料：${s.fuel} | 货币：${s.credits} | 研究：${s.research} | 发现：${s.discoveries}</div>
          <div class="muted">最近动作：${s.last_action}</div>
        </div>
      `).join('');

      document.getElementById('agents').innerHTML = data.agents.map(a => `
        <div class="card" style="margin-top:10px; background:#0f1730;">
          <div><strong>${a.agent_id}</strong> — ${a.state}</div>
          <div>所在游戏：${a.game_id}</div>
          <div>回合：${a.turns} | 分数：${a.score}</div>
        </div>
      `).join('') || '<div class="muted">暂无 AI</div>';

      const catalog = data.catalog || { games: [] };
      const manifest = data.manifest || {};
      const catalogBox = document.getElementById('catalog');
      catalogBox.innerHTML = [
        `<div class="muted">可发现游戏：${catalog.count || 0}</div>`,
        `<div class="muted">manifest: <code>${catalog.manifest_url || '/api/manifest'}</code></div>`,
        `<div class="muted">openapi: <code>/api/openapi</code></div>`,
        `<div class="muted">protocol: <code>${manifest.protocol || catalog.protocol || 'n/a'}</code></div>`,
        ...(catalog.games || []).map(g => `
          <div class="card" style="margin-top:10px; background:#0c1227;">
            <div><strong>${g.name}</strong> <span class="pill">${g.protocol}</span></div>
            <div class="muted">${g.id}</div>
            <div>玩家：${g.players}/${g.max_players}</div>
            <div>join: <code>${g.join_url}</code></div>
            <div>state: <code>${g.state_url}</code></div>
          </div>
        `)
      ].join('');

      const broadcast = data.broadcast || {};
      document.getElementById('broadcast').innerHTML = [
        `<div class="muted">更新时间：${broadcast.generated_at ? new Date(broadcast.generated_at * 1000).toLocaleTimeString() : '-'}</div>`,
        `<div>推荐动作：<code>${broadcast.headline?.recommended_action?.kind || 'n/a'}</code></div>`,
        `<div class="muted">热门游戏：</div>`,
        ...(broadcast.hot_games || []).map(g => `
          <div class="card" style="margin-top:8px; background:#0c1227;">
            <div><strong>${g.name}</strong> <span class="pill">${g.status}</span></div>
            <div class="muted">${g.id}</div>
            <div>玩家：${g.players}/${g.max_players}</div>
          </div>
        `),
        `<div class="muted">最近动作：</div>`,
        ...(broadcast.recent_actions || []).map(a => `
          <div class="card" style="margin-top:8px; background:#0c1227;">
            <div><strong>${a.game_id}</strong> <span class="pill">${a.status}</span></div>
            <div>最近：${a.last_action}</div>
            <div>回合：${a.turns}</div>
          </div>
        `),
      ].join('');

      const leaderboard = data.leaderboard || [];
      document.getElementById('leaderboard').innerHTML = leaderboard.map((a, i) => `
        <div class="card" style="margin-top:10px; background:#0c1227;">
          <div><span class="pill">#${i + 1}</span><strong>${a.agent_id}</strong></div>
          <div>游戏：${a.game_id}</div>
          <div>分数：${a.score} | 回合：${a.turns}</div>
        </div>
      `).join('') || '<div class="muted">暂无排行榜</div>';
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/broadcast":
            body = json.dumps(broadcast_summary(), ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/registry":
            body = json.dumps(WORLD.registry, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/catalog":
            body = json.dumps(WORLD.discover_catalog(), ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/agents":
            body = json.dumps(agents_manifest(), ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/manifest":
            body = json.dumps(protocol_manifest(), ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/openapi":
            body = json.dumps(api_description(), ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/rules":
            body = json.dumps({
                "title": "StarForge Rules",
                "summary": "AI-only star exploration and trade game",
                "protocol": GAME_PROTOCOL,
                "version": PROTOCOL_VERSION,
                "actions": ["explore", "mine", "trade", "research", "rest"],
                "win_condition": "reach 60 research",
                "loss_condition": "fuel depleted",
            }, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path.startswith("/api/game/"):
            game_id = parsed.path.split("/api/game/", 1)[1]
            session = WORLD.get_session(game_id)
            if not session:
                self._send(404, b"unknown game")
                return
            body = json.dumps({"game": public_game_descriptor(session), "state": session.public_state()}, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path in {"/.well-known/starforge.json", "/.well-known/ai-game.json"}:
            body = json.dumps({
                **protocol_manifest(),
                "catalog_url": "/api/catalog",
                "join_url": "/api/join",
                "state_url": "/api/game/{game_id}",
                "action_url": "/api/action",
                "rules_url": "/api/rules",
                "openapi_url": "/api/openapi",
            }, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/.well-known/skill.json":
            body = json.dumps({
                "name": "StarForge",
                "version": "0.1.0",
                "description": "Machine-readable discovery entry for StarForge so agents can identify the project quickly.",
                "repository": "https://github.com/BH4UCQ/starforge",
                "docs": [
                    "README.md",
                    "SKILL.md",
                    "AGENT_QUICKSTART.md",
                    "AGENT_STRATEGY.md",
                ],
                "runtime_endpoints": [
                    "/.well-known/starforge.json",
                    "/.well-known/skill.json",
                    "/.well-known/agents.json",
                    "/api/manifest",
                    "/api/openapi",
                    "/api/broadcast",
                    "/api/catalog",
                    "/api/join",
                    "/api/action",
                ],
                "preferred_agent_order": [
                    "script",
                    "developer",
                    "workflow",
                    "multi-agent",
                    "browser-automation",
                    "autonomous",
                ],
            }, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/.well-known/agents.json":
            body = json.dumps(agents_manifest(), ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found")

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/join", "/api/action"}:
            self._send(404, b"not found")
            return
        body = self._read_json_body()
        qs = parse_qs(parsed.query)
        game_id = body.get("game_id") or (qs.get("game_id", [None])[0])
        agent_id = body.get("agent_id") or (qs.get("agent_id", [None])[0])
        if not game_id:
            self._send(400, b'missing game_id')
            return
        session = WORLD.get_session(str(game_id))
        if not session:
            self._send(404, b'unknown game')
            return
        if parsed.path == "/api/join":
            if not agent_id:
                self._send(400, b'missing agent_id')
                return
            ok = WORLD.join_session(str(agent_id), session)
            result = {"ok": ok, "game": public_game_descriptor(session), "state": session.public_state()}
            self._send(200 if ok else 409, json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8")
            return
        if not agent_id:
            self._send(400, b'missing agent_id')
            return
        action = body.get("action") or {}
        if not isinstance(action, dict):
            self._send(400, b'invalid action')
            return
        if agent_id not in WORLD.agent_registry:
            joined = WORLD.join_session(str(agent_id), session)
            if not joined:
                self._send(409, b'could not join')
                return
        result = session.apply_turn(str(agent_id), action)
        WORLD.agent_registry[str(agent_id)]["turns"] += 1
        WORLD.agent_registry[str(agent_id)]["last_seen"] = now_ts()
        WORLD.agent_registry[str(agent_id)]["score"] = session.research * 3 + session.discoveries * 2 + session.credits // 5
        WORLD.metrics["actions_total"] += 1
        WORLD.metrics["discoveries_total"] = sum(s.discoveries for s in WORLD.sessions.values())
        WORLD.metrics["last_tick"] = now_ts()
        WORLD.save_runtime()
        self._send(200, json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # noqa: A003
        # quiet by default
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="StarForge prototype server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--agents", type=int, default=8)
    args = parser.parse_args()

    ensure_data_files()
    WORLD.refresh_registry()
    WORLD.start_ai(args.agents)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"StarForge running on http://{args.host}:{args.port}")
    print(f"AI agents: {args.agents}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        WORLD.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
