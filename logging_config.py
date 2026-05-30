"""
Logging estruturado — compatível com pm2 logs e arquivo rotativo local.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: Path | str = "logs",
    level: str = "INFO",
    *,
    console: bool = True,
    file: bool = True,
) -> None:
    """
    Configura logging para AWS/pm2:
    - stdout: INFO+ (visível em `pm2 logs`)
    - arquivo: DEBUG+ em logs/signal_generator.log
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if console:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        stdout_handler.setFormatter(formatter)
        root.addHandler(stdout_handler)

    if file:
        file_handler = logging.FileHandler(
            log_path / "signal_generator.log",
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    for noisy in ("BinomoAPI", "urllib3", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging iniciado | nível=%s | dir=%s | pm2=stdout",
        level,
        log_path.resolve(),
    )
