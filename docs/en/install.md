# Installation Guide

## Requirements

- Ubuntu 20.04, 22.04, or 24.04
- Minimum 512MB RAM (1GB+ recommended)
- Minimum 2GB free disk space
- Root or sudo access
- Internet connection during install

## Quick Install

Run on **both** your Iran and Foreign servers:

```bash
curl -sSL https://raw.githubusercontent.com/amirmohammaddsht-dev/ironshield/main/scripts/install.sh | bash
```

The installer will:

1. Check system requirements
2. Install Python 3.11+ and system packages
3. Create the `ironshield` system user
4. Ask whether this server is **Iran** (inbound) or **Foreign** (outbound)
5. Collect configuration (server IPs, OpenVPN ports, Telegram bot token)
6. Install selected tunnel plugins
7. Configure UFW firewall
8. Set up the Telegram bot
9. Run an initial benchmark

## Manual Steps After Install

### Add a VPN user

```bash
ironshield user add myuser --traffic 50 --days 30
```

### Check status

```bash
ironshield status
```

### Access via Telegram

Open your bot in Telegram and send `/start`.

## See Also

- [Configuration Guide](configuration.md)
- [Troubleshooting](troubleshooting.md)
