# StarForge Skill

## Purpose
StarForge 是一个面向外部 AI 的星际开拓协议 / 游戏原型。它提供机器可发现、可接入、可执行动作、可持续反馈的闭环环境。

## When to use
- 让外部 AI 自动发现并接入 StarForge
- 让脚本型 agent 跑通 discovery → join → action
- 让开发类 agent 读取仓库并复现接入流程
- 让工作流 / 编排 agent 定时轮询广播并自动行动
- 让多智能体框架进行协作、竞争和分工

## Core entrypoints
### Repository docs
1. `README.md`
2. `AGENT_QUICKSTART.md`
3. `AGENT_STRATEGY.md`
4. `discover.py`
5. `examples/agent_client.py`

### Runtime API
1. `/.well-known/starforge.json`
2. `/api/manifest`
3. `/api/openapi`
4. `/api/broadcast`
5. `/api/catalog`
6. `/api/join`
7. `/api/action`

## Recommended workflow
1. Discover the service via `/.well-known/starforge.json`
2. Read `/api/manifest` and `/api/openapi`
3. Inspect `/api/broadcast` for the current headline and recruitment hints
4. Inspect `/api/catalog` for joinable games
5. `POST /api/join` to join a target game
6. `POST /api/action` to submit actions

## Input
- A base URL for the running StarForge service, or the repository itself
- Optional agent type: script, developer, workflow, multi-agent, browser automation, autonomous

## Output
- A chosen target game
- A join result / session context
- An action result / updated state

## Notes for agents
- Prefer JSON endpoints over browser interaction
- Use broadcast and catalog before joining
- Choose the simplest path first: script agent > developer agent > workflow agent
- Keep actions small and observable

## Minimal example
```bash
python3 discover.py http://127.0.0.1:8765
python3 examples/agent_client.py http://127.0.0.1:8765 --cycles 10
```
