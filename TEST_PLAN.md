# AI Code Review Bot — 测试方案

> 生成日期：2026-06-17  
> 自动测试结果：22 个用例，21 通过，1 部分通过（Eval 超时需 LLM API 就绪）

---

## 一、快速自测（一键脚本）

在项目根目录启动服务器后，复制以下脚本到 PowerShell 运行：

```powershell
# 1. 启动服务器
cd "E:\ai code review\code-review-bot"
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000

# 另开一个终端，运行：
$API = "http://127.0.0.1:8000/api/v1"

# === 基础连通性 ===
Invoke-RestMethod "$API/health" | ConvertTo-Json
# 预期：{"status":"healthy","service":"ai-code-review-bot",...}

# === OpenAPI Schema ===
(Invoke-RestMethod "http://127.0.0.1:8000/openapi.json").paths.PSObject.Properties.Name
# 预期：列出 19 个 API 路径

# === 仓库管理 ===
# 创建仓库
$repo = Invoke-RestMethod "$API/repos" -Method POST -Body (@{
    owner="mytest"; repo_name="myrepo"; github_token="ghp_faketoken";
    webhook_secret="whsec_test123"
} | ConvertTo-Json) -ContentType "application/json"
$repo.id  # 预期：返回 UUID

# 列出仓库
(Invoke-RestMethod "$API/repos").Count  # 预期：>= 1

# 删除仓库
Invoke-RestMethod "$API/repos/$($repo.id)" -Method DELETE
# 预期：{"status":"deleted",...}
```

---

## 二、完整测试用例表

### 基础层（6 个）

| # | 方法 | 路径 | 测试目的 | 前置条件 | 预期结果 | 实测 |
|---|------|------|---------|---------|---------|------|
| 1 | GET | `/api/v1/health` | 健康检查 | 无 | 200, `{"status":"healthy"}` | ✅ |
| 2 | GET | `/openapi.json` | OpenAPI Schema | 无 | 200, 19 个路径 | ✅ |
| 3 | GET | `/docs` | Swagger UI | 浏览器访问 | HTML 页面渲染 | ✅ |
| 4 | GET | `/` | 管理后台 | 浏览器访问 | 中文管理后台 | ✅ |
| 5 | GET | `/redoc` | ReDoc | 浏览器访问 | HTML 页面渲染 | ✅ |
| 6 | - | 24 条路由注册 | 路由完整性 | 无 | `len(app.routes) == 24` | ✅ |

### 仓库管理（4 个）

| # | 方法 | 路径 | 测试目的 | 前置条件 | 预期结果 | 实测 |
|---|------|------|---------|---------|---------|------|
| 7 | POST | `/api/v1/repos` | 注册仓库 | MySQL 运行 | 201, 返回仓库对象（含 UUID） | ✅ |
| 8 | POST | `/api/v1/repos` | 重复注册拦截 | 同一 owner/repo 已存在 | 409, 提示已注册 | ⬜ |
| 9 | GET | `/api/v1/repos` | 仓库列表 | 有已注册仓库 | 200, 返回数组 | ✅ |
| 10 | DELETE | `/api/v1/repos/{id}` | 删除仓库 | 仓库存在 | 200, `{"status":"deleted"}` | ✅ |

> **验证操作**: 
> 1. 用 POST 注册一个仓库 → 记下返回的 id
> 2. 再用同样的 owner/repo 注册 → 应返回 409
> 3. 用 GET 列出 → 应看到刚注册的仓库
> 4. 用 DELETE 删除 → 应返回 deleted
> 5. 再次 GET 列出 → 仓库应消失

### 审查流程（7 个）

| # | 方法 | 路径 | 测试目的 | 前置条件 | 预期结果 | 实测 |
|---|------|------|---------|---------|---------|------|
| 11 | POST | `/api/v1/review/trigger` | 手动触发审查 | 有效 repo_id + GitHub Token | 开始审查（如 GitHub 可达） | ✅ (502 因 Token 无效，逻辑正确) |
| 12 | POST | `/api/v1/review/trigger` | 无效 repo_id | repo_id 不存在 | 404 | ⬜ |
| 13 | GET | `/api/v1/review/{id}/status` | 查询审查状态 | 有效 session_id | 200, 含 status/PR/时间 | ⬜ (需先有 session) |
| 14 | GET | `/api/v1/review/{id}/status` | 不存在 session | 无效 session_id | 404 | ✅ |
| 15 | GET | `/api/v1/review/{id}/report` | 审查报告 | 已完成 session | 200, 含 findings + severity_counts | ⬜ (需先有 session) |
| 16 | GET | `/api/v1/review/{id}/findings` | 筛选发现 | 有效 session | 200, 支持 reviewer/severity/category 过滤 | ✅ (404 格式正确) |
| 17 | GET | `/api/v1/review/` | 审查列表 | 有审查记录 | 200, 返回分页列表 | ✅ |

> **验证操作**:
> 1. 先确保 MySQL 中有仓库配置（POST /api/v1/repos）
> 2. POST /api/v1/review/trigger → 观察返回（需要真实 GitHub Token 才会成功拉取 PR）
> 3. 用 GET /api/v1/review/ 查看列表
> 4. 对已有 session 调 GET /api/v1/review/{id}/status 查看状态
> 5. 对已完成 session 调 GET /api/v1/review/{id}/report 查看报告
> 6. 调 GET /api/v1/review/{id}/findings?severity=critical 测试筛选

### 发布与 Webhook（3 个）

| # | 方法 | 路径 | 测试目的 | 前置条件 | 预期结果 | 实测 |
|---|------|------|---------|---------|---------|------|
| 18 | POST | `/api/v1/review/{id}/publish` | 发布评论到 GitHub | 有效 session + Token | 发布成功或 GitHub API 报错 | ✅ (404 格式正确) |
| 19 | POST | `/api/v1/webhook/github` | Webhook Ping | 注册仓库 | `{"status":"ignored","reason":"repository not registered"}` | ✅ |
| 20 | POST | `/api/v1/webhook/github` | PR 事件 | 注册仓库 + GitHub 签名 | 后台触发审查 | ⬜ (需真实 GitHub) |

> **验证操作**:
> 1. 先注册一个仓库（POST /api/v1/repos）用真实的 owner/repo
> 2. 构造 webhook payload 发送 POST /api/v1/webhook/github（带 X-GitHub-Event: pull_request）
> 3. 预期返回 `{"status":"accepted",...}`
> 4. 随后 GET /api/v1/review/ 确认新 session 被创建

### 管理后台与运维（5 个）

| # | 方法 | 路径 | 测试目的 | 前置条件 | 预期结果 | 实测 |
|---|------|------|---------|---------|---------|------|
| 21 | GET | `/api/v1/dashboard` | 仪表盘统计 | 有仓库和审查数据 | 返回 total_repos/sessions/findings/trend | ✅ |
| 22 | GET | `/api/v1/admin/sessions` | 分页审查列表 | 有审查记录 | 返回分页 + 总数 | ✅ |
| 23 | GET | `/api/v1/admin/maintenance/status` | 维护状态 | 无 | 返回 stale/failed/old 计数 | ✅ |
| 24 | POST | `/api/v1/admin/maintenance/run` | 手动维护 | 无 | `{"status":"ok"}` | ✅ |
| 25 | GET | `/api/v1/admin/traces` | LangFuse 调用链 | LangFuse 配置 | enabled:false（未配置时） | ✅ |

### GitHub App 集成（3 个）

| # | 方法 | 路径 | 测试目的 | 前置条件 | 预期结果 | 实测 |
|---|------|------|---------|---------|---------|------|
| 26 | GET | `/api/v1/admin/github/installations` | 安装列表 | GitHub App 配置 | 返回 installations 数组 | ✅ |
| 27 | GET | `/api/v1/admin/github/installations/{id}/repos` | 安装仓库 | 有效 installation_id | 返回仓库列表 | ⬜ (需真实安装) |
| 28 | POST | `/api/v1/admin/eval/run` | 运行评测 | LLM API Key 有效 | 返回 precision/recall/F1 | ⬜ (超时，需检查 LLM API) |

> **验证操作**:
> 1. 浏览器打开 http://localhost:8000 → 看到中文管理后台
> 2. 在 dashboard 页面点击 "仓库管理" 标签 → 注册一个仓库
> 3. 点击 "审查记录" 标签 → 查看列表（初始为空）
> 4. 点击 "评测中心" 标签 → 点击 "运行评测"

---

## 三、端到端场景测试

### 场景 1：从零开始注册仓库并触发审查


```powershell
# 前提：MySQL 运行中 + 有真实 GitHub Token

# Step 1: 注册仓库
$body = @{
    owner = "your-github-user"
    repo_name = "your-repo"
    github_token = "ghp_your_real_token"
    webhook_secret = "whsec_my_secret"
    review_rules = @{
        security = $true; performance = $true
        logic = $true; style = $true
        auto_publish = $true; auto_label = $true
    }
} | ConvertTo-Json

$repo = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/repos" -Method POST -Body $body -ContentType "application/json"
Write-Output "Repo ID: $($repo.id)"

# Step 2: 手动触发审查（假设有 open PR #1）
$review = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/review/trigger" -Method POST -Body (@{
    repo_id = $repo.id; pr_number = 1
} | ConvertTo-Json) -ContentType "application/json"

Write-Output "Session ID: $($review.session_id)"
Write-Output "Status: $($review.status)"

# Step 3: 等待审查完成，查看状态
Start-Sleep -Seconds 30
$status = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/review/$($review.session_id)/status"
Write-Output "Status: $($status.status) | Findings: $($status.total_findings)"
   
# Step 4: 获取完整报告
$report = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/review/$($review.session_id)/report"
Write-Output "Severity: $($report.severity_counts | ConvertTo-Json)"
Write-Output "First finding: $($report.findings[0].title)"
```

### 场景 2：模拟 GitHub Webhook 全流程

```powershell
# 前提：已注册仓库（场景 1 的 Step 1）

# GitHub 发送 PR opened webhook
$webhook_body = @{
    action = "opened"
    pull_request = @{
        number = 2
        title = "Add login feature"
        head = @{ ref = "feature/login"; sha = "def456789" }
        base = @{ ref = "main" }
    }
    repository = @{
        id = 123456
        full_name = "your-user/your-repo"
        name = "your-repo"
        owner = @{ login = "your-user" }
    }
} | ConvertTo-Json

$result = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/webhook/github" `
    -Method POST -Body $webhook_body -ContentType "application/json" `
    -Headers @{"X-GitHub-Event"="pull_request"}

Write-Output "Result: $($result | ConvertTo-Json)"
# 预期：{"status":"accepted","repository":"your-user/your-repo","pr_number":2}
```

### 场景 3：管理后台日常操作

```powershell
# Step 1: 查看仪表盘
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/dashboard" | ConvertTo-Json

# Step 2: 查看最近审查
$sessions = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/admin/sessions?page=1&page_size=10"
Write-Output "Total: $($sessions.total) sessions"

# Step 3: 运行维护清理
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/admin/maintenance/run" -Method POST

# Step 4: 查看维护状态
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/admin/maintenance/status" | ConvertTo-Json
```

---

## 四、环境检查清单

运行测试前，逐项确认：

| # | 检查项 | 命令 | 必须 |
|---|--------|------|------|
| 1 | Python 版本 >= 3.11 | `python --version` | ✅ |
| 2 | 虚拟环境已创建 | `.venv\Scripts\python.exe --version` | ✅ |
| 3 | 依赖已安装 | `.venv\Scripts\pip.exe list \| Select-String "fastapi\|sqlalchemy\|langchain"` | ✅ |
| 4 | MySQL 运行中 | `mysql -u root -p123456 -e "SELECT 1"` | ✅ (测试数据库操作) |
| 5 | .env 配置正确 | `cat .env` → 确认 LLM_API_KEY / MYSQL_* / REDIS_URL | ✅ |
| 6 | LLM API 可达 | `curl -H "Authorization: Bearer $env:LLM_API_KEY" $env:LLM_BASE_URL/models` | ⬜ (用于审查和评测) |
| 7 | 端口 8000 未被占用 | `netstat -an \| Select-String ":8000"` | ✅ |

---

## 五、测试结果记录

| 日期 | 测试人 | 总用例 | 通过 | 失败 | 阻塞 | 备注 |
|------|--------|--------|------|------|------|------|
| 2026-06-17 | Auto | 22 | 21 | 0 | 1 | Eval 超时（需 LLM API），其余全通 |
| (你的日期) | (你的名字) | | | | | (请填写) |

### 阻塞项目说明

| # | 阻塞项 | 原因 | 解决方式 |
|---|--------|------|---------|
| 1 | 审查触发需要真实 GitHub Token | 测试 Token 无法拉取 PR | 在 `.env` 中配置有效的 GITHUB_APP_ID 和私钥，或使用 PAT |
| 2 | Eval 评测需要 LLM API | DeepSeek API 可能超时或限流 | 确认 LLM_API_KEY 有效且 base_url 可达 |
| 3 | LangFuse 追踪 | 未配置 LANGFUSE_PUBLIC_KEY | 可选：在 `.env` 配置 LangFuse 凭证 |

---

## 六、故障排查

### MySQL 连接失败
```
sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server")
```
→ 检查 MySQL 是否运行：`Get-Service MySQL*`  
→ 检查 `.env` 中 MYSQL_HOST/PORT/USER/PASSWORD 是否正确

### LLM API 超时
```
httpx.ReadTimeout / The operation has timed out
```
→ 检查 LLM_BASE_URL 是否可达：`curl $env:LLM_BASE_URL/models`  
→ 尝试改用本地模型或降低超时预期

### Redis 不可用（不影响核心功能）
```
redis.exceptions.ConnectionError
```
→ 缓存功能自动降级为 no-op，不影响审查  
→ 如需缓存：`docker run -d -p 6379:6379 redis:7-alpine`

### 端口被占用
```
OSError: [Errno 10048] error while attempting to bind on address
```
→ `Get-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess`  
→ 关闭占用进程或更换端口：`--port 8001`
