"""日志、错误处理和进度报告工具模块。"""

import logging
import sys
from pathlib import Path


def setup_logger(
    name: str = "knowledge_base",
    level: int = logging.INFO,
    log_file: str | None = None,
) -> logging.Logger:
    """配置并返回日志记录器。

    Args:
        name: 日志记录器名称。
        level: 日志级别。
        log_file: 可选的日志文件路径。

    Returns:
        配置完成的日志记录器。
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 标准输出 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 可选文件 handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "knowledge_base") -> logging.Logger:
    """获取已配置的日志记录器，若未配置则返回默认 logger。"""
    return logging.getLogger(name)
