<p align="center">
  <img src="../assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <a href="https://github.com/Mftrferdinand/Zeline/tree/main/docs"><img src="https://img.shields.io/badge/Docs-zeline.zerolinear.com-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="https://t.me/zerolinear"><img src="https://img.shields.io/badge/Community-0A84FF?style=flat&labelColor=334155&logo=telegram&logoColor=white"></a>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/License-MIT-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="../README.md"><img src="https://img.shields.io/badge/Lang-EN-0A84FF?style=flat&labelColor=334155"></a>
  <a href="README.id.md"><img src="https://img.shields.io/badge/Lang-ID-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="README.zh.md"><img src="https://img.shields.io/badge/Lang-中文-0A84FF?style=flat&labelColor=334155"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-ES-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="README.ur.md"><img src="https://img.shields.io/badge/Lang-اردو-0A84FF?style=flat&labelColor=334155"></a>
  <br>
  <strong>Zeline Agentic AI</strong> — por Zerolinear, un laboratorio de investigación en IA.
</p>

---

# Zeline

Zeline es un framework open-source de IA agéntica desarrollado por **Zerolinear**. Es una base flexible para construir agentes de IA que pueden razonar, usar herramientas, interactuar con sistemas externos y ejecutar flujos de trabajo complejos de múltiples pasos.

En lugar de estar atado a un único modelo, proveedor o infraestructura, Zeline se construye con la flexibilidad como prioridad. Conecta tus modelos de IA preferidos y endpoints compatibles con OpenAI, configura proveedores, integra herramientas y extiende el framework para adaptarlo a tu forma de trabajar — los modelos y proveedores se pueden intercambiar sin reconstruir el sistema, manteniendo la arquitectura del agente portable y adaptable.

Ejecútalo localmente para desarrollo o despliega en tu propio servidor o nube, y conéctalo a las interfaces que uses. El objetivo es mantener el control en manos del desarrollador: tus modelos, tus herramientas, tu infraestructura, tus datos. Open-source, agnóstico de modelo, extensible y centrado en el desarrollador.

## Características

- **Núcleo del agente** — bucle de agente compatible con OpenAI con llamadas a herramientas, más CLI interactiva y consultas únicas
- **Agnóstico de modelo** — funciona con OpenAI, OpenRouter, vLLM, Ollama y cualquier API compatible con OpenAI o Anthropic; cambia de modelo o proveedor sin reconstruir
- **Pool de API keys** — registra varias claves por proveedor (`zeline keys add`); una clave con 401/403 se retira y una con rate-limit (429) descansa mientras las solicitudes rotan automáticamente a la siguiente clave sana
- **Memoria persistente** — memoria a largo plazo aislada por identidad de plataforma
- **Búsqueda de sesiones FTS5** — búsqueda de texto completo en todas las sesiones pasadas (conversaciones + episodios)
- **Filtro silencioso de prompt injection** — detecta instrucciones maliciosas en datos externos sin interrumpir al usuario
- **Aprobación por niveles** — operaciones rutinarias (lectura/escritura/red) se permiten automáticamente; operaciones peligrosas (instalación/destructivas) requieren aprobación
- **Skills** — procedimientos Markdown reutilizables cargados bajo demanda
- **Gateways de mensajería** — Telegram, Discord y WhatsApp
- **Herramientas integradas** — búsqueda web, deep research, lectura/escritura/edición de archivos, ejecución de código y shell
- **Control real del navegador** — la herramienta `browser` controla Chromium/Chrome vía Chrome DevTools Protocol
- **Trabajos programados** — `zeline cron` ejecuta trabajos con intervalos o expresiones cron dentro del gateway
- **Human-in-the-loop** — `ask_user` pausa para hacer una pregunta, con opciones tocables en gateways de mensajería
- **Cliente MCP** — conecta servidores MCP externos y expone sus herramientas automáticamente

## Instalación

**Requisitos:** Python 3.10+. WhatsApp también necesita Node.js 18+ y npm.

### PyPI (recomendado)

```sh
pip install zeline
# o, en un entorno aislado:
uv tool install zeline
```

Luego `zeline setup`.

### Linux, macOS, Termux

```bash
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.5/install.sh && bash install.sh
```

Luego `zeline setup`.

### Windows PowerShell

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.5/install.ps1 -OutFile install.ps1; .\install.ps1
```

Luego `zeline setup`.

## Inicio rápido

```sh
# Configuración interactiva
zeline setup

# Iniciar el gateway (Telegram/Discord)
zeline gateway

# Una pregunta rápida
zeline ask "¿qué hora es en Yakarta?"
```

## Seguridad

Zeline usa un modelo de aprobación por niveles (como los agentes líderes):
- **Lectura/Escritura/Red**: permitidas automáticamente, sin preguntas
- **Instalación/Destructivas**: requieren tu aprobación; si no respondes, se deniegan automáticamente (fail-closed)
- **Secretos**: nunca se muestran en logs ni en el chat
- **Filtro de inyección**: las instrucciones sospechosas en datos externos se detectan y se ignoran silenciosamente

Para permitirlo todo sin preguntas (más permisivo que los agentes estándar):
```json
// ~/.zeline/config.json
{"approval": {"auto_allow_all": true}}
```

## Documentación completa

La documentación completa está en inglés: [README.md](../README.md)

- [Guía de instalación](installation.md) (inglés)
- [Índice de skills](../zeline/skills/ZENITH_INDEX.md) (inglés)

## Licencia

MIT — ver [LICENSE](../LICENSE).
