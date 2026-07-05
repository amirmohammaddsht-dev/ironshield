"""
IronShield - CLI Main Entry Point
Path: ironshield/cli/main.py
Purpose: All CLI commands using Click.
         Communicates with Core Engine via Unix Socket API.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from ironshield.cli.display import (
    benchmark_results_table,
    console,
    plugin_status_table,
    print_banner,
    print_error,
    print_info,
    print_success,
    print_warning,
    routing_status_panel,
    server_metrics_panel,
    tunnel_score_table,
    users_table,
)
from ironshield.version import __version__

SOCKET_PATH = Path("/opt/ironshield/ironshield.sock")


def _get_client():
    """Get a synchronous API client."""
    from ironshield.api.client import SyncAPIClient

    return SyncAPIClient(socket_path=SOCKET_PATH)


def _require_running():
    """Check that Core Engine is running, exit if not."""
    if not SOCKET_PATH.exists():
        print_error(
            "IronShield Core is not running.\n"
            "  Start it with: [cyan]systemctl start ironshield-core[/cyan]"
        )
        sys.exit(1)


# ── CLI Group ─────────────────────────────────


@click.group()
@click.version_option(__version__, "--version", "-v", message="IronShield %(version)s")
def cli():
    """
    🛡️  IronShield — VPN & Tunnel Management Platform

    Manage your Iran/Foreign server VPN infrastructure from the command line.
    """
    pass


# ── Install ───────────────────────────────────


@cli.command()
def install():
    """Run the interactive installation wizard."""
    from ironshield.cli.installer import Installer

    installer = Installer()
    success = installer.run()
    sys.exit(0 if success else 1)


# ── Status ────────────────────────────────────


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status(as_json: bool):
    """Show status of all plugins and tunnels."""
    _require_running()

    client = _get_client()
    try:
        data = client.get_status()
    except Exception as e:
        print_error(f"Failed to get status: {e}")
        sys.exit(1)

    if as_json:
        import json

        click.echo(json.dumps(data, indent=2))
        return

    print_banner()

    # Plugin status
    plugins = data.get("dashboard", {}).get("plugins", [])
    plugin_dict = {p["name"]: p for p in plugins} if isinstance(plugins, list) else plugins
    if plugin_dict:
        console.print(plugin_status_table(plugin_dict))
        console.print()

    # Tunnel status
    tunnels = data.get("tunnels", {}).get("tunnels", [])
    if tunnels:
        console.print(tunnel_score_table(tunnels))
        console.print()

    # Routing
    routing = data.get("routing", {})
    if routing:
        console.print(routing_status_panel(routing))

    # System metrics
    system = data.get("dashboard", {}).get("system", {})
    if system:
        console.print()
        server_role = data.get("dashboard", {}).get("server", "")
        label = "🇮🇷 Iran Server" if server_role == "iran" else "🌍 Foreign Server"
        console.print(server_metrics_panel(label, system))


# ── Plugin Management ─────────────────────────


@cli.group()
def plugin():
    """Manage IronShield plugins."""
    pass


@plugin.command("list")
def plugin_list():
    """List all loaded plugins and their status."""
    _require_running()
    client = _get_client()
    try:
        data = client.list_plugins()
        plugins = data.get("plugins", {})
        console.print(plugin_status_table(plugins))
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@plugin.command("start")
@click.argument("name")
def plugin_start(name: str):
    """Start a plugin service."""
    _require_running()
    client = _get_client()
    try:
        result = client.start_plugin(name)
        if result.get("success"):
            print_success(f"{name} started")
        else:
            print_error(result.get("error", "Failed"))
            sys.exit(1)
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@plugin.command("stop")
@click.argument("name")
def plugin_stop(name: str):
    """Stop a plugin service."""
    _require_running()
    client = _get_client()
    try:
        result = client.stop_plugin(name)
        if result.get("success"):
            print_success(f"{name} stopped")
        else:
            print_error(result.get("error", "Failed"))
            sys.exit(1)
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@plugin.command("restart")
@click.argument("name")
def plugin_restart(name: str):
    """Restart a plugin service."""
    _require_running()
    client = _get_client()
    try:
        result = client.restart_plugin(name)
        if result.get("success"):
            print_success(f"{name} restarted")
        else:
            print_error(result.get("error", "Failed"))
            sys.exit(1)
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@plugin.command("update")
@click.argument("name", required=False)
@click.option("--all", "update_all", is_flag=True, help="Update all plugins")
def plugin_update(name: str, update_all: bool):
    """Update a plugin or all plugins."""
    _require_running()
    client = _get_client()

    if update_all:
        plugins_data = client.list_plugins()
        names = list(plugins_data.get("plugins", {}).keys())
    elif name:
        names = [name]
    else:
        print_error("Specify a plugin name or use --all")
        sys.exit(1)

    for n in names:
        try:
            result = client._call(f"POST /plugins/{n}/update")
            if result.get("success"):
                print_success(f"{n} updated")
            else:
                print_warning(f"{n}: {result.get('error', 'Failed')}")
        except Exception as e:
            print_warning(f"{n}: {e}")


# ── Logs ──────────────────────────────────────


@cli.command()
@click.argument("plugin_name")
@click.option("--lines", "-n", default=50, help="Number of lines to show")
def logs(plugin_name: str, lines: int):
    """Show recent logs for a plugin."""
    _require_running()
    client = _get_client()
    try:
        data = client.get_plugin_logs(plugin_name, lines=lines)
        log_lines = data.get("lines", [])
        if not log_lines:
            print_info(f"No logs available for {plugin_name}")
            return
        for line in log_lines:
            console.print(f"[dim]{line}[/dim]")
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── Tunnel Management ─────────────────────────


@cli.group()
def tunnel():
    """Manage VPN tunnels."""
    pass


@tunnel.command("list")
def tunnel_list():
    """List all tunnels with scores."""
    _require_running()
    client = _get_client()
    try:
        data = client.get_ranked_tunnels()
        tunnels = data.get("tunnels", [])
        console.print(tunnel_score_table(tunnels))
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@tunnel.command("switch")
@click.argument("name")
def tunnel_switch(name: str):
    """Manually switch to a specific tunnel."""
    _require_running()
    client = _get_client()
    try:
        result = client.switch_tunnel(name)
        if result.get("success"):
            print_success(f"Switched to tunnel: {name}")
        else:
            print_error(result.get("message", "Failed"))
            sys.exit(1)
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@tunnel.command("auto")
def tunnel_auto():
    """Restore automatic tunnel selection."""
    _require_running()
    client = _get_client()
    try:
        client.clear_tunnel_override()
        print_success("Auto-routing restored")
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── Benchmark ─────────────────────────────────


@cli.command()
@click.option(
    "--full", is_flag=True, help="Run full benchmark (latency + loss + throughput + real delay)"
)
@click.option("--tunnel", "tunnel_name", default=None, help="Benchmark a specific tunnel")
def benchmark(full: bool, tunnel_name: str):
    """Run benchmark tests on all tunnels."""
    _require_running()
    client = _get_client()

    print_info("Running benchmark... this may take a minute")
    try:
        if tunnel_name:
            data = asyncio.run(
                __import__("ironshield.api.client", fromlist=["APIClient"])
                .APIClient(socket_path=SOCKET_PATH)
                .request(f"POST /benchmark/{tunnel_name}")
            )
            results = {tunnel_name: data} if data else {}
        else:
            data = client.run_benchmark(quick=not full)
            results = data.get("results", {})

        if results:
            console.print(benchmark_results_table(results))
        else:
            print_warning("No benchmark results available")
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── User Management ───────────────────────────


@cli.group()
def user():
    """Manage VPN users."""
    pass


@user.command("list")
def user_list():
    """List all VPN users."""
    _require_running()
    client = _get_client()
    try:
        data = client.list_users()
        users = data.get("users", [])
        if not users:
            print_info("No users found")
            return
        console.print(users_table(users))
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@user.command("add")
@click.argument("username")
@click.option(
    "--traffic", "-t", default=None, type=float, help="Traffic limit in GB (default: unlimited)"
)
@click.option("--days", "-d", default=30, type=int, help="Subscription days (default: 30)")
def user_add(username: str, traffic: float, days: int):
    """Add a new VPN user."""
    _require_running()
    client = _get_client()
    try:
        result = client.create_user(
            username=username,
            traffic_limit_gb=traffic,
            expire_days=days,
        )
        if result.get("error"):
            print_error(result["error"])
            sys.exit(1)

        print_success(f"User '{username}' created")
        print_info(f"Expires in: {days} days")
        if traffic:
            print_info(f"Traffic limit: {traffic} GB")
        else:
            print_info("Traffic limit: Unlimited")

        # Show .ovpn content hint
        if result.get("ovpn_content"):
            ovpn_path = Path(f"/opt/ironshield/configs/openvpn/clients/{username}.ovpn")
            print_info(f"Config file: {ovpn_path}")

    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@user.command("delete")
@click.argument("username")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def user_delete(username: str, yes: bool):
    """Delete a VPN user."""
    _require_running()

    if not yes:
        click.confirm(f"Delete user '{username}'?", abort=True)

    client = _get_client()
    try:
        result = client.delete_user(username)
        if result.get("success"):
            print_success(f"User '{username}' deleted")
        else:
            print_error(result.get("error", "Failed"))
            sys.exit(1)
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@user.command("info")
@click.argument("username")
def user_info(username: str):
    """Show detailed info for a VPN user."""
    _require_running()
    client = _get_client()
    try:
        data = client._call(f"GET /users/{username}")
        if data.get("error"):
            print_error(data["error"])
            sys.exit(1)

        from rich.table import Table
        from rich import box as rich_box

        table = Table(box=rich_box.SIMPLE, show_header=False)
        table.add_column("Field", style="bold cyan", width=20)
        table.add_column("Value")

        used = data.get("traffic_used_gb", 0) or 0
        limit = data.get("traffic_limit_gb")
        remaining = data.get("traffic_remaining_gb")

        table.add_row("Username", data.get("username", ""))
        table.add_row("Status", "✅ Active" if data.get("is_active") else "❌ Inactive")
        table.add_row("Traffic Used", f"{used:.2f} GB")
        table.add_row("Traffic Limit", f"{limit:.0f} GB" if limit else "Unlimited")
        table.add_row("Remaining", f"{remaining:.2f} GB" if remaining is not None else "Unlimited")
        table.add_row("Days Until Expiry", str(data.get("days_until_expiry", "∞")))
        table.add_row("Expired", "Yes" if data.get("is_expired") else "No")
        table.add_row("Over Quota", "Yes" if data.get("is_over_quota") else "No")
        table.add_row("Last Connected", data.get("last_connected_at") or "Never")
        table.add_row("Created", data.get("created_at", "")[:10])

        console.print(table)

    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@user.command("toggle")
@click.argument("username")
def user_toggle(username: str):
    """Enable or disable a VPN user."""
    _require_running()
    client = _get_client()
    try:
        result = client.toggle_user(username)
        state = "enabled" if result.get("is_active") else "disabled"
        print_success(f"User '{username}' {state}")
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@user.command("config")
@click.argument("username")
@click.option("--output", "-o", default=None, help="Output file path")
def user_config(username: str, output: str):
    """Download .ovpn config for a user."""
    _require_running()
    client = _get_client()
    try:
        data = client.get_user_config(username)
        if data.get("error"):
            print_error(data["error"])
            sys.exit(1)

        content = data.get("ovpn_content", "")
        out_path = output or f"{username}.ovpn"
        Path(out_path).write_text(content)
        print_success(f"Config saved to: {out_path}")

    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── Routing ───────────────────────────────────


@cli.group()
def routing():
    """Manage Smart Routing Engine."""
    pass


@routing.command("status")
def routing_status():
    """Show current routing status."""
    _require_running()
    client = _get_client()
    try:
        data = client.get_routing_status()
        console.print(routing_status_panel(data))
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@routing.command("history")
@click.option("--limit", "-n", default=10, help="Number of decisions to show")
def routing_history(limit: int):
    """Show recent routing decisions."""
    _require_running()
    client = _get_client()
    try:
        data = client.get_routing_history()
        decisions = data.get("decisions", [])[:limit]

        if not decisions:
            print_info("No routing decisions recorded yet")
            return

        from rich.table import Table
        from rich import box as rich_box

        table = Table(title="Routing History", box=rich_box.ROUNDED, header_style="bold cyan")
        table.add_column("Time", width=20)
        table.add_column("From", width=16)
        table.add_column("To", width=16, style="bold green")
        table.add_column("Reason", width=16)
        table.add_column("Score Δ", width=10, justify="right")

        for d in decisions:
            from_score = d.get("from_score")
            to_score = d.get("to_score")
            delta = ""
            if from_score is not None and to_score is not None:
                diff = to_score - from_score
                color = "green" if diff > 0 else "red"
                delta = f"[{color}]{diff:+.0f}[/{color}]"

            table.add_row(
                (d.get("at") or "")[:16].replace("T", " "),
                d.get("from") or "—",
                d.get("to") or "",
                d.get("reason") or "",
                delta,
            )

        console.print(table)

    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── Metrics ───────────────────────────────────


@cli.command()
def metrics():
    """Show system resource metrics."""
    _require_running()
    client = _get_client()
    try:
        data = client.get_metrics()
        system = data.get("system", {})
        console.print(server_metrics_panel("🖥️  System Metrics", system))

        # User stats
        users = data.get("users", {})
        if users:
            console.print()
            console.print(
                f"[bold]👥 Users:[/bold] "
                f"{users.get('active', 0)} active / "
                f"{users.get('total', 0)} total / "
                f"{users.get('expired', 0)} expired"
            )
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── Config ────────────────────────────────────


@cli.group()
def config():
    """Manage IronShield configuration."""
    pass


@config.command("show")
def config_show():
    """Show current configuration."""
    _require_running()
    client = _get_client()
    try:
        import json

        data = client.get_config()
        console.print_json(json.dumps(data, indent=2))
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a configuration value (dot notation)."""
    _require_running()
    client = _get_client()
    try:
        # Try to cast to appropriate type
        try:
            parsed = int(value)
        except ValueError:
            try:
                parsed = float(value)
            except ValueError:
                if value.lower() in ("true", "false"):
                    parsed = value.lower() == "true"
                else:
                    parsed = value

        result = client.update_config({key: parsed})
        if result.get("success"):
            print_success(f"Set {key} = {parsed}")
        else:
            print_error(f"Failed: {result.get('errors', 'Unknown error')}")
    except Exception as e:
        print_error(str(e))
        sys.exit(1)


# ── Health Check ──────────────────────────────


@cli.command()
def health():
    """Quick health check of the Core Engine."""
    if not SOCKET_PATH.exists():
        print_error("Core Engine is not running")
        sys.exit(1)

    client = _get_client()
    try:
        data = client.get_health()
        if data.get("status") == "ok":
            print_success(f"Core Engine is healthy (v{data.get('version', '?')})")
        else:
            print_warning("Core Engine returned unexpected status")
    except Exception as e:
        print_error(f"Health check failed: {e}")
        sys.exit(1)


# ── Entry Point ───────────────────────────────


def main():
    """CLI entry point registered in pyproject.toml."""
    cli()


if __name__ == "__main__":
    main()
