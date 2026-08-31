<p align="center">
  <img src="../assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <a href="https://github.com/Mftrferdinand/Zeline/tree/main/docs"><img src="https://img.shields.io/badge/Docs-Documentation-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="https://t.me/zerolinear"><img src="https://img.shields.io/badge/Community-0A84FF?style=flat&labelColor=334155&logo=telegram&logoColor=white"></a>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/License-MIT-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="../README.md"><img src="https://img.shields.io/badge/Lang-EN-0A84FF?style=flat&labelColor=334155"></a>
  <a href="README.id.md"><img src="https://img.shields.io/badge/Lang-ID-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="README.zh.md"><img src="https://img.shields.io/badge/Lang-中文-0A84FF?style=flat&labelColor=334155"></a>
  <br>
  <strong>Zeline Agentic AI</strong> — 由 Zerolinear 打造，一个 AI 研究实验室。
</p>

---

# Zeline

Zeline 是由 **Zerolinear** 开发的开源智能体 AI 框架。它是一个灵活的基础平台，可用于构建能够推理、使用工具、与外部系统交互并执行复杂的多步骤工作流的 AI 智能体。

Zeline 并不绑定于单一的模型、提供商或基础设施，而是围绕灵活性构建。你可以接入自己偏好的 AI 模型和 OpenAI 兼容端点、配置提供商、集成工具，并对框架进行扩展，以契合你希望智能体的工作方式——模型和提供商可以随时替换，无需重建整个系统，从而使智能体架构保持可移植和可适配。

你可以在本地运行它进行开发，或将其部署到自己的服务器或云端，并将其连接到你所使用的界面。我们的目标是让掌控权始终留在开发者手中：你的模型、你的工具、你的基础设施、你的数据。开源、模型无关、可扩展，以开发者为先。

## 特性

- **智能体核心** —— 一个支持工具调用的 OpenAI 兼容智能体循环，外加交互式 CLI 和一次性查询
- **模型无关** —— 兼容 OpenAI、OpenRouter、vLLM、Ollama，以及任何 OpenAI 或 Anthropic 兼容的 API；无需重建即可切换模型或提供商
- **持久化记忆** —— 按平台身份隔离的长期记忆
- **会话持久化** —— 对话历史存储在 SQLite 中（`~/.zeline/sessions.db`），因此可在网关重启后依然保留
- **技能** —— 按需加载的可复用 Markdown 流程；完整的内置技能目录见 [Zenith 技能索引](../zeline/skills/ZENITH_INDEX.md)
- **消息网关** —— Telegram（长轮询、命令、附件）、WhatsApp（Baileys 二维码配对），以及一个带认证的本地 HTTP webhook
- **内置工具** —— 网页搜索、网页抓取、深度研究、HTTP 请求、文件读取/写入/编辑/搜索、代码执行和 shell
- **惰性工具 schema** —— 每次请求只发送少量核心 schema，其余工具仅以一行摘要列出，模型按需通过 `tool_search` 取回完整参数。在 `full` 配置下，这是每次请求 6,793 个字符而非 17,089（减少 60%），且不隐藏任何能力：工具名称始终可见，列出的工具也可直接调用。只有当节省超过额外一次往返的成本时才会启用，因此公开的 `safe` 配置仍会完整发送；运行 `zeline toolsearch` 可查看你自己工具集的实测数字
- **真实浏览器控制** —— `browser` 工具通过 Chrome DevTools Protocol 驱动已安装的 Chromium/Chrome（打开、点击、输入、读取、截图），因此可以访问由 JavaScript 渲染或需要登录的页面——这是抓取原始 HTML 做不到的。无需 Playwright 或 Puppeteer 依赖
- **网关内的定时任务** —— `zeline cron` 在运行中的网关进程内执行按间隔或 cron 表达式调度的任务，复用其配置、提供商密钥和工作区，并把结果投递到拥有该任务的会话
- **语言服务器智能** —— `code_intel` 向真实的 LSP 服务器查询诊断、定义、引用、悬停信息和符号；服务器只在 PATH 中查找，绝不下载，未安装时回退到项目的 linter
- **运维扩展点** —— Python 插件钩子可在任何工具调用执行前审计、改写或阻止它，普通 Python 文件也可作为 `custom_*` 工具加载
- **撤销** —— 写入和编辑前先做快照；`zeline undo` 可列出并恢复它们
- **人类参与回路** —— `ask_user` 会暂停运行以向你提出一个问题，在消息网关上提供可点击的选项，在 CLI 中提供键盘提示
- **写入时自动格式化** —— 智能体写入或编辑文件后，会运行项目中已安装的格式化工具（ruff、gofmt、biome、prettier、rustfmt、shfmt 等），使生成的代码符合仓库风格；可按扩展名配置，且绝不会因格式化失败而丢失写入内容
- **MCP 客户端** —— 连接外部 MCP 服务器（stdio 或 HTTP）并自动暴露其工具
- **范围化工具配置** —— 按使用界面控制访问权限：
  - `safe` —— 仅记忆和公开技能；消息网关的默认配置
  - `workspace` —— 在 `safe` 基础上增加所有者工作区内的文件
  - `full` —— 在 `workspace` 基础上增加 shell 访问；面向本地所有者 CLI

## 安装

**要求：** Python 3.10+。WhatsApp 还需要 Node.js 18+ 和 npm。在 POSIX
平台上，Zeline 使用私有 Python 环境；Windows 只为当前用户安装。无需 root
或管理员权限。

### Termux、Linux、macOS 和 iSH

```bash
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.7/install.sh && bash install.sh
```

然后运行 `zeline setup`。安装脚本会自行下载带版本号的 wheel，并在安装前对照
`SHA256SUMS` 校验，因此无需手动检查。在 iSH 上请先执行
`apk add bash curl python3`。

在 iSH 中 CLI 和 HTTP 集成可以使用，但当 iSH 不在前台时，iOS 可能会暂停消息网关。

### Windows PowerShell

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.7/install.ps1 -OutFile install.ps1; .\install.ps1
```

然后运行 `zeline setup`。

### 独立验证（可选）

安装脚本对 wheel 的校验来自与脚本自身相同的发布，因此它证明的是完整性而非来源。
若要验证来源，请用 GitHub CLI 校验该发布的构建来源证明（build provenance）——
一条独立的签名链：

```bash
gh attestation verify install.sh --repo Mftrferdinand/Zeline
```

请查看[完整安装指南](installation.md)，了解各系统依赖、从代码检出安装、
更新、PATH 修复、iOS 限制和卸载方法。

### 更新

所有平台都只需一条命令 —— Termux、Linux、macOS、iSH 和 Windows PowerShell：

```bash
zeline update
```

Zeline 会下载最新发布版安装脚本，先校验其 SHA-256 再执行，并原地完成更新。
`~/.zeline` 下的配置、会话、记忆和私有技能不会被改动。若从 git 检出运行，
则改为重新构建你的本地源码。

查看当前安装的版本以及是否有新发布：

```bash
zeline version
```

这两项在 Telegram 中同样可用，因此纯手机安装完全不需要 shell：**`/version`**
对照最新发布报告当前构建，**`/update`** 执行更新。`/update` 仅限机器人所有者，
并在一个独立进程中运行 —— 网关会先完成进行中的工作、停止、安装，然后自行重启，
进度会发回聊天。若从源码检出运行，该命令会拒绝执行，因为安装未提交的工作区
会让人意外。

安装后检查工具、集成与运行状态：

```bash
zeline tools list
zeline mcp list
zeline doctor
zeline gateway list
```

## 使用 CLI

```bash
zeline
# 或
zeline chat -q "What can you do?"
```

## 连接平台

### Telegram

使用 [@BotFather](https://t.me/BotFather) 创建一个机器人，然后运行：

```bash
zeline gateway setup telegram
zeline gateway start
```

空的允许列表会使机器人变为公开。公开网关默认始终使用 `safe` 工具配置，因此用户无法访问主机文件或 shell。

Telegram 命令：

```text
/start, /help             Show command help
/status                   Show provider and session status
/models                   Show the active model
/model <provider/model>   Persistently switch this installation's model
/new                      Clear the current chat history
/restart                  Restart the current Telegram chat session
/stop                     Stop this Zeline gateway process
/logs                     Show how to inspect gateway logs from the installation terminal
```

支持最大 256 KB 的附件，适用于文本、JSON、CSV、常见代码/配置文件，以及包含安全文本文件的 ZIP 压缩包。基于文本的 PDF 会通过 `pypdf` 提取内容。图片作为附件元数据被接受；像素分析需要具备视觉能力的提供商。

### WhatsApp

```bash
zeline gateway setup whatsapp
zeline gateway start
```

首次启动时，Zeline 会在 `~/.zeline/wa-bridge/` 下安装其 Baileys 桥接，并打印一个二维码。在 WhatsApp 中，打开**已关联的设备**，选择**关联设备**，然后扫描它。

> 此网关通过 Baileys 使用 WhatsApp 多设备功能，而非 Meta Business API。请确保你的使用符合 WhatsApp 政策。

### HTTP webhook

```bash
zeline gateway enable webhook
zeline gateway start
```

默认监听地址是 `127.0.0.1:8765`。它不会监听公网。

```bash
curl http://127.0.0.1:8765/health

curl -X POST http://127.0.0.1:8765/message \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_W...OKEN' \
  -d '{"chat_id":"demo-user","text":"Hello Zeline"}'
```

使用以下命令显示脱敏后的配置：

```bash
zeline config show
```

如果你通过隧道或反向代理暴露 webhook，请使用 HTTPS 并保持令牌认证处于启用状态。

## 命令参考

```text
zeline                         First run: gateway onboarding; later: local chat
zeline chat -q "..."           Send one query after gateway + model setup
zeline setup                   First run: gateway picker; later: setup center
zeline setup <section>         Configure gateway|model|tools|integrations|agent
zeline model                   Detect protocol, fetch models, and choose one
zeline tools list              List native tools, profiles, and enabled state
zeline tools profile <name>    Set safe|workspace|full for the local CLI
zeline tools enable|disable T  Toggle one native tool for new sessions
zeline tools workspace <path>  Set the owner workspace root
zeline doctor                  Check dependencies and configuration
zeline config path             Print the configuration location
zeline config show             Print configuration with masked secrets
zeline gateway setup [name]    Configure telegram, whatsapp, or webhook
zeline gateway enable <name>   Enable a gateway
zeline gateway disable <name>  Disable a gateway
zeline gateway list            Show configured gateways
zeline gateway token webhook   Explicitly reveal a webhook token
zeline gateway start           Run enabled gateways in the background
zeline gateway stop            Stop the background gateway process
zeline gateway status          Show background gateway status
zeline gateway log             Print gateway logs
zeline gateway run             Run enabled gateways in the foreground
zeline skills                  List installed skills
zeline memory                  Print local CLI memory
```

首次启动时，Zeline 需要从方向键选择器中选定一个网关：
Telegram、WhatsApp、Webhook 或取消。它只会配置所选的网关，
随后返回终端，并引导用户运行 `zeline model`；在网关和模型
设置都完成之前，本地聊天会保持锁定状态。

在模型设置过程中，Zeline 会检测 OpenAI 兼容或 Anthropic API，
查询提供商的模型端点，并显示一个带编号的选择器。密钥输入时
每个字符会显示为一个 `*`，而真正的 API 密钥保持隐藏。如果某个
提供商无法列出模型，Zeline 会要求提供明确的模型 ID，而不接受
占位符。

Zeline 可以通过 `runtime_info` 和内置的 `self-analysis` 技能，安全地
描述其当前使用的模型、提供商 URL、协议、工具配置以及可用工具。
API 密钥和网关令牌绝不会被包含在内。

## 安全

- 将 `~/.zeline/`、`.env`、提供商密钥和机器人令牌排除在 Git 之外。
- 网关用户默认获得 `safe` 配置。
- Webhook 需要一个密钥令牌，并默认绑定到回环地址。
- 记忆按平台身份进行命名空间隔离，例如 `telegram:123` 或 `webhook:alice`。
- WhatsApp 桥接在 Python 和 Node 之间使用一个随机的运行时令牌。
- 该仓库启用了密钥扫描、推送保护、Dependabot、CodeQL 和依赖审查。

有关报告指引，请参阅 [SECURITY.md](../SECURITY.md)。

## 开发

```bash
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --wheel-dir dist .
```

## 路线图

- PyPI 发布和签名的发布制品
- 面向 systemd 和 Termux:Boot 的服务集成
- 更多消息适配器
- 定时任务
- 插件与扩展 API
- 会话搜索和更丰富的界面

## 许可证

MIT © 2026 Mftrferdinand
