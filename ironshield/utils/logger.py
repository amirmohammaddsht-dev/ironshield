"""
IronShield - Centralized Logging System
Path: ironshield/utils/logger.py
Purpose: Structured JSON logging for all system components
"""

import logging
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


LOG_DIR = Path("/opt/ironshield/logs")
LOG_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured log output."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """Colored formatter for terminal output."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]
        timestamp = datetime.now().strftime("%H:%M:%S")
        return (
            f"{color}[{timestamp}] [{record.levelname:8s}] "
            f"[{record.name}] {record.getMessage()}{reset}"
        )


def get_logger(
    name: str,
    level: str = "info",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    Create and return a configured logger instance.

    Args:
        name: Logger name (usually __name__ of the module)
        level: Log level (debug/info/warning/error/critical)
        log_file: Optional log file name

    Returns:
        logging.Logger: Configured logger
    """
    logger = logging.getLogger(f"ironshield.{name}")

    if logger.handlers:
        return logger

    log_level = LOG_LEVEL_MAP.get(level.lower(), logging.INFO)
    logger.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(ColoredFormatter())
    logger.addHandler(console_handler)

    # File handler (if log directory exists)
    if LOG_DIR.exists():
        file_name = log_file or f"{name}.log"
        file_path = LOG_DIR / file_name
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(JSONFormatter())
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def get_system_logger() -> logging.Logger:
    """Get the main system logger."""
    return get_logger("system", log_file="system.log")


def get_service_logger(service_name: str) -> logging.Logger:
    """Get a service-specific logger."""
    return get_logger(f"service.{service_name}", log_file="services.log")


def get_benchmark_logger() -> logging.Logger:
    """Get the benchmark logger."""
    return get_logger("benchmark", log_file="benchmark.log")


def get_audit_logger() -> logging.Logger:
    """Get the audit logger for important system changes."""
    return get_logger("audit", log_file="audit.log")


def get_bot_logger() -> logging.Logger:
    """Get the Telegram bot logger."""
    return get_logger("bot", log_file="bot.log")
