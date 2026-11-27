"""
utils/logger.py — Structured, Colored & Rotating Logging Facade for CareerPilot AI
===================================================================================
Delegates to `services/logging_service.py` while preserving backward compatibility
for existing modules and legacy tests.
"""

import logging
from services.logging_service import setup_logging, SensitiveDataFilter


def setup_logger(name: str = "CareerPilot", log_level: int = logging.INFO) -> logging.Logger:
    """Configure and return a structured logger with console, rotating, and error outputs."""
    return setup_logging(name=name, log_level=log_level)
