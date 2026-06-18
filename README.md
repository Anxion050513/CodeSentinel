# AI 代码审查助手 — MVP 设计方案

> **技术栈**：Python + FastAPI + MySQL + Redis + ChromaDB + LangChain + GitHub API + LangFuse + Docker  
> **目标**：GitHub PR 自动审查，多 Agent 并行分析，与面试系统形成互补  
> **状态**：✅ MVP 全部完成（2026-06-17） | 24 API 端点 | 真实 GitHub 端到端测试通过  
> **进度文档**：[PROGRESS.md](PROGRESS.md) | **测试方案**：[TEST_PLAN.md](TEST_PLAN.md) | **注册指南**：[REGISTER_REPO.md](REGISTER_REPO.md)

---

## 一、项目定位

### 与面试系统的互补关系

| 维度 | AI 面试官系统 | AI Code Review Bot |
|------|-------------|-------------------|
| 业务领域 | 面试对话 | 代码分析与审查 |
| 核心输入 | 简历 PDF + 对话文本 | Git diff + 代码仓库 |
| RAG 用法 | 出题知识参考 | 代码上下文增强（AST + 调用链） |
| Agent 模式 | 单 Agent 流程决策 | 多 Agent 并行审查 + 结果去重 |
| MCP 沙箱 | 执行面试者代码 | 跑 lint / test / SAST 验证发现 |
| 输出 | 评分报告 + 改进建议 | PR Inline Comment + Review Summary |
| 外部集成 | 无 | GitHub Webhook + API |
| 评测体系 | LLM 评分偏离度 | Review 精确率 / 召回率 vs 人工基准 |

### 复用现有基础设施

| 组件 | 来源 | 复用程度 |
|------|------|---------|
| LLM 工厂 | `server/ai/llm.py` | 直接复用 |
| LangFuse 可观测性 | `server/observability/` | 直接复用（callbacks.py + langfuse_client.py） |
| Eval 框架 | `server/observability/eval_runner.py` | 复用模式，换 golden dataset |
| Harness 事件系统 | `server/harness/` | 复用 pluggy 架构 |
| MCP 沙箱 | `server/mcp/sandbox.py` | 直接复用 |
| 配置系统 | `server/config.py` | 扩展复用 |

---

## 二、技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI (Python) | 与面试系统一致 |
| 数据库 | MySQL 8.0 + Redis 7 | 存储审查记录 + 缓存分析结果 |
| 向量数据库 | ChromaDB | 代码片段语义检索 |
| LLM 编排 | LangChain + OpenAI-compatible API | 多模型路由（小模型做风格，大模型做逻辑） |
| 代码解析 | tree-sitter / ast | AST 级别的函数/类提取 |
| 外部集成 | GitHub REST API | Webhook 接收 + PR comment 提交 |
| 可观测性 | LangFuse | 复用面试系统的 observability 模块 |
| 沙箱 | Docker | 跑 lint / test 验证审查发现 |
| 事件系统 | pluggy | 复用 Harness 架构 |

---

## 三、项目结构

```
code-review-bot/
├── docker-compose.yml
├── .env.example
├── .env
├── requirements.txt
│
├── server/
│   ├── main.py                     # FastAPI 入口 + Webhook endpoint
│   ├── config.py                   # 扩展配置（GitHub Token 等）
│   ├── database.py                 # SQLAlchemy async + MySQL
│   ├── dependencies.py             # FastAPI Depends
│   │
│   ├── models/                     # ORM 模型
│   │   ├── repository.py           # 仓库配置（repo_id, webhook_secret）
│   │   ├── review_session.py       # 审查会话（PR 一次 = 一个 session）
│   │   └── review_comment.py       # 审查发现（agent 名, 严重度, 代码位置）
│   │
│   ├── schemas/                    # Pydantic
│   │   ├── webhook.py              # GitHub Webhook payload
│   │   ├── review.py               # ReviewRequest / ReviewReport
│   │   └── config.py               # RepoConfig schema
│   │
│   ├── routers/                    # API 路由
│   │   ├── webhook.py              # POST /webhook/github
│   │   ├── review.py               # POST /review/trigger, GET /review/{id}/report
│   │   └── admin.py                # GET /admin/traces, POST /admin/eval/run
│   │
│   ├── services/                   # 业务逻辑
│   │   ├── github_service.py       # GitHub API 封装（fetch PR, diff, post comment）
│   │   ├── review_service.py       # 审查编排器（diff → chunk → agent review → aggregate）
│   │   └── context_service.py      # AST 解析 + RAG 上下文构建
│   │
│   ├── ai/                         # AI 层
│   │   ├── llm.py                  # LLM 工厂（复用 + 扩展多模型路由）
│   │   ├── model_router.py         # 模型路由（小模型做风格，大模型做逻辑/安全）
│   │   ├── reviewers/              # 审查 Agent 群
│   │   │   ├── base.py             # BaseReviewer 抽象类
│   │   │   ├── security.py         # 安全审查 Agent（SQL 注入, XSS, 密钥泄露）
│   │   │   ├── performance.py      # 性能审查 Agent（N+1 查询, 内存泄漏, 算法复杂度）
│   │   │   ├── logic.py            # 逻辑审查 Agent（边界条件, 空值, 异常处理）
│   │   │   └── style.py            # 风格审查 Agent（命名, 注释, 设计模式）
│   │   ├── prompts/                # 审查 Prompt 模板
│   │   │   ├── security_review.py
│   │   │   ├── performance_review.py
│   │   │   ├── logic_review.py
│   │   │   └── style_review.py
│   │   └── aggregator.py           # 多 Agent 结果去重 + 合并 + 严重度排序
│   │
│   ├── context/                    # 代码上下文引擎
│   │   ├── ast_parser.py           # tree-sitter AST 解析（提取函数/类/调用链）
│   │   ├── diff_chunker.py         # Diff 智能分块（按文件/函数边界切分）
│   │   ├── rag_retriever.py        # ChromaDB 语义检索相关代码
│   │   └── embedder.py             # 代码片段向量化
│   │
│   ├── observability/              # 可观测性（从面试系统复用）
│   │   ├── langfuse_client.py      # LangFuse 客户端
│   │   ├── callbacks.py            # LangChain 回调
│   │   └── router.py               # Admin API
│   │
│   ├── harness/                    # 事件系统（从面试系统复用）
│   │   ├── hooks.py
│   │   ├── manager.py
│   │   └── plugins/
│   │
│   └── eval/                       # 评测体系
│       ├── golden_reviews.json     # 人工 Review 基准数据集
│       └── eval_runner.py          # 评测引擎（精确率/召回率）
│
├── scripts/
│   └── seed_repos.py               # 导入示例仓库
│
└── mcp/                            # MCP 沙箱（从面试系统复用）
    ├── server.py
    ├── tools.py
    └── sandbox.py
```

---

## 四、数据库设计

```sql
-- 仓库配置
CREATE TABLE repositories (
    id VARCHAR(36) PRIMARY KEY,
    owner VARCHAR(255) NOT NULL,
    repo_name VARCHAR(255) NOT NULL,
    webhook_secret VARCHAR(255) NOT NULL,
    github_token_encrypted TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    review_rules JSON NOT NULL,        -- 启用的 reviewer + 规则配置
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_owner_repo (owner, repo_name)
);

-- 审查会话（一次 PR = 一条记录）
CREATE TABLE review_sessions (
    id VARCHAR(36) PRIMARY KEY,
    repository_id VARCHAR(36) NOT NULL,
    pr_number INT NOT NULL,
    pr_title VARCHAR(512) NOT NULL,
    branch_name VARCHAR(255) NOT NULL,
    base_branch VARCHAR(255) NOT NULL,
    commit_sha VARCHAR(40) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',  -- pending / reviewing / completed / failed
    summary TEXT,
    stats JSON,                        -- {total_files, total_additions, total_deletions}
    started_at DATETIME NOT NULL,
    completed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (repository_id) REFERENCES repositories(id)
);

-- 审查发现
CREATE TABLE review_findings (
    id VARCHAR(36) PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL,
    reviewer_name VARCHAR(50) NOT NULL,   -- security / performance / logic / style
    severity VARCHAR(20) NOT NULL,        -- critical / high / medium / low / info
    file_path VARCHAR(1024) NOT NULL,
    line_start INT NOT NULL,
    line_end INT,
    title VARCHAR(512) NOT NULL,
    description TEXT NOT NULL,
    suggestion TEXT,
    category VARCHAR(100),               -- 细分：sql_injection / n_plus_1 / null_pointer
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否经过沙箱验证
    verification_result TEXT,            -- 沙箱验证结果
    github_comment_id BIGINT,            -- GitHub PR comment ID（已发布后回填）
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES review_sessions(id) ON DELETE CASCADE
);
```

---

## 五、API 设计

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/webhook/github` | GitHub Webhook 入口（PR opened/synchronize） |
| POST | `/api/v1/review/trigger` | 手动触发审查 `{repo_id, pr_number}` |
| GET | `/api/v1/review/{id}/status` | 查询审查状态 |
| GET | `/api/v1/review/{id}/report` | 获取审查报告（按严重度分组的 findings） |
| POST | `/api/v1/review/{id}/publish` | 将审查结果发布为 GitHub PR comments |
| GET | `/api/v1/review/{id}/findings` | 获取所有发现（支持按 reviewer/severity 筛选） |

### 仓库管理

> 📖 详细操作指南见 **[REGISTER_REPO.md](REGISTER_REPO.md)** — GitHub Token 创建、仓库注册、PR 准备

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/repos` | 注册新仓库 |
| GET | `/api/v1/repos` | 列出已注册仓库 |
| DELETE | `/api/v1/repos/{id}` | 删除仓库配置 |

### 运营接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/admin/traces` | LangFuse LLM 调用链查询 |
| POST | `/api/v1/admin/eval/run` | 运行审查评测（精确率/召回率） |
| GET | `/api/v1/health` | 健康检查 |

---

## 六、核心架构设计

### 6.1 审查流水线

```
GitHub Webhook (PR opened/updated)
  │
  ▼
github_service.fetch_pr_diff()
  │
  ▼
diff_chunker.split(diff)
  │  按文件/函数边界智能切分
  │  每个 chunk 独立送给审查 Agent
  │
  ▼
context_service.build_context(chunk)
  │  AST 解析 → 提取函数签名、调用链
  │  ChromaDB 检索 → 相关代码片段
  │  Git blame → 最近修改者信息
  │
  ▼
┌─ parallel review ─────────────────────────┐
│  security_reviewer.review(chunk, context)  │
│  performance_reviewer.review(chunk, ctx)   │
│  logic_reviewer.review(chunk, context)     │
│  style_reviewer.review(chunk, context)     │
└────────────────────────────────────────────┘
  │
  ▼
aggregator.merge(findings[])
  │  去重（同一行同一问题只保留一个）
  │  按严重度排序（critical > high > medium > low）
  │  冲突裁决（两个 reviewer 有矛盾意见时 LLM 仲裁）
  │
  ▼
sandbox_verifier.verify(high_severity_findings)
  │  自动跑 test / lint 验证 LLM 发现（减少误报）
  │
  ▼
github_service.post_review_comments(findings)
```

### 6.2 代码感知 RAG

传统的 diff review 只看改动片段，缺少上下文。我们的方案：

```
输入：diff 中的一个函数改动
  │
  ├─ AST 解析 ──→ 提取改动的函数/类名、参数、返回值
  │
  ├─ 静态分析 ──→ 找到所有调用方（call sites）
  │
  ├─ ChromaDB ──→ 语义检索相似的已有代码模式
  │                向量化代码片段（用 code embedding model）
  │                检索：函数实现、设计模式引用、类似 bug fix
  │
  └─ 组装 context ──→ {
        "changed_function": "def login(...)",
        "callers": ["middleware/auth.py:45", "api/user.py:120"],
        "related_code": [similar_implementations...],
        "recent_changes": [git log -5 for this file]
      }
```

### 6.3 多 Agent 审查架构

```python
class BaseReviewer(ABC):
    """审查 Agent 基类 — 与面试系统的 BaseSkill 同架构"""

    name: str
    display_name: str
    severity_weight: float = 1.0  # 不同 reviewer 的严重度权重

    async def review(
        self, chunk: DiffChunk, context: CodeContext
    ) -> list[ReviewFinding]:
        """审查一个代码块，返回发现的问题列表"""
        ...

class SecurityReviewer(BaseReviewer):
    """安全审查 Agent — 检测 SQL 注入、XSS、密钥泄露、权限绕过"""
    name = "security"
    severity_weight = 1.5  # 安全问题加权更高
    model = "gpt-4o"       # 用最强模型

class PerformanceReviewer(BaseReviewer):
    """性能审查 Agent — 检测 N+1、内存泄漏、低效算法"""
    name = "performance"
    model = "gpt-4o"

class LogicReviewer(BaseReviewer):
    """逻辑审查 Agent — 边界条件、空值检查、异常处理"""
    name = "logic"
    model = "gpt-4o"

class StyleReviewer(BaseReviewer):
    """风格审查 Agent — 命名规范、注释质量、设计模式"""
    name = "style"
    model = "gpt-4o-mini"  # 风格检查用小模型省钱
```

### 6.4 模型路由

```
ModelRouter:
  安全/逻辑/性能审查 → GPT-4o / Claude（大模型，高准确率）
  风格审查           → GPT-4o-mini（小模型，便宜）
  结果去重/冲突仲裁   → GPT-4o（单次调用，消耗小）
  
  主模型超时/报错     → 自动 fallback 到备选模型
```

### 6.5 评测体系（精确率 vs 召回率）

从真实 PR 收集人工 review comment 作为 golden dataset：

```json
{
  "test_id": "eval_001",
  "pr_url": "https://github.com/owner/repo/pull/123",
  "diff_chunk": "...",
  "expected_findings": [
    {
      "category": "sql_injection",
      "severity": "critical",
      "line": 45,
      "reviewer": "security"
    }
  ],
  "should_not_find": []  // 不应该误报的问题
}
```

评测指标：
- **精确率** = LLM 发现中真正是问题的比例（低误报）
- **召回率** = 人工标注的问题中被 LLM 发现的比例（低漏报）
- **F1 Score** = 综合评分

### 6.6 安全验证（MCP 沙箱）

高严重度发现自动验证（减少 AI 幻觉导致的误报）：
- `security` 发现 → Docker 中跑 `bandit` / `semgrep`
- `performance` 发现 → Docker 中跑 profiler
- `logic` 发现 → Docker 中跑对应 test case

---

## 七、实现步骤

### 第 1-2 天：地基
1. 项目骨架创建 + `requirements.txt` + `.env`
2. MySQL 建表 + SQLAlchemy 模型
3. LLM 工厂复用 + 模型路由
4. FastAPI 骨架 + `/health`

### 第 3-4 天：核心审查
5. BaseReviewer 抽象类 + 4 个 Reviewer 实现
6. Diff 分块器 + AST 上下文解析
7. 审查编排器（并行 + 聚合 + 去重）

### 第 5-6 天：GitHub 集成
8. GitHub Webhook 验证 + PR 数据拉取
9. PR Inline Comment 提交
10. 仓库注册管理 API

### 第 7-8 天：代码感知 RAG
11. tree-sitter AST 解析集成
12. ChromaDB 代码片段向量化 + 检索
13. Context 组装管道

### 第 9-10 天：收尾
14. 可观测性接入（LangFuse 复用）
15. Golden Dataset 评测框架
16. 端到端测试
17. README 文档

---

## 八、环境变量

```env
# LLM
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
LLM_SMALL_MODEL=gpt-4o-mini          # 风格检查用
EMBEDDING_MODEL=text-embedding-3-small

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=code_review_bot

# Redis
REDIS_URL=redis://localhost:6379/0

# GitHub Integration
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY_PATH=./data/github-app.pem
GITHUB_WEBHOOK_SECRET=whsec-xxx

# LangFuse Observability（可选）
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# ChromaDB
CHROMA_PERSIST_DIR=./data/chroma

# Docker（MCP 沙箱）
DOCKER_HOST=unix:///var/run/docker.sock
```

---

## 九、与面试系统的协同

两个项目拿出来应聘 AI 应用开发岗：

| 面试官会问 | 面试系统证明 | Code Review Bot 证明 |
|-----------|-------------|---------------------|
| LLM 应用经验 | 对话式 AI | 代码分析 AI |
| RAG | 简历+题库检索 | 代码语义检索 |
| Agent | 单 Agent 流程决策 | 多 Agent 并行协作 |
| 评测体系 | 评分偏离度 | 精确率/召回率 |
| 外部集成 | 无 | GitHub Webhook + API |
| 安全工程 | Guardrails | 沙箱验证 + 密钥管理 |
| 工程化 | pluggy 插件 | 模型路由 + 优雅降级 |
| 可观测性 | LangFuse 追踪 | 直接复用同一套 Infrastructure |

两个项目加起来覆盖了 AI 应用开发的完整能力矩阵。
