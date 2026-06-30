# Configuration Guide

IronShield stores its configuration in `/opt/ironshield/configs/main.yaml`.

## View Configuration

```bash
ironshield config show
```

## Update a Value

```bash
ironshield config set openvpn.port 8443
```

## Key Configuration Sections

| Section | Description |
|---------|-------------|
| `ironshield.role` | `iran` or `foreign` |
| `server.iran.ip` / `server.foreign.ip` | Peer server IP addresses |
| `openvpn.*` | OpenVPN server settings |
| `tunnels.*` | Per-tunnel plugin configuration |
| `telegram.*` | Bot token, admin IDs, proxy settings |
| `benchmark.*` | Latency targets, scoring weights, schedule |
| `routing.*` | Smart routing cooldown and thresholds |

## Backup and Rollback

Configuration changes are automatically backed up before being applied.
View history:

```bash
ironshield config show
```

Backups are stored in `/opt/ironshield/configs/backups/`.
