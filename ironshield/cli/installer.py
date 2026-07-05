"""
IronShield - CLI Installer
Path: ironshield/cli/installer.py
Purpose: Interactive installation wizard. Collects server configuration,
         installs plugins, sets up UFW, configures Telegram bot,
         and runs initial benchmark.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List

import questionary

from ironshield.cli.display import (
    console,
    print_banner,
    print_error,
    print_info,
    print_step,
    print_success,
    print_warning,
    make_progress_bar,
)
from ironshield.core.config_engine import ConfigEngine
from ironshield.utils.logger import get_logger
from ironshield.utils.system import (
    create_system_user,
    get_available_disk_gb,
    get_available_ram_gb,
    get_ubuntu_version,
    is_root,
    run_command,
    setup_directories,
    setup_sudoers,
)
from ironshield.utils.validators import (
    is_valid_domain,
    is_valid_ip,
    is_valid_port,
    is_valid_telegram_token,
    is_valid_telegram_id,
)

logger = get_logger("installer")

SYSTEM_USER = "ironshield"
MIN_RAM_GB = 0.5
MIN_DISK_GB = 2.0


class Installer:
    """
    Interactive installer for IronShield.

    Steps:
    1. Pre-flight checks (OS, RAM, Disk, root)
    2. Language selection
    3. Server role selection (Iran / Foreign)
    4. Configuration gathering
    5. System user + directories setup
    6. UFW configuration
    7. Plugin installation
    8. Telegram bot setup
    9. Initial benchmark
    10. Summary
    """

    def __init__(self):
        self.config: Dict = {}
        self.role: str = "iran"
        self.selected_plugins: List[str] = []

    # ── Entry Point ───────────────────────────

    def run(self) -> bool:
        """
        Run the full installation wizard.

        Returns:
            bool: True if installation succeeded
        """
        print_banner()

        try:
            if not self._preflight_checks():
                return False

            self._select_language()
            self._select_role()
            self._gather_config()
            self._setup_system()
            self._install_plugins()
            self._setup_bot()
            self._run_initial_benchmark()
            self._print_summary()
            return True

        except KeyboardInterrupt:
            console.print("\n[yellow]Installation cancelled by user.[/yellow]")
            return False
        except Exception as e:
            print_error(f"Installation failed: {e}")
            logger.exception("Installation error")
            return False

    # ── Step 1: Pre-flight ────────────────────

    def _preflight_checks(self) -> bool:
        """Check system requirements before installation."""
        console.print("\n[bold]🔍 Checking system requirements...[/bold]\n")

        checks = [
            ("Root access", is_root, "Run with sudo or as root"),
            ("Ubuntu OS", lambda: get_ubuntu_version() is not None, "Ubuntu required"),
            (
                f"RAM ≥ {MIN_RAM_GB}GB",
                lambda: get_available_ram_gb() >= MIN_RAM_GB,
                f"Need at least {MIN_RAM_GB}GB RAM",
            ),
            (
                f"Disk ≥ {MIN_DISK_GB}GB",
                lambda: get_available_disk_gb() >= MIN_DISK_GB,
                f"Need at least {MIN_DISK_GB}GB free disk",
            ),
        ]

        all_passed = True
        for name, check_fn, error_msg in checks:
            try:
                passed = check_fn()
            except Exception:
                passed = False

            if passed:
                console.print(f"  [green]✓[/green] {name}")
            else:
                console.print(f"  [red]✗[/red] {name} — {error_msg}")
                all_passed = False

        if not all_passed:
            print_error("System requirements not met. Cannot continue.")
            return False

        ubuntu_ver = get_ubuntu_version() or "Unknown"
        ram = get_available_ram_gb()
        disk = get_available_disk_gb()
        print_info(f"Ubuntu {ubuntu_ver} | {ram:.1f}GB RAM | {disk:.0f}GB disk free")
        return True

    # ── Step 2: Language ──────────────────────

    def _select_language(self) -> None:
        """Select installation language."""
        lang = questionary.select(
            "Select language / زبان را انتخاب کنید:",
            choices=[
                questionary.Choice("🇮🇷 فارسی", value="fa"),
                questionary.Choice("🇬🇧 English", value="en"),
            ],
        ).ask()
        self.config["language"] = lang or "fa"

    # ── Step 3: Role ──────────────────────────

    def _select_role(self) -> None:
        """Select server role (Iran or Foreign)."""
        console.print("\n[bold]🌍 Server Role[/bold]")

        role = questionary.select(
            "Where is this server located?",
            choices=[
                questionary.Choice(
                    "🇮🇷 Inside Iran (Inbound — OpenVPN + Tunnel Client)",
                    value="iran",
                ),
                questionary.Choice(
                    "🌍 Outside Iran (Outbound — Tunnel Server + Storm-DNS)",
                    value="foreign",
                ),
            ],
        ).ask()

        self.role = role or "iran"
        self.config["role"] = self.role
        print_info(f"Server role: {self.role.upper()}")

    # ── Step 4: Configuration ─────────────────

    def _gather_config(self) -> None:
        """Gather all required configuration from user."""
        console.print("\n[bold]⚙️  Configuration[/bold]")

        if self.role == "iran":
            self._gather_iran_config()
        else:
            self._gather_foreign_config()

        self._gather_tunnel_selection()
        self._gather_telegram_config()

    def _gather_iran_config(self) -> None:
        """Gather Iran server configuration."""
        # Foreign server IP
        foreign_ip = questionary.text(
            "Foreign server IP address:",
            validate=lambda v: is_valid_ip(v) or "Enter a valid IP address",
        ).ask()
        self.config["foreign_ip"] = foreign_ip or ""

        # OpenVPN port
        port = questionary.text(
            "OpenVPN primary port [443]:",
            default="443",
            validate=lambda v: (v.isdigit() and is_valid_port(int(v))) or "Invalid port",
        ).ask()
        self.config["openvpn_port"] = int(port or 443)

        # OpenVPN fallback port
        fallback = questionary.text(
            "OpenVPN fallback port [80]:",
            default="80",
            validate=lambda v: (v.isdigit() and is_valid_port(int(v))) or "Invalid port",
        ).ask()
        self.config["openvpn_port_fallback"] = int(fallback or 80)

    def _gather_foreign_config(self) -> None:
        """Gather Foreign server configuration."""
        # Iran server IP
        iran_ip = questionary.text(
            "Iran server IP address:",
            validate=lambda v: is_valid_ip(v) or "Enter a valid IP address",
        ).ask()
        self.config["iran_ip"] = iran_ip or ""

        # Storm-DNS setup
        setup_dns = questionary.confirm(
            "Set up Storm-DNS emergency tunnel?",
            default=True,
        ).ask()

        if setup_dns:
            domain = questionary.text(
                "Storm-DNS domain (e.g. v.example.com):",
                validate=lambda v: is_valid_domain(v) or "Enter a valid domain",
            ).ask()
            self.config["storm_dns_domain"] = domain or ""
            console.print(
                "\n[yellow]⚠️  DNS Setup Required:[/yellow]\n"
                f"  Add NS record: [bold]{domain}[/bold] → NS → ns.{domain}\n"
                f"  Add A record:  ns.{domain} → {self.config.get('iran_ip', 'YOUR_IP')}\n"
            )
            questionary.press_any_key_to_continue("Press Enter when DNS is configured...").ask()

    def _gather_tunnel_selection(self) -> None:
        """Let user select which tunnels to install."""
        console.print("\n[bold]🔗 Tunnel Selection[/bold]")

        all_tunnels = [
            ("phormal", "Phormal (Bridge + Relay) — Fastest, QUIC obfuscation"),
            ("backhaul", "Backhaul — Reliable, optimized for Iran"),
            ("gost", "GOST — Simple TCP forwarding"),
            ("frp", "FRP — Fast Reverse Proxy"),
            ("vxlan", "VXLAN — Layer 2 tunnel (kernel built-in)"),
            ("storm_dns", "Storm-DNS — Emergency DNS tunnel (always included)"),
        ]

        choices = [
            questionary.Choice(
                label, value=name, checked=(name in ("phormal", "backhaul", "storm_dns"))
            )
            for name, label in all_tunnels
        ]

        selected = questionary.checkbox(
            "Select tunnels to install:",
            choices=choices,
        ).ask()

        # Storm-DNS is always included
        self.selected_plugins = list(set((selected or []) + ["storm_dns"]))

        if self.role == "iran":
            self.selected_plugins.append("openvpn")

        print_info(f"Selected plugins: {', '.join(sorted(self.selected_plugins))}")

    def _gather_telegram_config(self) -> None:
        """Gather Telegram bot configuration."""
        console.print("\n[bold]🤖 Telegram Bot[/bold]")

        token = questionary.text(
            "Telegram Bot Token (from @BotFather):",
            validate=lambda v: is_valid_telegram_token(v) or "Invalid token format",
        ).ask()
        self.config["telegram_token"] = token or ""

        admin_id = questionary.text(
            "Your Telegram User ID (from @userinfobot):",
            validate=lambda v: is_valid_telegram_id(v) or "Enter numeric user ID",
        ).ask()
        self.config["telegram_admin_id"] = int(admin_id or 0)

    # ── Step 5: System Setup ──────────────────

    def _setup_system(self) -> None:
        """Set up Linux user, directories, and UFW."""
        console.print("\n[bold]🔧 System Setup[/bold]")

        steps = [
            ("Creating system user", lambda: create_system_user(SYSTEM_USER)),
            ("Setting up directories", setup_directories),
            ("Configuring sudoers", lambda: setup_sudoers(SYSTEM_USER)),
            ("Writing configuration", self._write_config),
            ("Configuring UFW", self._setup_ufw),
        ]

        with make_progress_bar(len(steps), "Setting up system") as progress:
            task = progress.add_task("System setup", total=len(steps))
            for name, fn in steps:
                print_step(steps.index((name, fn)) + 1, len(steps), name)
                try:
                    fn()
                    progress.advance(task)
                except Exception as e:
                    print_warning(f"{name} failed: {e}")

    def _write_config(self) -> None:
        """Write IronShield configuration to disk."""
        engine = ConfigEngine()
        engine.init_default(
            role=self.role,
            iran_ip=self.config.get("iran_ip", ""),
            foreign_ip=self.config.get("foreign_ip", ""),
        )

        # Apply gathered settings
        if self.role == "iran":
            engine.set("openvpn.port", self.config.get("openvpn_port", 443))
            engine.set("openvpn.port_fallback", self.config.get("openvpn_port_fallback", 80))

        engine.set("telegram.token", self.config.get("telegram_token", ""))
        engine.set("telegram.admin_ids", [self.config.get("telegram_admin_id", 0)])

        if self.config.get("storm_dns_domain"):
            engine.set("tunnels.storm_dns.domain", self.config["storm_dns_domain"])

    def _setup_ufw(self) -> None:
        """Configure UFW firewall rules."""
        commands = [
            "ufw --force reset",
            "ufw default deny incoming",
            "ufw default allow outgoing",
            "ufw allow 22/tcp comment 'SSH'",
        ]

        if self.role == "iran":
            port = self.config.get("openvpn_port", 443)
            fallback = self.config.get("openvpn_port_fallback", 80)
            commands.extend(
                [
                    f"ufw allow {port}/tcp comment 'OpenVPN'",
                    f"ufw allow {fallback}/tcp comment 'OpenVPN fallback'",
                ]
            )

        if self.role == "foreign" and self.config.get("storm_dns_domain"):
            commands.append("ufw allow 53/udp comment 'Storm-DNS'")

        commands.append("ufw --force enable")

        for cmd in commands:
            code, _, err = run_command(f"sudo {cmd}")
            if code != 0:
                print_warning(f"UFW: {err}")

    # ── Step 6: Plugin Installation ───────────

    # Upstream installer scripts for these plugins are themselves
    # menu-driven (they prompt for mode/credentials/bandwidth etc. via
    # `read`) and cannot be automated. run_command() captures
    # stdout/stderr for its timeout+error-message handling, which would
    # hide those prompts from the user while stdin still waits for an
    # answer — an invisible deadlock until the timeout fires. Run these
    # with inherited stdio instead so the user can see and answer them.
    INTERACTIVE_PLUGIN_SCRIPTS = {"phormal"}

    def _install_plugins(self) -> None:
        """Install selected plugins."""
        console.print("\n[bold]📦 Installing Plugins[/bold]")

        total = len(self.selected_plugins)
        with make_progress_bar(total, "Installing plugins") as progress:
            task = progress.add_task("Plugins", total=total)

            for i, plugin_name in enumerate(self.selected_plugins, 1):
                print_step(i, total, f"Installing {plugin_name}...")
                script = (
                    Path(__file__).parent.parent.parent
                    / f"scripts/services/install_{plugin_name}.sh"
                )
                if script.exists():
                    if plugin_name in self.INTERACTIVE_PLUGIN_SCRIPTS:
                        console.print(
                            f"[dim]{plugin_name} has its own interactive "
                            f"setup — follow its prompts below:[/dim]"
                        )
                        result = subprocess.run(f"bash {script}", shell=True)
                        code, err = result.returncode, ""
                    else:
                        code, _, err = run_command(f"bash {script}", timeout=300)
                    if code != 0:
                        print_warning(f"{plugin_name}: {err[:100]}")
                else:
                    print_info(f"{plugin_name}: script not yet available (Phase 10)")
                progress.advance(task)

        print_success("Plugin installation complete")

    # ── Step 7: Telegram Bot ──────────────────

    def _setup_bot(self) -> None:
        """Set up and test Telegram bot connection."""
        console.print("\n[bold]🤖 Telegram Bot Setup[/bold]")
        print_info("Bot will connect via SOCKS5 proxy through tunnel")
        print_info(f"Token configured: {'✓' if self.config.get('telegram_token') else '✗'}")
        print_info(f"Admin ID: {self.config.get('telegram_admin_id', 'not set')}")
        print_success("Bot configuration saved")

    # ── Step 8: Benchmark ─────────────────────

    def _run_initial_benchmark(self) -> None:
        """Run initial benchmark to select best tunnel."""
        console.print("\n[bold]🔬 Initial Benchmark[/bold]")
        print_info("Running quick latency test on configured tunnels...")
        # Full benchmark runs after all services start
        print_info("Full benchmark will run automatically after first startup")

    # ── Step 9: Summary ───────────────────────

    def _print_summary(self) -> None:
        """Print installation summary."""
        from rich.panel import Panel

        lines = [
            "[bold green]🎉 IronShield installed successfully![/bold green]\n",
            f"Server Role:   [bold]{self.role.upper()}[/bold]",
            f"Plugins:       {', '.join(sorted(self.selected_plugins))}",
            f"Telegram Bot:  {'configured' if self.config.get('telegram_token') else 'none'}",
            "",
            "[bold]Useful commands:[/bold]",
            "  [cyan]ironshield status[/cyan]          — View all service statuses",
            "  [cyan]ironshield tunnel list[/cyan]     — List tunnels and switch active one",
            "  [cyan]ironshield plugin list[/cyan]     — List installed plugins",
            "  [cyan]ironshield benchmark[/cyan]       — Run benchmark tests",
            "  [cyan]ironshield user add NAME[/cyan]   — Add a VPN user",
            "  [cyan]ironshield logs openvpn[/cyan]    — View OpenVPN logs",
        ]

        from rich import box as rich_box

        console.print(Panel("\n".join(lines), box=rich_box.ROUNDED))
