# -*- coding: utf-8 -*-
"""用 agentscope 的 _extract_input_schema 导出完整工具 schema。

SDK 迁移后运行时链路：plugin.py register 时以 _extract_input_schema
（parameters）+ _extract_func_description（short+long 全拼接）构造
ToolSpec——本脚本输出与运行时 LLM 可见 schema 一致（additionalProperties
=False 由 register 层补充，此处未含）。
"""
import json
import os
import sys

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
os.chdir(_root)

from agentscope.tool._utils import _extract_func_description, _extract_input_schema

from tools.communicate import agent_tools_communicate
from tools.context_export import agent_tools_context_export
from tools.context_read import agent_tools_context_read
from tools.list_agent import agent_tools_list_agent
from tools.list_session import agent_tools_list_session
from tools.notice import agent_tools_notice
from tools.spawn import agent_tools_spawn
from tools.wait import agent_tools_wait


def _extract_description(func) -> str:
    """与运行时一致：agentscope _extract_func_description（短+长全拼接）。"""
    return _extract_func_description(func.__doc__)


tools = [
    ("派生与会话列表", [agent_tools_spawn, agent_tools_list_agent, agent_tools_list_session]),
    ("会话交互", [agent_tools_communicate, agent_tools_wait]),
    ("通知", [agent_tools_notice]),
    ("上下文", [agent_tools_context_read, agent_tools_context_export]),
]

all_schemas = {}

for category, fns in tools:
    print(f'\n{"=" * 60}')
    print(f'  {category}（{len(fns)} 个工具）')
    print(f'{"=" * 60}')
    for fn in fns:
        schema = _extract_input_schema(fn)
        desc = _extract_description(fn)
        full = {
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": desc,
                "parameters": schema,
            },
        }
        all_schemas[fn.__name__] = full
        print(f'\n── {fn.__name__} ──')
        print(json.dumps(full, indent=2, ensure_ascii=False))

out_path = "tools_schema.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(all_schemas, f, indent=2, ensure_ascii=False)
print(f'\n\n已保存到 {out_path}（{len(all_schemas)} 个工具）')
