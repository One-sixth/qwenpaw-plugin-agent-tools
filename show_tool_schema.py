# -*- coding: utf-8 -*-
"""用 agentscope 的 _extract_input_schema 导出完整工具 schema。

description 取自函数 docstring 中 Args: 之前的段落，
parameters 取自 _extract_input_schema（从 Args: 中提取）。

注意：QwenPaw 运行时实际使用 api.register_tool(description=...) 覆盖 description，
此处仅为离线预览，与运行时不完全一致。
"""
import json
import os
import sys

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
os.chdir(_root)

from agentscope.tool._utils import _extract_input_schema

from tools.communicate import agent_tools_communicate
from tools.context_export import agent_tools_context_export
from tools.context_read import agent_tools_context_read
from tools.list_agent import agent_tools_list_agent
from tools.list_session import agent_tools_list_session
from tools.notice import agent_tools_notice
from tools.spawn import agent_tools_spawn
from tools.wait import agent_tools_wait


def _extract_description(func) -> str:
    """从函数 docstring 提取第一段作为工具描述（离线预览用）。"""
    doc = func.__doc__
    if not doc:
        return ""
    return doc.strip().split("\n\n")[0]


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
