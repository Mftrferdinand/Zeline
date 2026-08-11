<p align="center">
  <img src="../assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <strong>Zeline Agentic AI</strong> — 由 AI 研究实验室 Zerolinear 打造。
</p>

<p align="center">
  <a href="https://zeline.zerolinear.com"><img src="https://img.shields.io/badge/DOCS-ZELINE.ZEROLINEAR.COM-38BDF8?style=for-the-badge&labelColor=334155"></a>
  <a href="https://t.me/zerolinear"><img src="https://img.shields.io/badge/TELEGRAM-0A84FF?style=for-the-badge&labelColor=334155&logo=telegram&logoColor=white"></a>
  <a href="https://zerolinear.com"><img src="https://img.shields.io/badge/BUILT%20BY-ZEROLINEAR.COM-1D4ED8?style=for-the-badge&labelColor=334155"></a>
</p>

<p align="center">
  <a href="../LICENSE"><img src="https://img.shields.io/badge/LICENSE-MIT-38BDF8?style=for-the-badge&labelColor=334155"></a>
  <a href="../README.md"><img src="https://img.shields.io/badge/LANG-EN-0A84FF?style=for-the-badge&labelColor=334155"></a>
  <a href="README.id.md"><img src="https://img.shields.io/badge/LANG-ID-1D4ED8?style=for-the-badge&labelColor=334155"></a>
  <a href="README.zh.md"><img src="https://img.shields.io/badge/LANG-中文-1E3A8A?style=for-the-badge&labelColor=334155"></a>
</p>

---

# Zeline

Zeline 是由 [Zerolinear](https://zerolinear.com) 开发的开源智能体 AI 框架。  
它是一个灵活的基础平台，可用于构建能够推理、使用工具、与外部系统交互并执行复杂的多步骤工作流的 AI 智能体。

Zeline 并不绑定于单一的模型、提供商或基础设施，而是围绕灵活性构建。你可以接入自己偏好的 AI 模型和 OpenAI 兼容端点、配置提供商、集成工具，并对框架进行扩展，以契合你希望智能体的工作方式——模型和提供商可以随时替换，无需重建整个系统，从而使智能体架构保持可移植和可适配。

你可以在本地运行它进行开发，或将其部署到自己的服务器或云端，并将其连接到你所使用的界面。我们的目标是让掌控权始终留在开发者手中：你的模型、你的工具、你的基础设施、你的数据。开源、模型无关、可扩展，以开发者为先。

Zeline —— 一个 [Zerolinear](https://zerolinear.com) 项目，由 [Mftrferdinand](https://mftrferdinand.com) 主导。

## 特性

- **智能体核心** —— 一个支持工具调用的 OpenAI 兼容智能体循环，外加交互式 CLI 和一次性查询
- **模型无关** —— 兼容 OpenAI、OpenRouter、vLLM、Ollama，以及任何 OpenAI 或 Anthropic 兼容的 API；无需重建即可切换模型或提供商
- **持久化记忆** —— 按平台身份隔离的长期记忆
- **会话持久化** —— 对话历史存储在 SQLite 中（`~/.zeline/sessions.db`），因此可在网关重启后依然保留
- **技能** —— 按需加载的可复用 Markdown 流程
- **消息网关** —— Telegram（长轮询、命令、附件）、WhatsApp（Baileys 二维码配对），以及一个带认证的本地 HTTP webhook
- **内置工具** —— 网页搜索、网页抓取、深度研究、HTTP 请求、文件读取/写入/编辑/搜索、代码执行和 shell
- **MCP 客户端** —— 连接外部 MCP 服务器（stdio 或 HTTP）并自动暴露其工具
- **范围化工具配置** —— 按使用界面控制访问权限：
  - `safe` —— 仅记忆和公开技能；消息网关的默认配置
  - `workspace` —— 在 `safe` 基础上增加所有者工作区内的文件
  - `full` —— 在 `workspace` 基础上增加 shell 访问；面向本地所有者 CLI

## 安装

**要求：** Python 3.10 或更新版本。WhatsApp 还需要 Node.js 18+ 和 npm。

### Termux

```bash
pkg install git python -y
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.sh | bash
zeline setup
```

### Linux 和 macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.sh | bash
zeline setup
```

若想改为从代码检出安装：

```bash
git clone https://github.com/Mftrferdinand/Zerolinear.git
cd Zerolinear
bash install.sh
```

你的配置存储在本地 `~/.zeline/config.json`。安装完成后可快速检查一下：

```bash
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
zeline                         Set up a gateway first, then open local chat
zeline chat -q "..."           Send one query after gateway + model setup
zeline setup                   Open the gateway picker (Telegram/WhatsApp/Webhook)
zeline model                   Detect protocol, fetch models, and choose one
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

有关报告指引，请参阅 [SECURITY.md](SECURITY.md)。

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
