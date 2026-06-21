<div align="center">

# 🛡️ IronShield

**Automated VPN & Tunnel Management Platform**

*One command to install, configure, monitor, and manage your Iran/Foreign server infrastructure*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-20.04%20|%2022.04%20|%2024.04-orange.svg)](https://ubuntu.com)

</div>

---

## ✨ Features

- **One-command install** — `curl -sSL https://raw.githubusercontent.com/amirmohammaddsht-dev/ironshield/main/scripts/install.sh | bash`
- **Smart routing** — Auto-selects the best tunnel based on real-time benchmarks
- **Telegram bot** — Full management via Telegram (Admin + User panels)
- **Plugin system** — Easily add, update, or remove tunnel plugins
- **Auto failover** — Switches to backup tunnel when primary fails
- **Two-server support** — Iran (Inbound) + Foreign (Outbound) servers

## 🔌 Supported Plugins

| Category | Plugin |
|----------|--------|
| **VPN** | OpenVPN (TCP 443/80) |
| **Tunnel** | Phormal Bridge, Phormal Relay, GOST, FRP, Backhaul, VXLAN |
| **DNS Tunnel** | Storm-DNS (emergency fallback) |

## 🚀 Quick Install

### Iran Server
```bash
curl -sSL https://raw.githubusercontent.com/amirmohammaddsht-dev/ironshield/main/scripts/install.sh | bash
```

### Foreign Server
```bash
curl -sSL https://raw.githubusercontent.com/amirmohammaddsht-dev/ironshield/main/scripts/install.sh | bash
```

## 📋 Requirements

- Ubuntu 20.04 / 22.04 / 24.04
- Minimum 512MB RAM (1GB+ recommended)
- Minimum 2GB disk space
- Root or sudo access
- Internet connection during install

## 📖 Documentation

- [Installation Guide (EN)](docs/en/install.md)
- [راهنمای نصب (FA)](docs/fa/install.md)
- [Configuration](docs/en/configuration.md)
- [Troubleshooting](docs/en/troubleshooting.md)

## 🏗️ Architecture

```
[User] → OpenVPN (TCP 443) → [Iran Server] → [Best Tunnel] → [Foreign Server] → Internet
```

## 📜 License

MIT License — see [LICENSE](LICENSE)
