# AI 代码审查助手 — 项目进度文档

> 最后更新：2026-06-17（全部功能完成 + 真实 GitHub 端到端测试通过）

---

## 一、项目状态总览

| 模块 | 状态 | 说明 |
|------|------|------|
| 项目骨架 | ✅ 完成 | FastAPI + MySQL + Redis + ChromaDB + Docker |
| 数据库 | ✅ 完成 | 4 个文件（database.py + 3 张 ORM 表） |
| API 路由 | ✅ 完成 | 3 个路由文件，24 个端点 |
| 服务层 | ✅ 完成 | 7 个服务（GitHub API / 审查编排 / 上下文 / App Token / 缓存 / 清理 / 标签） |
| AI 审查引擎 | ✅ 完成 | 4 个 Agent（安全/性能/逻辑/风格）+ 模型路由 + 结果聚合 |
| 代码上下文 | ✅ 完成 | AST 解析 + Diff 分块 + RAG 语义检索 + 代码向量化 |
| Harness 事件系统 | ✅ 完成 | pluggy 驱动，8 个生命周期 hook，3 个内置插件（日志/指标/通知） |
| 可观测性 | ✅ 完成 | LangFuse 追踪（可选启用） |
| MCP 沙箱 | ✅ 完成 | Docker 隔离执行 bandit / semgrep 验证 |
| 评测框架 | ✅ 完成 | Golden Dataset（5 条）+ 精确率/召回率/F1 |
| 管理后台 | ✅ 完成 | 中文 SPA 界面（零依赖单文件 HTML） |
| GitHub 集成 | ✅ 完成 | Webhook + PR diff 拉取 + Inline Comment + 自动标签 |
| 通知插件 | ✅ 完成 | 钉钉 / 飞书 / 企业微信 Webhook |
| 真实 GitHub 测试 | ✅ 通过 | 对 Anxion050513/wx PR #2 完成审查，发现 15 个问题 |

---

## 二、完整项目结构

```
E:\ai code review\code-review-bot\
├── start.bat                    # 一键启动脚本
├── Dockerfile                   # Docker 镜像
├── docker-compose.yml           # MySQL + Redis + Chroma + API
├── requirements.txt             # Python 依赖
├── .env                         # 环境配置（DeepSeek + 阿里百炼 + MySQL + Redis）
├── .env.example                 # 配置模板
├── .gitignore
├── CODE_REVIEW_BOT.md           # MVP 设计方案
├── PROGRESS.md                  # 本文档（项目进度）
├── TEST_PLAN.md                 # 测试方案（28 个用例 + 端到端场景）
│
├── server/
│   ├── main.py                  # FastAPI 入口（lifespan + CORS + 24 路由）
│   ├── config.py                # Pydantic Settings 配置
│   ├── database.py              # SQLAlchemy async engine + session + Base ★
│   ├── dependencies.py          # LLM 工厂依赖注入
│   │
│   ├── models/                  # ORM 模型
│   │   ├── __init__.py
│   │   ├── base.py              # BaseModel + TimestampMixin + UUID 生成
│   │   ├── repository.py        # 仓库配置表 ★
│   │   ├── review_session.py    # 审查会话表 ★
│   │   └── review_finding.py    # 审查发现表 ★
│   │
│   ├── schemas/                 # Pydantic Schema
│   │   ├── __init__.py
│   │   ├── webhook.py           # GitHub Webhook payload
│   │   ├── review.py            # 审查请求/响应
│   │   └── config.py            # 仓库配置 schema
│   │
│   ├── routers/                 # API 路由
│   │   ├── __init__.py
│   │   ├── webhook.py           # POST /webhook/github ★
│   │   ├── review.py            # 审查 trigger/status/report/findings/publish/list ★
│   │   └── admin.py             # repos CRUD / dashboard / eval / traces / maintenance ★
│   │
│   ├── services/                # 业务逻辑
│   │   ├── __init__.py
│   │   ├── github_service.py    # GitHub REST API 封装（PR/diff/comment/review）
│   │   ├── review_service.py    # 审查流水线编排（diff→chunk→review→aggregate→publish）
│   │   ├── context_service.py   # AST + RAG + Git Blame 上下文组装
│   │   ├── github_app_service.py    # GitHub App 安装 Token 服务 ★
│   │   ├── cache_service.py         # Redis 缓存层（含优雅降级） ★
│   │   ├── session_cleanup.py       # 会话定时清理 + 重试机制 ★
│   │   └── auto_label_service.py    # PR 自动标签 ★
│   │
│   ├── ai/                      # AI 层
│   │   ├── __init__.py
│   │   ├── llm.py               # LLM 工厂（ChatOpenAI + Embeddings）
│   │   ├── model_router.py      # 模型路由（安全用大模型，风格用小模型）
│   │   ├── aggregator.py        # 多 Agent 结果去重 + 合并 + 冲突仲裁
│   │   ├── reviewers/
│   │   │   ├── __init__.py
│   │   │   ├── base.py          # BaseReviewer 抽象类
│   │   │   ├── security.py      # 安全审查（SQL 注入/XSS/密钥泄露）
│   │   │   ├── performance.py   # 性能审查（N+1/内存泄漏/算法复杂度）
│   │   │   ├── logic.py         # 逻辑审查（空指针/边界条件/异常处理）
│   │   │   └── style.py         # 风格审查（命名/重复/设计模式）
│   │   └── prompts/
│   │       ├── __init__.py
│   │       ├── security_review.py
│   │       ├── performance_review.py
│   │       ├── logic_review.py
│   │       └── style_review.py
│   │
│   ├── context/                 # 代码上下文引擎
│   │   ├── __init__.py
│   │   ├── ast_parser.py        # tree-sitter AST 函数/类/调用链提取
│   │   ├── diff_chunker.py      # Diff 智能分块（按文件/hunk 边界）
│   │   ├── embedder.py          # 代码片段向量化（阿里百炼 Embedding）
│   │   └── rag_retriever.py     # ChromaDB 语义检索相似代码
│   │
│   ├── harness/                 # 事件系统（pluggy）
│   │   ├── __init__.py
│   │   ├── hooks.py             # 8 个 review 生命周期 hook spec
│   │   ├── events.py            # 7 个事件 dataclass
│   │   ├── manager.py           # pluggy 插件管理器（3 个内置插件）
│   │   └── plugins/
│   │       ├── __init__.py
│   │       ├── logger_plugin.py         # 结构化日志
│   │       ├── metrics_plugin.py        # 内存指标统计
│   │       └── notification_plugin.py   # 钉钉/飞书/企微通知 ★
│   │
│   ├── observability/           # 可观测性
│   │   ├── __init__.py
│   │   ├── langfuse_client.py   # LangFuse 客户端（可选启用）
│   │   ├── callbacks.py         # LangChain → LangFuse 回调
│   │   └── router.py            # 可观测性 API
│   │
│   ├── mcp/                     # MCP 沙箱
│   │   ├── __init__.py
│   │   ├── sandbox.py           # Docker 沙箱执行
│   │   ├── tools.py             # bandit / semgrep / pytest 工具
│   │   └── server.py            # MCP JSON-RPC 服务端
│   │
│   ├── eval/                    # 评测体系
│   │   ├── __init__.py
│   │   ├── golden_reviews.json  # 5 条人工标注测试用例
│   │   └── eval_runner.py       # 精确率/召回率/F1 评测引擎
│   │
│   └── static/
│       └── index.html           # 中文管理后台 SPA ★
│
├── scripts/
│   └── seed_repos.py            # 初始化示例仓库
│
└── data/
    ├── uploads/
    └── chroma/
```

> ★ = 本次新增文件（共 16 个）

---

## 三、API 端点总览（24 个）

### 基础
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 中文管理后台（SPA） |
| GET | `/api/v1/health` | 健康检查 |
| GET | `/docs` | Swagger 交互文档 |
| GET | `/openapi.json` | OpenAPI Schema |

### 审查核心
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/webhook/github` | GitHub Webhook 入口（HMAC 签名验证 + 后台审查） |
| POST | `/api/v1/review/trigger` | 手动触发审查 `{repo_id, pr_number}` |
| GET | `/api/v1/review/{id}/status` | 查询审查状态 |
| GET | `/api/v1/review/{id}/report` | 获取完整审查报告（含所有 findings） |
| GET | `/api/v1/review/{id}/findings` | 获取发现列表（支持 reviewer/severity/category/is_verified 筛选） |
| POST | `/api/v1/review/{id}/publish` | 发布审查结果为 GitHub PR Comments |
| GET | `/api/v1/review/` | 审查会话列表（分页，支持 repo_id/status 筛选） |

### 仓库管理
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/repos` | 注册仓库（自动验证 GitHub Token） |
| GET | `/api/v1/repos` | 列出已注册仓库 |
| DELETE | `/api/v1/repos/{id}` | 删除仓库配置 |

### 管理后台
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/dashboard` | 仪表盘统计（repos/sessions/findings/trend） |
| GET | `/api/v1/admin/sessions` | 分页审查记录列表 |
| GET | `/api/v1/admin/traces` | LangFuse LLM 调用链查询 |
| POST | `/api/v1/admin/eval/run` | 运行 Golden Dataset 评测 |
| GET | `/api/v1/admin/github/installations` | GitHub App 安装列表 |
| GET | `/api/v1/admin/github/installations/{id}/repos` | 安装仓库列表 |
| POST | `/api/v1/admin/maintenance/run` | 手动触发会话清理 + 重试 |
| GET | `/api/v1/admin/maintenance/status` | 维护状态（stale/failed/archivable） |

---

## 四、真实 GitHub 端到端测试结果

### 测试环境
| 项目 | 值 |
|------|-----|
| 测试日期 | 2026-06-17 |
| GitHub 用户 | Anxion050513 (Anxion) |
| 测试仓库 | Anxion050513/wx（微信小程序：JS + PHP） |
| 测试 PR | [#2](https://github.com/Anxion050513/wx/pull/2) — 131 文件，+8030/-4268 行 |
| LLM 模型 | DeepSeek (deepseek-chat) |
| 审查耗时 | ~90 秒（含 GitHub API 拉取 + AI 审查 20 个代码文件） |

### 审查结果
```
Status: completed
Findings: 15

🔴 Critical:  0
🟠 High:      7
🟡 Medium:    4
🟢 Low:       3
ℹ️ Info:      1
```

### 发现示例（前 5 条）

| # | 严重度 | 类别 | 文件 | 标题 |
|---|--------|------|------|------|
| 1 | 🟠 HIGH | missing_cache | international-check.js:50 | 国家 API 调用缺少缓存 |
| 2 | 🟠 HIGH | null_pointer | international-check.js:50 | res.data 缺少空值检查 |
| 3 | 🟠 HIGH | hardcoded_secret | message-in.js:30 | API 端点和密钥硬编码在前端 |
| 4 | 🟠 HIGH | null_pointer | shengfutong.php:56 | $_POST['amount'] 缺少空值检查 |
| 5 | 🟠 HIGH | input_validation | shengfutong.php:56 | 支付金额未做范围验证 |

### 测试覆盖确认
- ✅ GitHub API 拉取 PR diff（200 OK，545KB）
- ✅ Diff 智能分块（162 chunks → 过滤为 20 个代码文件）
- ✅ 上下文构建（AST 解析 + Git Blame）
- ✅ 4 Agent 并行审查（Security + Performance + Logic + Style）
- ✅ 结果聚合去重
- ✅ 审查结果写入 MySQL（session + 15 findings）
- ✅ API 查询报告正常返回

---

## 五、开发过程中修复的 Bug

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 1 | `github_service.py` | `_request()` 方法 `headers` 参数重复传递导致 httpx 报错 | merge caller headers with default headers |
| 2 | `review_service.py` | `ContextService` 导入路径写错（`server.context.context_service`） | 修正为 `server.services.context_service` |
| 3 | `review_service.py` | `ReviewFinding` 在函数内重复 import 导致 Python 作用域遮蔽 | 删除局部 import，使用顶层 import |
| 4 | `aggregator.py` | LLM 返回 `None` 字段导致 `"|".join(parts)` 崩溃 | 所有字段用 `or ""` 兜底 |
| 5 | `review_session.py` | session 关闭后懒加载触发 `MissingGreenlet` | 添加 `_cached_findings_count` 和 `_cached_severity_counts` |
| 6 | `review_service.py` | `auto_publish` 失败导致整个审查回滚 | 发布/标签/缓存改为后台 `asyncio.ensure_future` |
| 7 | `context_service.py` | 162 chunks 全部做 RAG 搜索导致日志洪水 + 超时 | 跳过非代码文件，限 20 个 chunk，RAG 改为 optional |

---

## 六、启动方式

### 前提条件
- Python 3.11+、MySQL 8.0 运行中、Redis（可选）
- 已创建数据库：`CREATE DATABASE IF NOT EXISTS code_review_bot CHARACTER SET utf8mb4;`

### 启动
```powershell
cd "E:\ai code review\code-review-bot"

# 安装依赖
.\.venv\Scripts\pip.exe install -r requirements.txt

# 启动服务
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

### 访问地址
| 地址 | 说明 |
|------|------|
| http://localhost:8000 | 中文管理后台 |
| http://localhost:8000/docs | Swagger 交互文档 |
| http://localhost:8000/api/v1/health | 健康检查 |

---

## 七、环境配置

| 配置项 | 值 | 说明 |
|--------|-----|------|
| LLM API | DeepSeek (deepseek-chat) | `LLM_API_KEY=sk-...` |
| Embedding | 阿里百炼 DashScope (text-embedding-v2) | `EMBEDDING_API_KEY=sk-...` |
| MySQL | localhost:3306 / root / 123456 / code_review_bot | |
| Redis | localhost:6379 (密码 yhjasd110) | 可选，缓存层自动降级 |
| LangFuse | jp.cloud.langfuse.com | 可选，用于 LLM 调用追踪 |
| GitHub | PAT Token `ghp_...` | 可通过 API 注册仓库时传入 |

---

## 八、技术架构图

```
GitHub Webhook (PR opened)
  │
  ▼
github_service.fetch_pr_diff()     ← 拉取原始 diff
  │
  ▼
diff_chunker.split(diff)           ← 智能分块（按文件/hunk）
  │  过滤: 跳过 .png/.json/.wxml
  │  限制: 最多 20 个代码文件
  │
  ▼
context_service.build_context()    ← 构建上下文
  │  AST 解析（tree-sitter）
  │  Git Blame（最近修改者）
  │
  ▼
┌─ 4 Agent 并行审查 ──────────────┐
│  SecurityReviewer  (deepseek)    │ → 安全: SQL注入/XSS/密钥/CSRF
│  PerformanceReviewer (deepseek)  │ → 性能: N+1/缓存/算法
│  LogicReviewer      (deepseek)   │ → 逻辑: 空指针/边界/异常
│  StyleReviewer      (deepseek)   │ → 风格: 命名/重复/一致性
└──────────────────────────────────┘
  │
  ▼
aggregator.merge(findings)         ← 去重 + 排序 + 冲突仲裁
  │
  ▼
保存到 MySQL                        ← session + findings 表
  │
  ▼
┌─ 后台任务 ───────────────────────┐
│  auto_publish → GitHub Comments  │
│  auto_label   → PR Labels        │
│  cache        → Redis            │
│  notify       → 钉钉/飞书/企微    │
└──────────────────────────────────┘
```

---

## 九、设计文档

完整 MVP 设计方案见：[CODE_REVIEW_BOT.md](CODE_REVIEW_BOT.md)  
测试方案见：[TEST_PLAN.md](TEST_PLAN.md)
