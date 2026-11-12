import logging
from logging.handlers import RotatingFileHandler

from config.paths import log_dir

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DIR = log_dir()
# Keep the on-disk log bounded: the app logs at DEBUG level on every analysis.
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUP_COUNT = 2


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(ch)

    # File handler — best-effort, skipped if log dir can't be created
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        # Explicit UTF-8: image paths often contain non-ASCII characters, which
        # the platform default encoding (e.g. cp1252 on Windows) cannot write.
        fh = RotatingFileHandler(
            _LOG_DIR / "app.log",
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(_FMT))
        logger.addHandler(fh)
    except OSError:
        pass

    return logger
