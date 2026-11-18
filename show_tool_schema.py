# -*- coding: utf-8 -*-
"""导出 8 工具完整 LLM 可见 schema（tools_schema.json 留档）。

v0.2.1 起运行时 schema 来自 schemas.py（**唯一正式 schema 来源**）——
本脚本从 _TOOL_SCHEMAS 导出与运行时 ToolSpec 完全一致的形态（含
additionalProperties=False），覆盖写 tools_schema.json 快照。改 schema
只改 schemas.py，改完跑本脚本刷新快照；docstring 是代码注释不参与
schema 生成（作者拍板：不做双向锚定）。

用法：python show_tool_schema.py [--check]
  默认    导出覆盖 tools_schema.json
  --check 只比对不写（快照漂移时 exit 1）
"""
import json
import os
import sys

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)

from schemas import _TOOL_SCHEMAS

_ORDER = [
    "agent_tools_spawn",
    "agent_tools_list_agent",
    "agent_tools_list_session",
    "agent_tools_communicate",
    "agent_tools_wait",
    "agent_tools_notice",
    "agent_tools_context_read",
    "agent_tools_context_export",
]

_CATEGORY = {
    "agent_tools_spawn": "派生与会话列表",
    "agent_tools_list_agent": "派生与会话列表",
    "agent_tools_list_session": "派生与会话列表",
    "agent_tools_communicate": "会话交互",
    "agent_tools_wait": "会话交互",
    "agent_tools_notice": "通知",
    "agent_tools_context_read": "上下文",
    "agent_tools_context_export": "上下文",
}


def _build_snapshot():
    """schemas.py 常量 → tools_schema.json 快照形态（OpenAI function-call）。"""
    snapshot = {}
    for name in _ORDER:
        spec = _TOOL_SCHEMAS[name]
        snapshot[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["input_schema"],
            },
        }
    return snapshot


def main():
    snapshot = _build_snapshot()
    check_only = "--check" in sys.argv
    out_path = os.path.join(_root, "tools_schema.json")

    existing = None
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)

    if existing == snapshot:
        print(f"✓ tools_schema.json 与 schemas.py 一致（{len(snapshot)} 个工具）")
        return

    if check_only:
        print("✗ tools_schema.json 与 schemas.py 不一致——运行 python "
              "show_tool_schema.py 刷新快照")
        sys.exit(1)

    for name in _ORDER:
        print(f'\n{"=" * 60}')
        print(f'  {_CATEGORY[name]} — {name}')
        print(f'{"=" * 60}')
        print(json.dumps(snapshot[name], indent=2, ensure_ascii=False))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    print(f'\n已保存到 tools_schema.json（{len(snapshot)} 个工具，'
          '含 additionalProperties 完整形态）')


if __name__ == "__main__":
    main()
