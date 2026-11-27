"""
services/logging_service.py — Structured Daily Rotating Logging Engine (F7)
=============================================================================
Provides production-grade structured logging for CareerPilot AI / Autohire.ai:
  - Daily rotating log files (`logs/autohire.log` and `logs/careerpilot.log`)
  - Dedicated error log (`logs/error.log` for ERROR/CRITICAL events)
  - Timed daily rotation (`TimedRotatingFileHandler` with midnight interval)
  - Pipe-delimited structured format: `YYYY-MM-DD HH:MM:SS | LEVEL | module | msg`
  - Automatic directory creation (`logs/`)
  - Redaction of sensitive secrets (API keys, Gmail app passwords, tokens)
  - Colorized console output with graceful plain-text fallback
  - Strict prevention of duplicate log handlers across module reloads
"""

import os
import sys
import re
import logging
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import config as cfg

# Module guard to prevent duplicate handler attachment across reloads
_LOGGING_INITIALIZED = False
_DEFAULT_LOG_LEVEL = logging.INFO


class SensitiveDataFilter(logging.Filter):
    """
    Scrubs passwords, API keys, tokens, and credentials from log messages
    before they are emitted to console or persistent files.
    """

    # Generic credential patterns
    PATTERNS = [
        # Key-value secret patterns: api_key=xyz, password="abc"
        (re.compile(r'(?i)(api[_-]?key|password|token|secret|auth|authorization)\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{8,})["\']?'), r'\1=***REDACTED***'),
        # Bearer tokens
        (re.compile(r'(?i)bearer\s+[a-zA-Z0-9_\-\.]{12,}'), 'Bearer ***REDACTED***'),
        # Gemini API keys (AIzaSy...)
        (re.compile(r'AIzaSy[A-Za-z0-9_\-]{33}'), '***REDACTED***'),
        # Gmail 16-character app passwords (e.g. abcd efgh ijkl mnop or abcdefghijklmnop)
        (re.compile(r'\b[a-z]{4}\s+[a-z]{4}\s+[a-z]{4}\s+[a-z]{4}\b'), '***REDACTED***'),
    ]

    def __init__(self, name: str = ""):
        super().__init__(name)
        # Build exact string replacement set from loaded config secrets
        self.exact_secrets = set()
        for attr in [
            "GEMINI_API_KEY", "GMAIL_APP_PASSWORD", "APOLLO_API_KEY",
            "SNOV_API_SECRET", "TAVILY_API_KEY", "DASHBOARD_SECRET_KEY", "DASHBOARD_PASSWORD"
        ]:
            val = getattr(cfg, attr, "")
            if val and len(str(val).strip()) >= 6:
                self.exact_secrets.add(str(val).strip())

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.msg, str):
            record.msg = str(record.msg)

        msg = record.msg

        # 1. Regex replacements
        for pattern, repl in self.PATTERNS:
            msg = pattern.sub(repl, msg)

        # 2. Configured secret replacements
        for secret in self.exact_secrets:
            if secret in msg:
                msg = msg.replace(secret, "***REDACTED***")

        record.msg = msg
        return True


def setup_logging(
    name: str = "CareerPilot",
    log_level: int = logging.INFO,
    logs_dir_path: Optional[str] = None
) -> logging.Logger:
    """
    Configure and return a structured logger with console, daily rotating file,
    and dedicated error file outputs. Safe to call multiple times without
    attaching duplicate handlers.
    """
    global _LOGGING_INITIALIZED

    logs_dir = Path(logs_dir_path) if logs_dir_path else getattr(cfg, "LOGS_DIR", Path("logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()

    # Reconfigure UTF-8 encoding on Windows console if needed
    if sys.platform == "win32":
        try:
            import io
            if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.encoding.lower() != "utf-8":
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if isinstance(sys.stderr, io.TextIOWrapper) and sys.stderr.encoding.lower() != "utf-8":
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not _LOGGING_INITIALIZED:
        root_logger.setLevel(log_level)

        # Remove any pre-existing handlers on root to guarantee no duplicates
        for existing_h in list(root_logger.handlers):
            root_logger.removeHandler(existing_h)

        secret_filter = SensitiveDataFilter()

        # 1. Structured Pipe Formatter for File Outputs:
        # Format: 2026-11-27 09:00:00 | INFO    | module_name | Message
        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 2. Console Handler (Colorized or Plain)
        try:
            import colorlog
            console_formatter = colorlog.ColoredFormatter(
                fmt="%(log_color)s%(asctime)s | %(levelname)-7s | %(name)s | %(message)s%(reset)s",
                datefmt="%Y-%m-%d %H:%M:%S",
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "red,bg_white",
                }
            )
        except ImportError:
            console_formatter = file_formatter

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(log_level)
        console_handler.addFilter(secret_filter)
        root_logger.addHandler(console_handler)

        # 3. Primary Daily Rotating Log Handler (`logs/autohire.log`)
        # Rotates at midnight daily; keeps 30 backup files with suffix .YYYY-MM-DD
        autohire_log = logs_dir / "autohire.log"
        daily_autohire_handler = TimedRotatingFileHandler(
            filename=str(autohire_log),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8"
        )
        daily_autohire_handler.suffix = "%Y-%m-%d"
        daily_autohire_handler.setFormatter(file_formatter)
        daily_autohire_handler.setLevel(log_level)
        daily_autohire_handler.addFilter(secret_filter)
        root_logger.addHandler(daily_autohire_handler)

        # 4. Secondary Rotating Log Handler (`logs/careerpilot.log`)
        # Maintains compatibility with legacy modules and tests
        careerpilot_log = logs_dir / "careerpilot.log"
        careerpilot_handler = RotatingFileHandler(
            filename=str(careerpilot_log),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        careerpilot_handler.setFormatter(file_formatter)
        careerpilot_handler.setLevel(log_level)
        careerpilot_handler.addFilter(secret_filter)
        root_logger.addHandler(careerpilot_handler)

        # 5. Dedicated Error Log Handler (`logs/error.log`) — ERROR/CRITICAL only
        error_log = logs_dir / "error.log"
        error_handler = RotatingFileHandler(
            filename=str(error_log),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        error_handler.setFormatter(file_formatter)
        error_handler.setLevel(logging.ERROR)
        error_handler.addFilter(secret_filter)
        root_logger.addHandler(error_handler)

        # 6. Third-party Logger Noise Suppression
        for noisy_lib in ["werkzeug", "httpx", "httpcore", "urllib3", "playwright", "asyncio"]:
            logging.getLogger(noisy_lib).setLevel(logging.WARNING)

        _LOGGING_INITIALIZED = True

    return logging.getLogger(name)


class LoggingService:
    """
    Service wrapper for accessing structured logging and log inspection.
    """

    def __init__(self, logs_dir: Optional[Path] = None):
        self.logs_dir = logs_dir or getattr(cfg, "LOGS_DIR", Path("logs"))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        setup_logging()

    def get_logger(self, name: str = "CareerPilot") -> logging.Logger:
        return logging.getLogger(name)

    def get_log_files(self) -> List[Dict[str, Any]]:
        """List available log files and metadata in the logs directory."""
        files = []
        if not self.logs_dir.exists():
            return files

        for p in sorted(self.logs_dir.glob("*.log*")):
            if p.is_file():
                files.append({
                    "name": p.name,
                    "path": str(p),
                    "size_bytes": p.stat().st_size,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat()
                })
        return files

    def read_recent_lines(self, log_name: str = "autohire.log", lines: int = 50) -> List[str]:
        """Read the last N lines from a specified log file."""
        target = self.logs_dir / log_name
        if not target.exists():
            # Fallback to careerpilot.log if autohire.log not yet written
            target = self.logs_dir / "careerpilot.log"
            if not target.exists():
                return []

        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                return all_lines[-lines:]
        except Exception:
            return []
