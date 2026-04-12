import logging
import os
import sys
import re
from logging.handlers import RotatingFileHandler

class SensitiveDataFilter(logging.Filter):
    """Filter to redact sensitive data from log messages."""
    def filter(self, record):
        if not isinstance(record.msg, str):
            return True
            
        # Redact keys and secrets
        record.msg = re.sub(
            r'(api_key|token|secret|password|key|client_id|client_secret)[=:]\s*["\']?([\w\.\-]+)["\']?',
            r'\1=***REDACTED***',
            record.msg,
            flags=re.IGNORECASE
        )
        
        # Redact Discord tokens specifically (MTA...)
        record.msg = re.sub(
            r'[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}',
            '***DISCORD_TOKEN_REDACTED***',
            record.msg
        )
        
        return True

def setup_logger(name: str = "immortal_bot") -> logging.Logger:
    """Set up structured logging with file and console output."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger
    
    logger.addFilter(SensitiveDataFilter())

    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "bot.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    error_handler = RotatingFileHandler(
        os.path.join(log_dir, "error.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_level = os.getenv("LOG_LEVEL", "INFO").upper()
    console_handler.setLevel(getattr(logging, console_level, logging.INFO))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

logger = setup_logger()
