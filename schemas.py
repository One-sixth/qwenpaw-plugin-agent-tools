# -*- coding: utf-8 -*-
"""8 工具 schema 正式来源（v0.2.1 起）。

本文件是工具 description / input_schema 的**唯一正式来源**（LLM 可见），
修改频次中等——改工具 schema 通常只改这里，改完跑
`python show_tool_schema.py` 刷新 tools_schema.json 快照。

代码里的 docstring / Args 注解只是简单注释（修改频次低），**不参与
schema 生成、不要求与本文件同步**（作者拍板：不做双向锚定）。
"""

AGENT_TOOLS_SPAWN = {
    "description": '以指定 agent 为模板 spawn 持久子会话（默认后台，返回 chat_id）；完成后可用 communicate 续聊。',
    "input_schema": {
        "properties": {
            "task": {
                "description": '子会话首轮任务描述。',
                "type": 'string'
            },
            "template": {
                "default": '',
                "description": '目标 agent 的 id（agent_tools_list_agent 返回的 id，非显示名）；空=自己。',
                "type": 'string'
            },
            "background": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": True,
                "description": 'True=后台立即返回（默认）；False=前台等完成（直接返回最终正文）或超时（超时只放弃等待，不停止会话）。'
            },
            "timeout": {
                "anyOf": [
                    {
                        "type": 'integer'
                    },
                    {
                        "type": 'number'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'null'
                    }
                ],
                "default": None,
                "description": 'background=False 时的等待上限（秒），默认 600。'
            }
        },
        "required": [
            'task'
        ],
        "type": 'object',
        "additionalProperties": False
    },
}

AGENT_TOOLS_COMMUNICATE = {
    "description": '与子会话交互：send 发起新一轮（忙则排队）/ recv 读最新一轮正文 / stop 停止。\n仅读最新一轮；读历史轮用 agent_tools_context_read。',
    "input_schema": {
        "properties": {
            "chat_id": {
                "description": '子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）。',
                "type": 'string'
            },
            "action": {
                "default": 'send',
                "description": 'send=发起新一轮（text 必填，忙则排队）；recv=读最新一轮正文（最终输出+自言自语）；stop=停止并清空排队。',
                "type": 'string'
            },
            "text": {
                "default": '',
                "description": 'send 的消息文本；recv/stop 无需传。',
                "type": 'string'
            }
        },
        "required": [
            'chat_id'
        ],
        "type": 'object',
        "additionalProperties": False
    },
}

AGENT_TOOLS_WAIT = {
    "description": '前台等待子会话回合结束并返回最终正文（超时不停止目标会话）。',
    "input_schema": {
        "properties": {
            "chat_id": {
                "description": '子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）。',
                "type": 'string'
            },
            "timeout": {
                "anyOf": [
                    {
                        "type": 'integer'
                    },
                    {
                        "type": 'number'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'null'
                    }
                ],
                "default": 900,
                "description": '等待上限（秒），默认 900；超时可续等。null=用默认。'
            }
        },
        "required": [
            'chat_id'
        ],
        "type": 'object',
        "additionalProperties": False
    },
}

AGENT_TOOLS_NOTICE = {
    "description": '注册子会话完成通知（每回合 completed/failed/cancelled 均触发）。通知在你停手（回合空闲）时投递，连续调工具期间暂存、停手补发。',
    "input_schema": {
        "properties": {
            "chat_id": {
                "description": '子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）。',
                "type": 'string'
            },
            "cancel": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": 'True=取消注册并撤回待发通知。'
            }
        },
        "required": [
            'chat_id'
        ],
        "type": 'object',
        "additionalProperties": False
    },
}

AGENT_TOOLS_LIST_AGENT = {
    "description": '查看当前所有可用 agent（id/名字/启用状态/描述）。spawn 的 template 传这里的 id 字段（非显示名）。',
    "input_schema": {
        "properties": {},
        "type": 'object',
        "additionalProperties": False
    },
}

AGENT_TOOLS_LIST_SESSION = {
    "description": '列出本插件 spawn 的全部会话（状态/chat_id/最新回合/通知注册 🔔），用于找回 chat_id；人工会话不在列。',
    "input_schema": {
        "properties": {},
        "type": 'object',
        "additionalProperties": False
    },
}

AGENT_TOOLS_CONTEXT_READ = {
    "description": '读取会话上下文（默认最近数轮），支持正负索引翻页与块过滤开关。',
    "input_schema": {
        "properties": {
            "chat_id": {
                "anyOf": [
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'null'
                    }
                ],
                "default": None,
                "description": '目标会话 id（完整 UUID 或其第一段 8 位 hex 短 id）；不填=当前会话。'
            },
            "start": {
                "anyOf": [
                    {
                        "type": 'integer'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'null'
                    }
                ],
                "default": None,
                "description": '起始轮索引：正=可见区轮（0=最旧），负=压缩历史（-1 最新，越负越旧）；不填=最近 count 轮。'
            },
            "count": {
                "anyOf": [
                    {
                        "type": 'integer'
                    },
                    {
                        "type": 'string'
                    }
                ],
                "default": 5,
                "description": '读取轮数（默认 5，上限 50 自动降档）。'
            },
            "include_think": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含思考块。'
            },
            "include_tool": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含工具调用与结果块。'
            },
            "include_monologue": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含自言自语块。'
            },
            "include_compressed": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含被压缩的历史轮。'
            }
        },
        "type": 'object',
        "additionalProperties": False
    },
}

AGENT_TOOLS_CONTEXT_EXPORT = {
    "description": '把会话上下文导出为 Markdown 块时间线文件，返回路径。',
    "input_schema": {
        "properties": {
            "chat_id": {
                "anyOf": [
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'null'
                    }
                ],
                "default": None,
                "description": '目标会话 id（完整 UUID 或其第一段 8 位 hex 短 id）；不填=当前会话。'
            },
            "output_path": {
                "anyOf": [
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'null'
                    }
                ],
                "default": None,
                "description": '导出 Markdown 路径（必填）；相对路径挂工具基准目录（项目目录→agent 工作区），成功回显绝对路径。'
            },
            "include_think": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含思考块。'
            },
            "include_tool": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含工具调用与结果块。'
            },
            "include_monologue": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含自言自语块。'
            },
            "include_compressed": {
                "anyOf": [
                    {
                        "type": 'boolean'
                    },
                    {
                        "type": 'string'
                    },
                    {
                        "type": 'integer'
                    }
                ],
                "default": False,
                "description": '包含被压缩的历史轮。'
            }
        },
        "type": 'object',
        "additionalProperties": False
    },
}


_TOOL_SCHEMAS = {
    "agent_tools_spawn": AGENT_TOOLS_SPAWN,
    "agent_tools_communicate": AGENT_TOOLS_COMMUNICATE,
    "agent_tools_wait": AGENT_TOOLS_WAIT,
    "agent_tools_notice": AGENT_TOOLS_NOTICE,
    "agent_tools_list_agent": AGENT_TOOLS_LIST_AGENT,
    "agent_tools_list_session": AGENT_TOOLS_LIST_SESSION,
    "agent_tools_context_read": AGENT_TOOLS_CONTEXT_READ,
    "agent_tools_context_export": AGENT_TOOLS_CONTEXT_EXPORT,
}
