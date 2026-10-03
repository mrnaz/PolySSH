import logging
from pathlib import Path


def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("polyssh")
    logger.setLevel(logging.ERROR)

    if not logger.handlers:
        handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)

    return logger
