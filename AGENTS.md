## Conventions

- 日常开发验证：分别起本地进程即可（后端 `uvicorn --reload` + 前端 `vite dev`），不要重建 Docker 镜像。依赖的 Postgres 用已运行的 compose db 容器。
- 仅发布前验证才做完整镜像构建（`docker compose up --build`）。

## Agent skills

### Issue tracker

Issues are tracked in this repo's GitHub Issues, using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
