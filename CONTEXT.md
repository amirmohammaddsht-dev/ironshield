# IronShield — راهنمای کامل پروژه برای AI

> این فایل برای استفاده در پرامپت‌های هوش مصنوعی طراحی شده است.
> هر بار که می‌خواهید با AI روی این پروژه کار کنید، این فایل را در context بگذارید.

---

## ۱. هدف پروژه

**IronShield** یک پلتفرم متن‌باز برای مدیریت خودکار VPN و تانل‌های رمزنگاری‌شده است.
هدف اصلی: اتصال امن و پایدار بین **سرور داخل ایران** (inbound) و **سرور خارج از ایران** (outbound) و ارائه VPN به کاربران نهایی.

### مشکلی که حل می‌کند
- فیلترینگ اینترنت در ایران باعث می‌شود ابزارهای VPN معمولی کار نکنند
- وقتی یک تانل قطع می‌شود، سیستم باید خودکار به تانل بهتر سوئیچ کند
- مدیریت کاربران VPN، ترافیک، و انقضا باید از طریق Telegram قابل انجام باشد

### معماری کلی
```
کاربر نهایی
    ↓ (OpenVPN TCP 443/80)
سرور ایران
    ↓ (Phormal/Backhaul/GOST/FRP/VXLAN/Storm-DNS)
سرور خارج
    ↓ (اینترنت آزاد)
```

---

## ۲. اطلاعات مخزن

```
GitHub:    github.com/amirmohammaddsht-dev/ironshield
Release:   v1.0.0 (منتشر شده)
نصب:       curl -sSL https://raw.githubusercontent.com/amirmohammaddsht-dev/ironshield/main/scripts/install.sh | bash
زبان:      Python 3.11+
CI:        GitHub Actions — Python 3.11 + 3.12 + Code Quality ✅
تست‌ها:    438/438 پاس
```

---

## ۳. ساختار کامل پروژه

```
ironshield/
├── .github/
│   └── workflows/
│       ├── ci.yml              ← تست + لینت (Python 3.11 + 3.12)
│       ├── security.yml        ← اسکن هفتگی (Bandit, TruffleHog, pip-audit)
│       ├── plugin_check.yml    ← اعتبارسنجی plugin‌ها + بررسی آپدیت
│       ├── release.yml         ← ساخت release روی v*.*.* tag
│       └── docs.yml            ← بررسی تطابق مستندات FA/EN
│
├── ironshield/                 ← کد اصلی Python
│   ├── version.py
│   ├── utils/
│   │   ├── logger.py           ← JSON + colored logging
│   │   ├── crypto.py           ← Fernet encryption, PBKDF2
│   │   ├── system.py           ← systemd, UFW, system user
│   │   ├── network.py          ← ping, packet loss, throughput, real delay
│   │   └── validators.py       ← اعتبارسنجی IP، پورت، توکن
│   │
│   ├── db/
│   │   ├── models.py           ← SQLAlchemy: User, Tunnel, TunnelMetric,
│   │   │                          SystemMetric, TrafficLog, FailoverEvent,
│   │   │                          AuditLog, RoutingDecision, Setting
│   │   └── database.py         ← SQLite + WAL mode + session manager
│   │
│   ├── services/
│   │   └── base.py             ← BaseService abstract class
│   │                              PluginMeta, BenchmarkResult (با scoring)
│   │                              Result, HealthResult, ServiceStatus
│   │                              ServerRole, PluginCategory
│   │
│   ├── core/
│   │   ├── plugin_manager.py   ← کشف خودکار plugin‌ها، YAML parsing
│   │   ├── plugin_updater.py   ← بررسی آپدیت از GitHub/APT/URL
│   │   ├── config_engine.py    ← YAML config + Jinja2 + backup/rollback
│   │   ├── service_manager.py  ← lifecycle مدیریت plugin‌ها + UFW
│   │   ├── tunnel_manager.py   ← DB sync، امتیازدهی، ranked list
│   │   ├── benchmark_engine.py ← async scheduled benchmarks
│   │   ├── smart_routing.py    ← انتخاب هوشمند تانل + anti-flapping
│   │   ├── health_check.py     ← بررسی موازی سلامت سرویس‌ها
│   │   ├── failover_engine.py  ← راه‌اندازی مجدد خودکار
│   │   └── monitoring.py       ← جمع‌آوری متریک، گزارش روزانه/هفتگی
│   │
│   ├── api/
│   │   ├── routes.py           ← تعریف 27 route
│   │   ├── server.py           ← Unix Socket async server (JSON-RPC)
│   │   ├── client.py           ← Async APIClient + SyncAPIClient
│   │   └── handlers.py         ← پیاده‌سازی همه handlers
│   │
│   ├── bot/
│   │   ├── locales/
│   │   │   ├── fa.json         ← رشته‌های فارسی (60+ string)
│   │   │   └── en.json         ← رشته‌های انگلیسی
│   │   ├── middlewares/
│   │   │   └── auth.py         ← I18n + AuthMiddleware + RateLimitMiddleware
│   │   ├── keyboards/
│   │   │   └── admin_kb.py     ← 15+ inline keyboard
│   │   ├── handlers/
│   │   │   ├── admin.py        ← پنل ادمین کامل
│   │   │   ├── user.py         ← پنل کاربر (اشتراک، config، QR)
│   │   │   └── alerts.py       ← سیستم هشدار
│   │   └── main.py             ← ساخت bot + SOCKS5 proxy
│   │
│   ├── cli/
│   │   ├── display.py          ← Rich tables, panels, progress bars
│   │   ├── installer.py        ← Wizard نصب تعاملی (9 مرحله)
│   │   └── main.py             ← Click CLI با 20+ دستور
│   │
│   └── agent/
│       ├── collector.py        ← جمع‌آوری متریک با psutil + caching
│       ├── api.py              ← HTTP server خام asyncio + client
│       └── main.py             ← entry point systemd
│
├── plugins/                    ← سیستم plugin-based
│   ├── vpn/
│   │   └── openvpn/
│   │       ├── plugin.yaml
│   │       ├── service.py      ← PKI، certs، traffic monitoring — کامل
│   │       ├── install.sh
│   │       ├── uninstall.sh
│   │       └── update.sh
│   └── tunnels/
│       ├── phormal/            ← کامل (Bridge + Relay + benchmark)
│       ├── gost/               ← کامل (GitHub download + Iran/Foreign config)
│       ├── frp/                ← stub (install.sh کامل)
│       ├── backhaul/           ← stub (install.sh کامل)
│       ├── vxlan/              ← stub (install.sh کامل)
│       └── storm_dns/          ← stub (install.sh کامل)
│
├── scripts/
│   ├── install.sh              ← نصب یک‌دستوری کامل
│   ├── uninstall.sh            ← حذف با --purge
│   ├── update.sh               ← آپدیت با backup خودکار
│   ├── utils/
│   │   ├── backup.sh
│   │   ├── restore.sh
│   │   └── check_deps.sh
│   └── services/
│       ├── install_openvpn.sh
│       ├── install_phormal.sh
│       ├── install_gost.sh
│       ├── install_frp.sh
│       ├── install_backhaul.sh
│       ├── install_vxlan.sh
│       └── install_storm_dns.sh
│
├── configs/
│   └── templates/
│       └── systemd/
│           ├── ironshield-core.service
│           ├── ironshield-bot.service
│           └── ironshield-agent.service
│
├── docs/
│   ├── en/  (install.md, configuration.md, troubleshooting.md)
│   └── fa/  (همان فایل‌ها — آماده برای ترجمه)
│
├── tests/
│   └── unit/
│       ├── test_utils.py
│       ├── test_database.py
│       ├── test_plugin_system.py
│       ├── test_plugins_phase4.py
│       ├── test_core_engines.py
│       ├── test_api.py
│       ├── test_bot.py
│       ├── test_cli.py
│       ├── test_agent.py
│       ├── test_scripts.py
│       └── test_workflows.py
│
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

## ۴. سرویس‌ها و plugin‌ها

| نام | نقش | سرور | اولویت | وضعیت |
|-----|-----|------|---------|--------|
| OpenVPN | VPN ورودی کاربر (TCP 443/80) | ایران | 1 | ✅ کامل |
| Phormal | تانل سریع (Bridge/Relay + QUIC obfs) | هر دو | 1 | ✅ کامل |
| Backhaul | تانل پایدار، بهینه برای ایران | هر دو | 2 | ⏳ stub |
| GOST | TCP forwarding ساده | هر دو | 3 | ✅ کامل |
| FRP | Fast Reverse Proxy | هر دو | 3 | ⏳ stub |
| VXLAN | تانل لایه ۲ (kernel built-in) | هر دو | 4 | ⏳ stub |
| Storm-DNS | تانل DNS اضطراری (priority=99) | هر دو | 99 | ⏳ stub |

> **stub** = کد Python پایه دارد، install.sh کامل است، service.py پیاده‌سازی کامل ندارد

---

## ۵. تصمیمات معماری کلیدی

### فرمول امتیازدهی تانل‌ها
```python
# وقتی real_delay_ms موجود است:
score = latency * 0.25 + real_delay * 0.30 + packet_loss * 0.30 + throughput * 0.15

# وقتی real_delay_ms = None است (وزن‌ها re-normalize می‌شوند):
total_weight = 0.25 + 0.30 + 0.15  # = 0.70
score = latency * (0.25/0.70) + packet_loss * (0.30/0.70) + throughput * (0.15/0.70)
```

### Anti-Flapping
- Cooldown: **10 دقیقه** بین هر سوئیچ
- حداقل اختلاف امتیاز: **10 امتیاز** برای توجیه سوئیچ
- Stability bonus: **+5 امتیاز** برای تانلی که بیش از 30 دقیقه پایدار بوده

### Storm-DNS
- فقط در حالت **اضطراری** فعال می‌شود (وقتی همه تانل‌های دیگر fail شدند)
- همچنین SOCKS5 proxy برای اتصال ربات Telegram فراهم می‌کند
- Port: `18000`

### Plugin Contract
هر plugin باید این فایل‌ها را داشته باشد:
```
plugins/category/name/
├── plugin.yaml     ← metadata
├── service.py      ← کلاسی که از BaseService ارث می‌برد
├── install.sh
├── uninstall.sh
└── update.sh
```

### Internal API
- **پروتکل**: Unix Domain Socket (نه شبکه)
- **فرمت**: JSON-RPC با newline delimiter
- **مسیر**: `/opt/ironshield/ironshield.sock`
- **احراز هویت**: Optional API key در header

### سرور Agent
- روی سرور **خارج** اجرا می‌شود
- فقط روی `127.0.0.1:8765` گوش می‌دهد (فقط از طریق تانل قابل دسترس است)
- احراز هویت: `X-Agent-Key` header

---

## ۶. اطلاعات فنی

### Stack
```
Python:     3.11+
Database:   SQLite با WAL mode
ORM:        SQLAlchemy
Bot:        python-telegram-bot 20.7
CLI:        Click + Rich
Web:        بدون framework (raw asyncio HTTP)
Crypto:     cryptography (Fernet + PBKDF2)
Testing:    pytest + pytest-asyncio
Lint:       black==23.12.1 + flake8==7.0.0  (pinned!)
```

### کاربر سیستم
```bash
username: ironshield
shell:    /usr/sbin/nologin
home:     /opt/ironshield
```

### مسیرهای مهم
```
/opt/ironshield/                  ← root نصب
/opt/ironshield/venv/             ← Python virtual environment
/opt/ironshield/configs/main.yaml ← تنظیمات اصلی
/opt/ironshield/configs/backups/  ← backup خودکار config
/opt/ironshield/db/ironshield.db  ← SQLite database
/opt/ironshield/logs/             ← log فایل‌ها
/opt/ironshield/keys/             ← کلیدهای رمزنگاری
/opt/ironshield/configs/openvpn/clients/ ← فایل‌های .ovpn
/opt/ironshield/ironshield.sock   ← Unix socket
/etc/sudoers.d/ironshield         ← قوانین sudo
```

### UFW
- **default**: deny incoming, allow outgoing
- **SSH**: همیشه باز (port 22)
- **OpenVPN**: 443/tcp + 80/tcp (فقط سرور ایران)
- **تانل‌ها**: فقط از IP سرور peer مجاز است
- **Storm-DNS**: 53/udp (فقط سرور خارج)

---

## ۷. Bug‌های مهم که رفع شده‌اند

### ۱. Falsy bug در `packet_loss_percent=0`
```python
# اشتباه:
if self.packet_loss_percent:  # وقتی 0 است، False می‌شود!

# درست:
if self.packet_loss_percent is not None:
```

### ۲. Re-normalization وزن‌ها
وقتی `real_delay_ms=None` است، باید وزن‌های باقیمانده به جمع 1.0 برسند، نه اینکه `real_delay` با بدترین امتیاز محاسبه شود.

### ۳. Dynamic import و mock
```python
# اشتباه — با importlib.util.spec_from_file_location کار نمی‌کند:
with patch("plugins.vpn.openvpn.service.SERVER_CONF", fake_path):

# درست:
module = load_plugin("plugins/vpn/openvpn/service.py", "key")
with patch.object(module, "SERVER_CONF", fake_path):
```

### ۴. Version pinning در CI
```
black==23.12.1   ← حتما pin شود
flake8==7.0.0    ← حتما pin شود
```

---

## ۸. دستورات CLI

```bash
# نصب
ironshield install

# وضعیت
ironshield status
ironshield health
ironshield metrics

# Plugin‌ها
ironshield plugin list
ironshield plugin start openvpn
ironshield plugin stop gost
ironshield plugin restart phormal
ironshield plugin update --all
ironshield logs openvpn -n 50

# تانل‌ها
ironshield tunnel list
ironshield tunnel switch phormal
ironshield tunnel auto

# Benchmark
ironshield benchmark
ironshield benchmark --full
ironshield benchmark --tunnel gost

# کاربران VPN
ironshield user list
ironshield user add ali --traffic 50 --days 30
ironshield user delete ali
ironshield user info ali
ironshield user toggle ali
ironshield user config ali -o ali.ovpn

# Routing
ironshield routing status
ironshield routing history

# Config
ironshield config show
ironshield config set openvpn.port 8443
```

---

## ۹. API endpoints

| Method | Path | توضیح |
|--------|------|--------|
| GET | /health | بررسی سلامت |
| GET | /status | وضعیت کامل سیستم |
| GET | /plugins | لیست plugin‌ها |
| POST | /plugins/{name}/start | شروع plugin |
| POST | /plugins/{name}/stop | توقف plugin |
| POST | /plugins/{name}/restart | راه‌اندازی مجدد |
| GET | /plugins/{name}/logs | لاگ‌ها |
| GET | /tunnels | وضعیت تانل‌ها |
| GET | /tunnels/ranked | رتبه‌بندی تانل‌ها |
| POST | /tunnels/switch/{name} | سوئیچ دستی |
| DELETE | /tunnels/override | بازگشت به حالت auto |
| POST | /benchmark/quick | benchmark سریع |
| POST | /benchmark/full | benchmark کامل |
| GET | /users | لیست کاربران |
| POST | /users | ساخت کاربر |
| DELETE | /users/{username} | حذف کاربر |
| POST | /users/{username}/toggle | فعال/غیرفعال |
| GET | /users/{username}/config | فایل .ovpn |
| GET | /metrics | متریک‌های سیستم |
| GET | /config | تنظیمات |
| PATCH | /config | به‌روزرسانی تنظیمات |
| GET | /routing | وضعیت routing |
| GET | /routing/history | تاریخچه تصمیمات |
| POST | /ping | اندازه‌گیری real delay |

---

## ۱۰. GitHub Actions Workflows

| Workflow | Trigger | هدف |
|----------|---------|-----|
| `ci.yml` | push/PR به main | تست + lint روی Python 3.11 و 3.12 |
| `security.yml` | هر دوشنبه + push | Bandit, TruffleHog, pip-audit, ShellCheck |
| `plugin_check.yml` | تغییر plugins/** + روزانه | اعتبارسنجی ساختار plugin‌ها + بررسی آپدیت |
| `release.yml` | push tag v*.*.* | ساخت GitHub Release + SHA256 |
| `docs.yml` | تغییر docs/** | بررسی تطابق FA/EN |

---

## ۱۱. مدل‌های دیتابیس

```python
User:            username, telegram_id, is_active, traffic_limit_bytes,
                 traffic_used_bytes, expire_at, last_connected_at

Tunnel:          plugin_name, status, score, latency_ms, real_delay_ms,
                 packet_loss_percent, throughput_mbps, is_primary, is_backup,
                 is_emergency, priority

TunnelMetric:    tunnel_id, latency_ms, score, resolution, recorded_at

SystemMetric:    server, cpu_percent, ram_percent, disk_percent,
                 net_bytes_sent, net_bytes_recv, resolution

TrafficLog:      user_id, bytes_sent, bytes_received, client_ip

FailoverEvent:   event_type, severity, plugin_name, action_taken,
                 resolved_at, downtime_seconds

AuditLog:        performed_by, action, resource_type, success, error_message

RoutingDecision: from_tunnel, to_tunnel, reason, from_score, to_score,
                 is_manual, is_emergency

Setting:         key, value, value_type
```

---

## ۱۲. کلاس BaseService

```python
class BaseService(ABC):
    # هر plugin باید اینها را پیاده‌سازی کند:

    @property
    def meta(self) -> PluginMeta: ...        # اطلاعات plugin

    def install(self) -> Result: ...
    def uninstall(self) -> Result: ...
    def start(self) -> Result: ...
    def stop(self) -> Result: ...
    def health_check(self) -> HealthResult: ...
    def get_config(self) -> Dict: ...
    def apply_config(self, config: Dict) -> Result: ...
    def get_logs(self, lines: int) -> List[str]: ...

    # اختیاری:
    def supports_benchmark(self) -> bool: return False
    def benchmark(self) -> BenchmarkResult: ...
    def get_metrics(self) -> Dict: ...
    def update(self) -> Result: ...
```

---

## ۱۳. وضعیت پروژه

| فاز | موضوع | وضعیت |
|-----|--------|--------|
| ۱ | Foundation (utils, logging, CI) | ✅ کامل |
| ۲ | Database (SQLAlchemy models) | ✅ کامل |
| ۳ | Plugin System (BaseService) | ✅ کامل |
| ۴ | Plugin Implementations | ✅ کامل |
| ۵ | Core Engines (8 engine) | ✅ کامل |
| ۶ | Internal API (Unix Socket) | ✅ کامل |
| ۷ | Telegram Bot (FA/EN) | ✅ کامل |
| ۸ | CLI (Click + Rich) | ✅ کامل |
| ۹ | Agent (Foreign Server) | ✅ کامل |
| ۱۰ | Bash Scripts | ✅ کامل |
| ۱۱ | GitHub Actions Release | ✅ کامل |

**Release**: v1.0.0 منتشر شده در GitHub
**تست‌ها**: 438/438 پاس
**CI**: ✅ سبز

---

## ۱۴. کارهای باقیمانده (اختیاری)

```
⏳ پیاده‌سازی کامل service.py برای FRP، Backhaul، VXLAN، Storm-DNS
   (install.sh کامل است — فقط Python logic باید نوشته شود)

⏳ ترجمه واقعی مستندات به فارسی
   (docs/fa/ در حال حاضر کپی docs/en/ است)

⏳ Alembic migration files
   (برای تغییرات schema در نسخه‌های بعدی)

⏳ تست integration واقعی روی دو سرور فیزیکی
```

---

## ۱۵. نکات مهم برای توسعه‌دهنده

### اضافه کردن یک plugin جدید
1. دایرکتوری `plugins/category/name/` بساز
2. `plugin.yaml` با فیلدهای اجباری بنویس
3. کلاسی که از `BaseService` ارث می‌برد در `service.py` بنویس
4. سه اسکریپت `install.sh`, `uninstall.sh`, `update.sh` بنویس
5. تست بنویس و CI را چک کن

### اجرای تست‌ها
```bash
cd /opt/ironshield
source venv/bin/activate
pytest tests/unit/ -v
pytest tests/unit/test_core_engines.py  # فقط یک فایل
```

### قوانین کد
- همه comments و strings انگلیسی باشند
- black و flake8 با نسخه‌های pinned اجرا شوند
- هر تابع جدید باید تست داشته باشد
- از `patch.object(module, 'attr')` برای mock استفاده شود، نه `patch('path.to.attr')`

---

*آخرین به‌روزرسانی: پس از انتشار v1.0.0*
