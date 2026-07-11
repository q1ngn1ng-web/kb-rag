"""配置管理模块。

使用 YAML 配置文件，避免硬编码 LLM URL、路径等敏感信息。
"""

from pathlib import Path
from typing import Any

import yaml

from knowledge_base.exceptions import ConfigError

# 默认配置
DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "deepseek",    # xiaomi | deepseek | ollama
        "base_url": "https://api.deepseek.com",
        "api_key": "",             # 从环境变量或 config.yaml 读取
        "model": "deepseek-chat",
        "max_tokens": 4096,
        "temperature": 0.7,
        "timeout": 300,
    },
    "embedding": {
        "provider": "dashscope",  # deepseek | dashscope | ollama
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",            # 从环境变量或 config.yaml 读取
        "model": "text-embedding-v4",
    },
    "pdf": {
        "routing": {
            "auto": True,
            "engine": None,  # pymupdf | mineru | None (自动)
        },
        "classifier": {
            "formula_density_threshold": 0.02,
            "min_citation_count": 3,
            "min_academic_keywords": 2,
        },
    },
    "mineru": {
        "api_url": "https://mineru.net/api/v4",
        "api_key": "",           # 从环境变量或 config.yaml 读取
        "timeout": 300,
    },
    "knowledge_graph": {
        "working_dir": "./kb_data",
        "chunk_strategy": "F",
        "chunk_size": 8192,
        "chunk_overlap": 512,
        "top_k": 60,
        "chunk_top_k": 30,
        "max_entity_tokens": 12000,
        "max_relation_tokens": 16000,
        "max_total_tokens": 60000,
        "query_timeout": 60,
        "bm25": {
            "enabled": True,
            "top_k": 20,
            "rrf_k": 60,
        },
    },
    "obsidian": {
        "vault_path": "",
        "output_subdir": "imports",
    },
    "logging": {
        "level": "INFO",
        "file": "",
    },
}


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """加载 YAML 配置文件，与默认配置深度合并。

    Args:
        path: 配置文件路径（默认为当前目录 config.yaml）。

    Returns:
        合并后的配置字典。

    Raises:
        ConfigError: 配置文件不存在或格式错误。
    """
    config_path = Path(path)

    # 从默认配置开始
    config = _deep_merge(_deep_copy(DEFAULT_CONFIG), {})

    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                user_config: dict[str, Any] = yaml.safe_load(f) or {}
            config = _deep_merge(config, user_config)
        except yaml.YAMLError as e:
            raise ConfigError(f"配置文件格式错误: {e}") from e
    else:
        # 没有配置文件时也 OK，使用默认配置
        pass

    # 环境变量覆盖
    _apply_env_overrides(config)

    return config


def _apply_env_overrides(config: dict[str, Any]) -> None:
    """用环境变量覆盖配置中的敏感值。"""
    import os

    if not config["llm"]["api_key"]:
        # 根据 provider 选择对应的环境变量
        provider = config["llm"].get("provider", "")
        if provider == "xiaomi":
            config["llm"]["api_key"] = os.environ.get("XIAOMI_API_KEY", "")
        else:
            config["llm"]["api_key"] = os.environ.get("DEEPSEEK_API_KEY", "")
    if not config["mineru"]["api_key"]:
        config["mineru"]["api_key"] = os.environ.get("MINERU_API_KEY", "")
    if not config["embedding"]["api_key"]:
        config["embedding"]["api_key"] = os.environ.get("DASHSCOPE_API_KEY", "")


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典。"""
    result = {}
    for key in base:
        if key in override:
            if isinstance(base[key], dict) and isinstance(override[key], dict):
                result[key] = _deep_merge(base[key], override[key])
            else:
                result[key] = override[key]
        else:
            result[key] = base[key]
    for key in override:
        if key not in base:
            result[key] = override[key]
    return result


def _deep_copy(d: dict) -> dict:
    """简单深度拷贝（仅用于小规模配置字典）。"""
    import copy

    return copy.deepcopy(d)
