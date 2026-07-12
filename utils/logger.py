import logging
from pathlib import Path

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DIR = Path.home() / "Library" / "Logs" / "WB-Densitometry"


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
        fh = logging.FileHandler(_LOG_DIR / "app.log")
        fh.setFormatter(logging.Formatter(_FMT))
        logger.addHandler(fh)
    except OSError:
        pass

    return logger
