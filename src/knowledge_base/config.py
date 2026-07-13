"""配置管理模块。

从 config.yaml 读取配置，API Key 优先从环境变量获取。
默认值由各调用方自行处理（config.get("key", default_value)）。

配置模板见 config.yaml.example。
"""

from pathlib import Path
from typing import Any

import yaml

from knowledge_base.exceptions import ConfigError


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """加载 YAML 配置文件，环境变量覆盖敏感字段。

    Args:
        path: 配置文件路径（默认为当前目录 config.yaml）。

    Returns:
        配置字典。文件不存在时返回空字典，各调用方自行处理缺省值。
    """
    config_path = Path(path)

    if not config_path.exists():
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            config: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件格式错误: {e}") from e

    _apply_env_overrides(config)
    return config


def _apply_env_overrides(config: dict[str, Any]) -> None:
    """用环境变量覆盖配置中的敏感值。"""
    import os

    llm_cfg = config.get("llm", {})
    if not llm_cfg.get("api_key"):
        provider = llm_cfg.get("provider", "")
        if provider == "xiaomi":
            llm_cfg["api_key"] = os.environ.get("XIAOMI_API_KEY", "")
        else:
            llm_cfg["api_key"] = os.environ.get("DEEPSEEK_API_KEY", "")

    mineru_cfg = config.get("mineru", {})
    if not mineru_cfg.get("api_key"):
        mineru_cfg["api_key"] = os.environ.get("MINERU_API_KEY", "")

    embed_cfg = config.get("embedding", {})
    if not embed_cfg.get("api_key"):
        embed_cfg["api_key"] = os.environ.get("DASHSCOPE_API_KEY", "")
