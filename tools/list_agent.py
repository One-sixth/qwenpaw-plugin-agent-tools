# -*- coding: utf-8 -*-
"""agent_tools_list_agent — 列可用 agent（设计 §3.7，对齐官方 list_agents）。"""

import json
import logging
from pathlib import Path

try:
    from ..utils import get_current_context, make_error, make_success
except ImportError:  # 兼容 pytest 直接以项目根导入
    from utils import get_current_context, make_error, make_success

logger = logging.getLogger(__name__)


def agent_tools_list_agent():
    """查看当前所有可用 agent（id/名字/启用状态/描述）。spawn 的 template 传这里的 id 字段（非显示名）。"""
    try:
        from qwenpaw.config.utils import load_config
    except Exception as exc:  # noqa: BLE001
        return make_error("列出 agent", f"load_config 失败：{exc!r}")

    try:
        profiles = load_config().agents.profiles
    except Exception as exc:  # noqa: BLE001
        return make_error("列出 agent", f"agents.profiles 读取失败：{exc!r}")

    current_agent, _, _ = get_current_context()
    lines = []
    for agent_id, profile in profiles.items():
        name = ""
        description = ""
        agent_json = Path(profile.workspace_dir) / "agent.json"
        try:
            data = json.loads(agent_json.read_text(encoding="utf-8"))
            name = str(data.get("name") or "")
            description = str(data.get("description") or "")
        except Exception:  # noqa: BLE001
            pass  # agent.json 缺失/坏档 → 空字段
        star = " ★" if agent_id == current_agent else ""
        enabled = "" if profile.enabled else "（disabled，不可作 template）"
        lines.append(
            f"{agent_id}{star} ｜ {name or '（无名）'} ｜ "
            f"{'enabled' if profile.enabled else 'disabled'}{enabled} ｜ "
            f"{description or '（无描述）'}"
        )
    if not lines:
        return make_success("当前没有已配置的 agent。")
    header = (
        "[id ｜ name ｜ enabled ｜ description]　"
        "★=当前会话所属 agent；disabled 不可作 template；"
        "spawn 的 template 传 id 字段（非显示名）"
    )
    return make_success(header + "\n" + "\n".join(lines))
