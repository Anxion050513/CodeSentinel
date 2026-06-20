# AI Code Review Bot — 注册仓库指南

## 前置条件

### GitHub Token 准备

1. 打开 [GitHub Personal Access Tokens](https://github.com/settings/tokens)
2. 点击 **Generate new token → Classic**
3. 填写 Note（如 `code-review-bot`），选择过期时间
4. **勾选权限**：`repo`（及其所有子项，包括 `repo:status`、`repo_deployment`、`public_repo` 等）
5. 点击 **Generate token**
6. **立即复制 `ghp_xxx...`**，关闭页面后将无法再次查看

> **注意**：
> - Classic PAT 可以访问你名下所有仓库（包括私有仓库），同一 Token 可用于注册多个仓库
> - Fine-grained PAT 需要逐个仓库授权，不推荐在 Bot 场景使用
> - Token 有过期时间，过期后需重新生成并更新注册信息

### 仓库中需有 PR

审查 Bot 需要目标仓库中**至少有一个 Pull Request** 才能触发审查。如果仓库刚创建还没有 PR：

1. 在仓库中创建一个新分支（如 `dev`）
2. 在新分支上修改任意文件并提交
3. 回到仓库主页，点击 **Compare & pull request** → **Create pull request**

---

## 注册仓库

1. 启动服务后访问 `http://localhost:8000`
2. 点击 **📦 注册仓库** 卡片
3. 填写表单：

| 字段 | 说明 | 示例 |
|------|------|------|
| **Owner** | GitHub 用户名或组织名 | `Anxion050513` |
| **Repo Name** | 仓库名（**区分大小写**） | `Intervix` |
| **GitHub Token** | 上一步创建的 Personal Access Token | `ghp_xxxxxxxxxxxx` |
| **Webhook Secret** | 留空自动生成，或自定义 | `whsec_xxx` |

4. 点击 **✅ 注册**
5. 注册成功后可在 **📦 已注册仓库** 表格中看到

---

## 触发代码审查

1. 点击 **🚀 触发代码审查** 卡片
2. 下拉选择已注册的仓库
3. 填写 PR 号（如 `1`）
4. 点击 **🚀 开始审查**（约需 60-120 秒）
5. 审查完成后，在 **📋 审查记录** 表格中点击 📄 查看报告

---

## Webhook 自动触发（可选）

如果需要 Git Push 后自动审查，在 GitHub 仓库设置 Webhook：

1. 打开仓库 **Settings → Webhooks → Add webhook**
2. Payload URL：`http://你的服务器地址:8000/api/v1/webhook`
3. Content type：`application/json`
4. Secret：填写注册仓库时的 Webhook Secret
5. 勾选事件：**Pull requests**
6. 点击 **Add webhook**
