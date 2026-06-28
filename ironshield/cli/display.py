"""
IronShield - CLI Display
Path: ironshield/cli/display.py
Purpose: Rich terminal output helpers for all CLI commands.
         Tables, panels, progress bars, status indicators.
"""

from __future__ import annotations

from typing import Dict, List

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

console = Console()

# Status icons
ICONS = {
    "RUNNING": "[green]●[/green]",
    "STOPPED": "[yellow]●[/yellow]",
    "FAILED": "[red]●[/red]",
    "NOT_INSTALLED": "[dim]○[/dim]",
    "INSTALLING": "[blue]◌[/blue]",
    "UNKNOWN": "[dim]?[/dim]",
    "ACTIVE": "[green]●[/green]",
    "DEGRADED": "[yellow]●[/yellow]",
    "STANDBY": "[dim]●[/dim]",
    "EMERGENCY": "[red]🆘[/red]",
}


def print_banner() -> None:
    """Print IronShield ASCII banner."""
    banner = """
[bold blue]  ___                 ____  _     _      _     _ [/bold blue]
[bold blue] |_ _|_ __ ___  _ __ / ___|| |__ (_) ___| | __| |[/bold blue]
[bold blue]  | || '__/ _ \\| '_ \\___ \\| '_ \\| |/ _ \\ |/ _` |[/bold blue]
[bold blue]  | || | | (_) | | | |___) | | | | |  __/ | (_| |[/bold blue]
[bold blue] |___|_|  \\___/|_| |_|____/|_| |_|_|\\___|_|\\__,_|[/bold blue]

[dim]Automated VPN & Tunnel Management Platform[/dim]
"""
    console.print(banner)


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[bold green]✅ {message}[/bold green]")


def print_error(message: str) -> None:
    """Print an error message."""
    console.print(f"[bold red]❌ {message}[/bold red]")


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"[bold yellow]⚠️  {message}[/bold yellow]")


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"[blue]ℹ  {message}[/blue]")


def print_step(step: int, total: int, message: str) -> None:
    """Print a numbered installation step."""
    console.print(f"[bold cyan][{step}/{total}][/bold cyan] {message}")


def plugin_status_table(plugins: Dict[str, Dict]) -> Table:
    """
    Build a Rich table for plugin status display.

    Args:
        plugins: Dict of plugin_name → status dict

    Returns:
        Rich Table object
    """
    table = Table(
        title="Plugin Status",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Status", width=8, justify="center")
    table.add_column("Plugin", style="bold")
    table.add_column("Version", width=12)
    table.add_column("Category", width=16)
    table.add_column("Priority", width=8, justify="center")

    for name, info in sorted(plugins.items(), key=lambda x: x[1].get("priority", 99)):
        status = info.get("status", "UNKNOWN")
        icon = ICONS.get(status, "?")
        table.add_row(
            icon,
            info.get("display_name", name),
            info.get("version", "N/A"),
            info.get("category", "N/A"),
            str(info.get("priority", "-")),
        )

    return table


def tunnel_score_table(tunnels: List[Dict]) -> Table:
    """
    Build a Rich table for tunnel scores and status.

    Args:
        tunnels: List of tunnel dicts from TunnelManager

    Returns:
        Rich Table object
    """
    table = Table(
        title="Tunnel Rankings",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("", width=3, justify="center")
    table.add_column("Tunnel", style="bold")
    table.add_column("Status", width=10)
    table.add_column("Score", width=8, justify="right")
    table.add_column("Latency", width=10, justify="right")
    table.add_column("Loss", width=8, justify="right")
    table.add_column("Speed", width=10, justify="right")

    for i, t in enumerate(tunnels):
        status = t.get("status", "UNKNOWN")
        icon = ICONS.get(status, "?")
        score = t.get("score")
        score_str = f"[bold green]{score:.0f}[/bold green]" if score else "[dim]N/A[/dim]"
        latency = t.get("latency_ms")
        latency_str = f"{latency:.0f}ms" if latency else "N/A"
        loss = t.get("packet_loss_percent")
        loss_str = f"{loss:.1f}%" if loss is not None else "N/A"
        speed = t.get("throughput_mbps")
        speed_str = f"{speed:.0f}M" if speed else "N/A"

        rank = "🏆" if i == 0 else ("🔄" if i == 1 else "  ")
        table.add_row(
            rank,
            t.get("name", ""),
            f"{icon} {status}",
            score_str,
            latency_str,
            loss_str,
            speed_str,
        )

    return table


def server_metrics_panel(label: str, metrics: Dict) -> Panel:
    """
    Build a Rich panel for server resource metrics.

    Args:
        label: Server label (e.g. '🇮🇷 Iran Server')
        metrics: Metrics dict from MonitoringEngine

    Returns:
        Rich Panel object
    """
    if not metrics:
        return Panel("[dim]No metrics available[/dim]", title=label)

    cpu = metrics.get("cpu_percent", 0) or 0
    ram = metrics.get("ram_percent", 0) or 0
    disk = metrics.get("disk_percent", 0) or 0
    ram_used = metrics.get("ram_used_gb", 0) or 0
    ram_total = metrics.get("ram_total_gb", 0) or 0
    disk_used = metrics.get("disk_used_gb", 0) or 0
    disk_total = metrics.get("disk_total_gb", 0) or 0

    def bar(percent: float, width: int = 20) -> str:
        filled = int(percent / 100 * width)
        color = "green" if percent < 70 else ("yellow" if percent < 85 else "red")
        return f"[{color}]{'█' * filled}[/{color}]{'░' * (width - filled)}"

    content = (
        f"CPU   {bar(cpu)} {cpu:.0f}%\n"
        f"RAM   {bar(ram)} {ram:.0f}% ({ram_used:.1f}/{ram_total:.0f} GB)\n"
        f"Disk  {bar(disk)} {disk:.0f}% ({disk_used:.0f}/{disk_total:.0f} GB)"
    )

    return Panel(content, title=f"[bold]{label}[/bold]", box=box.ROUNDED)


def users_table(users: List[Dict]) -> Table:
    """
    Build a Rich table for user listing.

    Args:
        users: List of user dicts from DB

    Returns:
        Rich Table object
    """
    table = Table(
        title=f"VPN Users ({len(users)} total)",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("Status", width=6, justify="center")
    table.add_column("Username", style="bold")
    table.add_column("Used", width=10, justify="right")
    table.add_column("Limit", width=10, justify="right")
    table.add_column("Remaining", width=12, justify="right")
    table.add_column("Expires", width=12, justify="right")
    table.add_column("Last Seen", width=16)

    for u in users:
        is_active = u.get("is_active", False)
        is_expired = u.get("is_expired", False)
        is_over = u.get("is_over_quota", False)

        if is_over:
            icon = "[red]🚫[/red]"
        elif is_expired:
            icon = "[yellow]⌛[/yellow]"
        elif is_active:
            icon = "[green]✅[/green]"
        else:
            icon = "[dim]❌[/dim]"

        used = u.get("traffic_used_gb", 0) or 0
        limit = u.get("traffic_limit_gb")
        remaining = u.get("traffic_remaining_gb")
        days = u.get("days_until_expiry")
        last_seen = u.get("last_connected_at", "Never") or "Never"

        if last_seen and last_seen != "Never":
            last_seen = last_seen[:16].replace("T", " ")

        table.add_row(
            icon,
            u.get("username", ""),
            f"{used:.1f} GB",
            f"{limit:.0f} GB" if limit else "∞",
            f"{remaining:.1f} GB" if remaining is not None else "∞",
            f"{days}d" if days is not None else "∞",
            last_seen,
        )

    return table


def benchmark_results_table(results: Dict[str, Dict]) -> Table:
    """Build a table for benchmark results."""
    table = Table(
        title="Benchmark Results",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("Plugin", style="bold")
    table.add_column("Score", width=8, justify="right")
    table.add_column("Latency", width=10, justify="right")
    table.add_column("Real Delay", width=12, justify="right")
    table.add_column("Loss", width=8, justify="right")
    table.add_column("Speed", width=10, justify="right")

    sorted_results = sorted(
        results.items(),
        key=lambda x: x[1].get("score") or 0,
        reverse=True,
    )

    for name, r in sorted_results:
        if not r.get("success"):
            table.add_row(
                name, "[red]FAILED[/red]", "-", "-", "-", f"[dim]{r.get('error', '')}[/dim]"
            )
            continue

        score = r.get("score")
        score_color = "green" if (score or 0) >= 80 else ("yellow" if (score or 0) >= 50 else "red")
        score_str = f"[{score_color}]{score:.0f}[/{score_color}]" if score else "N/A"

        table.add_row(
            name,
            score_str,
            f"{r['latency_ms']:.0f}ms" if r.get("latency_ms") else "N/A",
            f"{r['real_delay_ms']:.0f}ms" if r.get("real_delay_ms") else "N/A",
            f"{r['packet_loss_percent']:.1f}%"
            if r.get("packet_loss_percent") is not None
            else "N/A",
            f"{r['throughput_mbps']:.0f}M" if r.get("throughput_mbps") else "N/A",
        )

    return table


def make_spinner(description: str = "Working...") -> Progress:
    """Create a spinner progress indicator."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    )


def make_progress_bar(total: int, description: str = "Installing") -> Progress:
    """Create a progress bar for installation steps."""
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )


def routing_status_panel(routing: Dict) -> Panel:
    """Build a panel showing Smart Routing status."""
    mode = routing.get("mode", "auto")
    current = routing.get("current_tunnel", "none")
    backup = routing.get("backup_tunnel", "none")
    emergency = routing.get("emergency", False)

    mode_icon = "🤖" if mode == "auto" else "👤"
    emergency_line = "\n[bold red]🆘 EMERGENCY MODE ACTIVE[/bold red]" if emergency else ""

    content = (
        f"Mode:    {mode_icon} {mode.upper()}\n"
        f"Primary: [bold green]{current}[/bold green]\n"
        f"Backup:  [dim]{backup}[/dim]"
        f"{emergency_line}"
    )
    return Panel(content, title="[bold]Smart Routing[/bold]", box=box.ROUNDED)
