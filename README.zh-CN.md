# overleaf-ce-agent-sync

面向自托管 Overleaf Community Edition 的 agent-safe Git 风格同步工具。

这个项目的目标是把本地 Git 作为 AI 编程 agent 和 Overleaf CE 之间的安全层。工具会把
Overleaf 项目当作一个远端快照导入本地 Git 分支，让 Git 负责发现文件级冲突，并且只在
拉取最新状态、合并无冲突、写回后验证成功的情况下标记同步完成。

它不是 Overleaf Server Pro 官方 Git 集成的替代品，也不实现 Git 协议服务器。它面向的是
Overleaf CE 用户：没有官方 Git bridge，但希望 Codex、Claude Code、Cline 或本地编辑器能在
Git 保护下安全修改 LaTeX 项目。

## 核心工作流

推荐工作方式：

```bash
cd ~/papers/my-paper
ol pull
# 让 agent 或编辑器修改 .tex/.bib/.sty/.cls 等文件
ol status
git diff
git add -A
git commit -m "agent: revise introduction"
ol push --dry-run
ol push
```

`pull` 会先把远端最新快照导入 `overleaf-remote`，再把合并结果放到暂存区等待你确认提交，
不会直接替你生成 merge commit。`push` 前会再做一次 freshness check；如果 Overleaf 网页端和
本地同时改了同一个文件，Git 会产生冲突，工具会停止，不会静默覆盖远端新内容。

## 当前 MVP 状态

当前版本已经提供：

- `ol init`
- `ol pull`
- `ol push`
- `ol status`
- `ol verify`
- 真实 HTTP/session-cookie backend，用于连接自托管 Overleaf CE
- `ol auth login/status/logout`
- 后续 `pyoverleaf` backend 的接口结构

HTTP backend 使用 Overleaf 编辑器同类的 Web/session 行为。登录 cookie 会保存到
`.ol-sync/session.json`，该文件默认被 `.gitignore` 忽略，不应提交到 Git。

## 绑定 / 登录 Overleaf CE 账号

如果你的自托管 Overleaf CE 支持普通邮箱密码登录，可以运行：

```bash
ol auth login --host http://localhost --email you@example.com
```

命令会提示输入密码，并把登录后的 session cookie 保存到 `.ol-sync/session.json`。

检查登录状态：

```bash
ol auth status --host http://localhost
```

退出登录，也就是删除本地保存的 session：

```bash
ol auth logout
```

如果你的 Overleaf 启用了 captcha、SSO，或密码登录被前端策略拦住，可以先在浏览器里登录，
然后复制 Cookie header，用 cookie 登录：

```bash
ol auth login --host http://localhost --cookie 'sharelatex.sid=...'
```

真实项目配置示例：

```toml
[project]
host = "http://localhost"
project_id = "YOUR_PROJECT_ID"
project_name = "my-paper"

[backend]
type = "http"

[auth]
session_file = ".ol-sync/session.json"
```

## 安装与开发

本项目推荐使用 `uv` 创建本地环境：

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
```

如果已经创建了 `.venv`：

```bash
source .venv/bin/activate
pytest
ruff check .
```

也可以直接使用 `.venv` 中的命令：

```bash
.venv/bin/ol --help
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

## 安全原则

这个项目优先保证不丢数据，而不是追求实时同步。

硬性原则：

- 不直接修改 Docker volume、MongoDB、Redis 或 Overleaf 编译缓存。
- 不实现字符级或保存级实时同步。
- 不绕过 Git 做自定义文本合并。
- 默认拒绝脏工作区上的危险操作。
- `push` 前必须重新拉取远端快照，并先处理 freshness pull 产生的暂存变更或冲突。
- 有 Git 冲突时立即停止。
- 写回后必须重新下载远端快照并验证，验证失败不会更新同步状态。

最高优先级是不让陈旧的本地 agent 输出静默覆盖 Overleaf 上的新修改。

## 常用命令

初始化一个真实 Overleaf CE 项目：

```bash
ol init --host http://localhost --project-id YOUR_PROJECT_ID --project-name my-paper
```

拉取远端快照：

```bash
ol pull
```

查看状态：

```bash
ol status
```

预览 push 计划：

```bash
ol push --dry-run
```

验证本地状态和远端快照是否一致：

```bash
ol verify
```

## 当前限制

- 当前只把 Overleaf 视作单一项目快照，不做复杂多分支同步。
- 不保留 Overleaf comments / tracked changes 等协作元数据。
- `.pdf` 等编译产物默认在 ignore 列表中，除非后续显式启用资产同步策略。
