# Troubleshooting

## Core Engine not running

```bash
sudo systemctl status ironshield-core
sudo systemctl restart ironshield-core
journalctl -u ironshield-core -n 50
```

## Telegram bot not responding

1. Check the bot service:
   ```bash
   sudo systemctl status ironshield-bot
   ```
2. Verify the tunnel is active — the bot connects via SOCKS5 through the tunnel:
   ```bash
   ironshield tunnel list
   ```
3. Check the bot token is correct:
   ```bash
   ironshield config show | grep -A3 telegram
   ```

## Tunnel keeps switching (flapping)

Check current routing status and recent decisions:

```bash
ironshield routing status
ironshield routing history
```

Increase the cooldown if switching too frequently:

```bash
ironshield config set routing.cooldown_minutes 15
```

## User cannot connect

```bash
ironshield user info USERNAME
ironshield logs openvpn -n 100
```

## All tunnels failed (emergency mode)

This activates Storm-DNS automatically. Check tunnel status:

```bash
ironshield tunnel list
```
