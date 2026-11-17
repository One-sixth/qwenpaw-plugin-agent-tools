# -*- coding: utf-8 -*-
"""pytest fixtures：单例 reset + mock 基座（process-tools conftest 范式）。"""

import asyncio
import sys
from pathlib import Path

import pytest

# 项目根入 sys.path（tools 子模块直接导入兼容）；tests/ 目录入 sys.path
# （tests/__init__.py 包模式下 helpers 等模块的顶层导入可见）
ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for p in (str(ROOT), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每测试前后清空全部进程内存态单例（防跨测试状态泄漏）。"""
    from manager import get_manager
    from notifier import reset_notifier

    get_manager().reset_manager()
    reset_notifier()
    yield
    get_manager().reset_manager()
    reset_notifier()


@pytest.fixture
def event_loop():
    """每测试独立事件循环（asyncio 测试基座）。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
