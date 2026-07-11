"""进度报告工具模块。

提供进度条、耗时统计和任务状态报告等功能。
"""

import time
from collections.abc import Iterable
from typing import TypeVar

from tqdm import tqdm

T = TypeVar("T")


def progress_iter(
    items: Iterable[T],
    desc: str = "Processing",
    total: int | None = None,
    unit: str = "item",
) -> Iterable[T]:
    """包装可迭代对象，提供 tqdm 进度条。

    Args:
        items: 可迭代对象。
        desc: 进度条描述文本。
        total: 总数（若 items 无 __len__）。
        unit: 单位名称。

    Yields:
        逐个产出 items 中的元素。
    """
    return tqdm(items, desc=desc, total=total, unit=unit, ncols=80)


class Timer:
    """简单的耗时统计器。"""

    def __init__(self) -> None:
        self.start_time: float = 0.0
        self.elapsed: float = 0.0

    def start(self) -> None:
        """开始计时。"""
        self.start_time = time.monotonic()

    def stop(self) -> float:
        """停止计时并返回耗时（秒）。"""
        self.elapsed = time.monotonic() - self.start_time
        return self.elapsed

    def __enter__(self) -> "Timer":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()


def format_duration(seconds: float) -> str:
    """将秒数格式化为可读的持续时间字符串。"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h{minutes}m{secs}s"


def report_summary(
    *,
    total_files: int,
    success_count: int,
    fail_count: int,
    total_entities: int = 0,
    elapsed: float = 0.0,
) -> str:
    """生成导入/处理摘要报告。

    Returns:
        格式化的摘要字符串。
    """
    parts = [
        f"总文件: {total_files}",
        f"成功: {success_count}",
        f"失败: {fail_count}",
    ]
    if total_entities:
        parts.append(f"实体数: {total_entities}")
    if elapsed:
        parts.append(f"耗时: {format_duration(elapsed)}")
    return " | ".join(parts)
