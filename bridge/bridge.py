import json
import argparse
import hashlib
import os
import re
import subprocess
import sys
import threading
import time


TARGET_CHAT_ID = "oc_98ba0dac9941993769d2b8908dcce3a0"
PRIVATE_TEST_CHAT_ID = "oc_9e756fe1e59b5b0efa8795c438f0ae45"
DM01_COGNITIVE_ALIGN_CHAT_ID = "oc_d87083ba011523de6b62119cd7137284"
AIOS_PETS_CHAT_ID = "oc_5e5cd2c549b5e4325ffcb4e0cbb5fcd1"
B76_TEST_CHAT_ID = "oc_b76fc129711da7ac28ee7279741cc33f"
WORKSPACE_DIR = os.environ.get("HMI_BRIDGE_WORKSPACE_DIR", "/Users/frankyang/Documents/AI cowork")
MAX_BACKLOG_SECONDS = int(os.environ.get("HMI_BRIDGE_MAX_BACKLOG_SECONDS", "3600"))
ALLOWED_CHAT_IDS = {
    TARGET_CHAT_ID,
    PRIVATE_TEST_CHAT_ID,
    DM01_COGNITIVE_ALIGN_CHAT_ID,
    AIOS_PETS_CHAT_ID,
    B76_TEST_CHAT_ID,
}
BASE_DIR = os.environ.get("HMI_BRIDGE_BASE_DIR", os.getcwd())
STATE_FILE = os.environ.get(
    "HMI_BRIDGE_STATE_FILE",
    os.path.join(BASE_DIR, "bridge_state.json"),
)
PENDING_FILE = os.environ.get(
    "HMI_BRIDGE_PENDING_FILE",
    os.path.join(BASE_DIR, "bridge_pending_tasks.json"),
)
DRAFT_FILE = os.environ.get(
    "HMI_BRIDGE_DRAFT_FILE",
    os.path.join(BASE_DIR, "bridge_reply_drafts.json"),
)
CONTEXT_FILE = os.environ.get(
    "HMI_BRIDGE_CONTEXT_FILE",
    os.path.join(BASE_DIR, "bridge_message_context.json"),
)
PRAISED_ANSWER_FILE = os.environ.get(
    "HMI_BRIDGE_PRAISED_ANSWER_FILE",
    os.path.join(BASE_DIR, "bridge_praised_answers.json"),
)
ESTIMATED_REPLY_SECONDS = int(os.environ.get("HMI_BRIDGE_ESTIMATED_REPLY_SECONDS", "600"))
STATUS_EXTENSION_SECONDS = int(os.environ.get("HMI_BRIDGE_STATUS_EXTENSION_SECONDS", "600"))
ACK_REACTION = os.environ.get("HMI_BRIDGE_ACK_REACTION", "OK")
REQUEST_REACTION = os.environ.get("HMI_BRIDGE_REQUEST_REACTION", "Get")
AUTO_PRAISE_ENABLED = os.environ.get("HMI_BRIDGE_AUTO_PRAISE", "0") == "1"
BOT_APP_IDS = {
    "cli_aa96499f7ce29cbd",
}
BOT_MENTION_NAMES = (
    "杨帆的飞书CLI",
    "杨帆的飞书 CLI",
    "飞书CLI",
    "飞书 CLI",
    "帆½",
)
ACK_TEXT = os.environ.get("HMI_BRIDGE_ACK_TEXT", "帆codex已收到")
CARD_STYLE_ALIASES = {
    "a": "A",
    "b": "B",
    "c": "C",
    "d": "D",
    "e": "E",
    "f": "F",
    "Ａ": "A",
    "Ｂ": "B",
    "Ｃ": "C",
    "Ｄ": "D",
    "Ｅ": "E",
    "Ｆ": "F",
}


def run_lark_cli(args):
    completed = subprocess.run(
        ["lark-cli", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed.stdout


def extract_command(content, bot_mentioned=None):
    text = content.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            text = payload.get("text", text)
        except json.JSONDecodeError:
            pass
    text = text.strip()
    for prefix in ("/dd010", "/car", "/sr", "/music", "/kb", "/collab", "/daily", "/help", "/status", "/latest"):
        if text.startswith(prefix):
            return prefix, text[len(prefix):].strip()
    for prefix in ("/dd010", "/car", "/sr", "/music", "/kb", "/collab", "/daily", "/help", "/status", "/latest"):
        index = text.find(prefix)
        if index >= 0:
            request = text[index + len(prefix):].strip()
            return prefix, request
    return infer_command_from_mention(text, bot_mentioned)


def infer_command_from_mention(text, bot_mentioned=None):
    mentioned = bool(bot_mentioned) or any(name in text for name in BOT_MENTION_NAMES)
    if not mentioned:
        return None, text

    cleaned = text
    for name in BOT_MENTION_NAMES:
        cleaned = cleaned.replace(f"@{name}", "")
        cleaned = cleaned.replace(name, "")
    cleaned = re.sub(r"^\s*@\S+(?:\s+CLI)?\s*", "", cleaned)
    cleaned = " ".join(cleaned.split())

    lowered = cleaned.lower()
    if any(keyword in lowered for keyword in ("今天", "今日", "daily", "日报")) and any(
        keyword in lowered
        for keyword in ("更新", "飞书文档", "文档", "codex", "项目", "改动")
    ):
        return "/daily", cleaned
    if any(
        keyword in lowered
        for keyword in (
            "协同",
            "最小token",
            "最小 token",
            "合并",
            "知识库",
            "置顶",
            "版本管理",
            "版本",
            "文档合并",
            "归档",
            "策略",
            "分发",
            "专注",
            "当前问题",
            "取消之前",
            "只回复",
        )
    ):
        if any(keyword in lowered for keyword in ("策略", "分发", "专注", "当前问题", "取消之前", "只回复")):
            return "/collab", cleaned
        return "/kb", cleaned
    if any(keyword in lowered for keyword in ("music", "audio", "音乐", "音频", "声场", "律动", "氛围灯")):
        return "/music", cleaned
    if any(keyword in lowered for keyword in ("sr", "智驾", "noa", "adas", "感知", "接管", "泊车")):
        return "/sr", cleaned
    if any(
        keyword in lowered
        for keyword in (
            "dd010",
            "车辆",
            "车身",
            "车控",
            "红旗",
            "figma",
            "设计文件",
            "监测",
            "3d",
            "car",
            "vehicle",
        )
    ):
        return "/dd010", cleaned
    if any(keyword in lowered for keyword in ("状态", "status", "运行", "在线")):
        return "/status", cleaned
    if any(
        keyword in lowered
        for keyword in (
            "结果",
            "报告",
            "进度",
            "完成",
            "刚才",
            "执行",
            "没看到",
            "没有看到",
            "文档",
            "链接",
        )
    ):
        return "/latest", cleaned
    return "/help", cleaned


def load_seen_message_ids():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return set(state.get("seen_message_ids", []))


def save_seen_message_ids(seen):
    recent = list(seen)[-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump({"seen_message_ids": recent}, file, ensure_ascii=False, indent=2)


def load_pending_tasks():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def save_pending_tasks(tasks):
    with open(PENDING_FILE, "w", encoding="utf-8") as file:
        json.dump(tasks, file, ensure_ascii=False, indent=2, sort_keys=True)


def load_reply_drafts():
    try:
        with open(DRAFT_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def save_reply_drafts(drafts):
    with open(DRAFT_FILE, "w", encoding="utf-8") as file:
        json.dump(drafts, file, ensure_ascii=False, indent=2, sort_keys=True)


def load_message_contexts():
    try:
        with open(CONTEXT_FILE, "r", encoding="utf-8") as file:
            contexts = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return contexts if isinstance(contexts, dict) else {}


def save_message_contexts(contexts):
    recent = sorted(
        contexts.items(),
        key=lambda item: item[1].get("created_at", 0),
        reverse=True,
    )[:300]
    with open(CONTEXT_FILE, "w", encoding="utf-8") as file:
        json.dump(dict(recent), file, ensure_ascii=False, indent=2, sort_keys=True)


def remember_message_context(message_id, content, sender_type, reply_to=None):
    if not message_id:
        return
    contexts = load_message_contexts()
    contexts[message_id] = {
        "content": extract_message_text(content)[:8000],
        "sender_type": sender_type,
        "reply_to": reply_to or "",
        "created_at": time.time(),
    }
    save_message_contexts(contexts)


def load_praised_answer_fingerprints():
    try:
        with open(PRAISED_ANSWER_FILE, "r", encoding="utf-8") as file:
            fingerprints = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return fingerprints if isinstance(fingerprints, list) else []


def save_praised_answer_fingerprint(fingerprint):
    fingerprints = load_praised_answer_fingerprints()
    if fingerprint not in fingerprints:
        fingerprints.append(fingerprint)
    with open(PRAISED_ANSWER_FILE, "w", encoding="utf-8") as file:
        json.dump(fingerprints[-500:], file, ensure_ascii=False, indent=2)


def format_local_time(timestamp):
    return time.strftime("%H:%M", time.localtime(timestamp))


def extract_message_text(content):
    text = content.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            text = payload.get("text", text)
        except json.JSONDecodeError:
            pass
    return text.strip()


def extract_card_style_kind(content):
    text = extract_message_text(content)
    lowered = text.lower()
    if not any(keyword in lowered for keyword in ("样式", "style", "card", "卡片", "测试", "测")):
        return None
    match = re.search(r"(?:样式|style|card|卡片)\s*([a-fA-FＡ-Ｆ])", text, re.IGNORECASE)
    if not match:
        match = re.search(r"([a-fA-FＡ-Ｆ])\s*(?:样式|style|card|卡片)", text, re.IGNORECASE)
    if not match:
        return None
    return CARD_STYLE_ALIASES.get(match.group(1), CARD_STYLE_ALIASES.get(match.group(1).lower()))


def markdown(content, text_size=None, text_align=None):
    element = {"tag": "markdown", "content": content}
    if text_size:
        element["text_size"] = text_size
    if text_align:
        element["text_align"] = text_align
    return element


def info_block(title, body, color="blue", margin="0px 0px 12px 0px"):
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "margin": margin,
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "background_style": f"{color}-50",
                "padding": "12px 12px 12px 12px",
                "vertical_spacing": "4px",
                "elements": [
                    markdown(f"**<font color='{color}'>{title}</font>**"),
                    markdown(body, text_size="notation"),
                ],
            }
        ],
    }


def field_block(fields, color="grey", margin="0px"):
    return {
        "tag": "div",
        "margin": margin,
        "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"}}
            for label, value in fields
        ],
    }


def button_element(text, value, button_type="default"):
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": button_type,
        "behaviors": [{"type": "callback", "value": value}],
    }


def card_base(title, subtitle, template, tag_text, tag_color, width_mode="default"):
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": width_mode,
            "summary": {"content": title},
        },
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": template,
            "icon": {"tag": "standard_icon", "token": "info_outlined", "color": template},
            "text_tag_list": [
                {
                    "tag": "text_tag",
                    "text": {"tag": "plain_text", "content": tag_text},
                    "color": tag_color,
                }
            ],
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "vertical_spacing": "8px",
            "elements": [],
        },
    }


def build_style_card(kind):
    now_text = time.strftime("%Y-%m-%d %H:%M")
    if kind == "A":
        card = card_base("帆codex已收到", f"样式 A · 极简确认 · {now_text}", "green", "收到", "green", "compact")
        card["body"]["elements"] = [
            info_block("已收到", "**帆codex已收到**\n<font color='grey'>这是一张低打扰确认卡，不展开任务分工。</font>", "green"),
            field_block([("状态", "已进入队列"), ("反馈", "仅确认收到")]),
        ]
        return card
    if kind == "B":
        card = card_base("需求分流预览", f"样式 B · 只显示类型，不公开分工 · {now_text}", "blue", "预览", "blue")
        card["body"]["elements"] = [
            info_block("帆codex已收到", "已识别到这是一条可分类需求，但卡片不展示项目分工和执行计划。", "blue"),
            {
                "tag": "column_set",
                "flex_mode": "trisect",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "background_style": "blue-50",
                        "padding": "10px",
                        "elements": [markdown("**设计分析**", text_align="center")],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "background_style": "wathet-50",
                        "padding": "10px",
                        "elements": [markdown("**资料整理**", text_align="center")],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "background_style": "turquoise-50",
                        "padding": "10px",
                        "elements": [markdown("**任务执行**", text_align="center")],
                    },
                ],
            },
        ]
        return card
    if kind == "C":
        card = card_base("处理中", f"样式 C · 长任务状态 · {now_text}", "turquoise", "处理中", "turquoise")
        card["body"]["elements"] = [
            info_block("帆codex处理中", "不会在群里展开分工；完成后只回结果摘要或产物链接。", "turquoise"),
            field_block([("阶段", "处理中"), ("下次反馈", "有结果时")]),
            info_block("状态轨迹", "已收到  >  处理中  >  待确认  >  已完成", "wathet", "0px"),
        ]
        return card
    if kind == "D":
        card = card_base("需要补充信息", f"样式 D · 静态交互预览 · {now_text}", "orange", "待确认", "orange")
        card["body"]["elements"] = [
            info_block("还差一个关键信息", "请选择要补充的范围。当前仅用于样式预览，按钮回调还未接入业务处理。", "orange"),
            button_element("补充范围", {"style": "D", "action": "scope"}, "primary_filled"),
            button_element("选择输出格式", {"style": "D", "action": "format"}),
            button_element("取消", {"style": "D", "action": "cancel"}),
        ]
        return card
    if kind == "E":
        card = card_base("已完成", f"样式 E · 完成摘要 · {now_text}", "green", "完成", "green")
        card["body"]["elements"] = [
            info_block("结果摘要", "任务已完成。这里展示最重要的结论、产物链接和需要人工查看的点。", "green"),
            field_block([("结果", "摘要已生成"), ("产物", "等待链接接入")], margin="0px 0px 12px 0px"),
            button_element("打开文档", {"style": "E", "action": "open_doc"}, "primary_filled"),
            button_element("继续追问", {"style": "E", "action": "follow_up"}),
        ]
        return card
    if kind == "F":
        card = card_base("没有完成", f"样式 F · 失败或降级 · {now_text}", "red", "需处理", "red")
        card["body"]["elements"] = [
            info_block("执行未完成", "可能是权限、网络、文档访问或 worker 异常。卡片只给可恢复动作，不展开内部分工。", "red"),
            field_block([("原因", "示例：权限不足"), ("建议", "重新授权或稍后重试")], margin="0px 0px 12px 0px"),
            button_element("重试", {"style": "F", "action": "retry"}, "primary_filled"),
            button_element("查看日志", {"style": "F", "action": "logs"}),
        ]
        return card
    return None


def send_chat_message(chat_id, text, key_seed):
    digest = hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:16]
    return run_lark_cli(
        [
            "im",
            "+messages-send",
            "--as",
            "bot",
            "--chat-id",
            chat_id,
            "--text",
            text,
            "--idempotency-key",
            f"hmi{digest}{int(time.time())}",
        ]
    )


def send_chat_card(chat_id, card, key_seed):
    digest = hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:16]
    return run_lark_cli(
        [
            "im",
            "+messages-send",
            "--as",
            "bot",
            "--chat-id",
            chat_id,
            "--msg-type",
            "interactive",
            "--content",
            json.dumps(card, ensure_ascii=False, separators=(",", ":")),
            "--idempotency-key",
            f"hmi{digest}{int(time.time())}",
        ]
    )


def reply_message_card(message_id, card, key_seed):
    digest = hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:16]
    return run_lark_cli(
        [
            "im",
            "+messages-reply",
            "--as",
            "bot",
            "--message-id",
            message_id,
            "--msg-type",
            "interactive",
            "--content",
            json.dumps(card, ensure_ascii=False, separators=(",", ":")),
            "--idempotency-key",
            f"hmi{digest}{int(time.time())}",
        ]
    )


def add_message_reaction(message_id, emoji_type=None):
    return run_lark_cli(
        [
            "im",
            "reactions",
            "create",
            "--as",
            "bot",
            "--message-id",
            message_id,
            "--data",
            json.dumps(
                {"reaction_type": {"emoji_type": emoji_type or ACK_REACTION}},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ]
    )


def failure_message(message_id, chat_id=TARGET_CHAT_ID, reason="未知错误"):
    send_chat_message(
        chat_id,
        f"帆codex任务失败：{reason}\n我已停止继续预估这条任务，请重新 @ 我或补充信息后再试。",
        f"failed-{message_id}",
    )


def is_capability_survey(content):
    text = extract_message_text(content)
    keywords = ("能力调研", "对接的是什么大模型", "飞书权限", "MCP", "开发框架", "开发者")
    return sum(1 for keyword in keywords if keyword.lower() in text.lower()) >= 3


def is_bridge_stability_review(content):
    text = extract_message_text(content).lower()
    keywords = ("桥接复盘", "桥接稳定", "稳定性复盘", "网络稳定", "上下文刷屏", "子agent")
    return any(keyword in text for keyword in keywords)


def build_bridge_stability_review_card():
    card = card_base(
        "桥接稳定性复盘",
        f"飞书入口治理方案 · {time.strftime('%Y-%m-%d %H:%M')}",
        "blue",
        "待发布",
        "blue",
    )
    card["body"]["vertical_spacing"] = "12px"
    card["body"]["elements"] = [
        info_block(
            "核心结论",
            "**飞书机器人应是独立、稳定的入口与交付 Agent。**\n<font color='grey'>Codex / Claude 仅作为终端协作子 Agent，返回结构化结果，不直接决定群消息节奏。</font>",
            "blue",
            "0px",
        ),
        {
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "12px",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "background_style": "blue-50",
                    "padding": "12px",
                    "vertical_spacing": "4px",
                    "elements": [
                        markdown("**<font color='blue'>本次问题</font>**"),
                        markdown("梯子中断后旧 bridge 未及时重启；旧待办与旧上下文继续出站，形成群聊刷屏污染。", "notation"),
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "background_style": "wathet-50",
                    "padding": "12px",
                    "vertical_spacing": "4px",
                    "elements": [
                        markdown("**<font color='wathet'>目标架构</font>**"),
                        markdown("Gateway 接收与交付；编排层管理队列和超时；子 Agent 只产出 success / failed / needs_input。", "notation"),
                    ],
                },
            ],
        },
        info_block(
            "出站保护",
            "`message_id` 幂等去重 · 旧任务到期即作废 · 网络或鉴权连续失败即熔断出站 · 恢复后不回放历史过程消息。",
            "turquoise",
            "0px",
        ),
        field_block(
            [
                ("收到", "原消息 OK 表情"),
                ("超时", "最多一次说明"),
                ("正式结果", "先私聊预览确认"),
                ("失败", "仅一次失败反馈"),
            ],
            margin="0px",
        ),
    ]
    return card


def build_capability_survey_card():
    card = card_base(
        "帆codex能力调研回复",
        f"结构化设计卡片 · {time.strftime('%Y-%m-%d %H:%M')}",
        "blue",
        "待发布",
        "blue",
    )
    card["body"]["elements"] = [
        info_block(
            "核心定位",
            "**面向 HMI Design 团队的本地协作与自动化助手**\n<font color='grey'>默认先确认收到，再按需要输出状态、预览和正式结果。</font>",
            "blue",
        ),
        field_block(
            [
                ("大模型", "OpenAI Codex 系列模型"),
                ("角色", "帆 codex / HMI 设计协作助手"),
                ("运行方式", "Python bridge + launchd + lark-cli"),
                ("编排框架", "非 LangChain / Dify / Coze"),
            ],
            margin="0px 0px 12px 0px",
        ),
        info_block(
            "飞书与工具能力",
            "已验证消息读取、群/私聊发送、卡片消息、轮询与事件监听；文档、云盘、表格等能力以当前应用和用户授权为准。",
            "wathet",
        ),
        info_block(
            "MCP / API 能力",
            "可调用 lark-cli、Codex 本地工作区、Shell、文件读写，以及已接入的 MCP/插件能力，适合消息桥接、文档整理、设计资料沉淀和自动化工作流。",
            "turquoise",
        ),
        info_block(
            "当前回复规则",
            "支持收到确认、预计回复时间、到点续报、失败回报；正式内容先生成结构化卡片预览，经确认后再发到群里。",
            "blue",
            "0px",
        ),
    ]
    return card


def build_formal_card(content, command):
    if is_bridge_stability_review(content):
        return build_bridge_stability_review_card()
    if is_capability_survey(content):
        return build_capability_survey_card()
    return None


def draft_id_for(message_id):
    return hashlib.sha1(message_id.encode("utf-8")).hexdigest()[:8]


def save_reply_draft(message_id, chat_id, card):
    drafts = load_reply_drafts()
    draft_id = draft_id_for(message_id)
    drafts[draft_id] = {
        "draft_id": draft_id,
        "source_message_id": message_id,
        "target_chat_id": chat_id,
        "created_at": time.time(),
        "card": card,
        "status": "pending_review",
    }
    save_reply_drafts(drafts)
    return draft_id


def get_latest_pending_draft(drafts):
    pending = [draft for draft in drafts.values() if draft.get("status") == "pending_review"]
    if not pending:
        return None
    return sorted(pending, key=lambda draft: draft.get("created_at", 0), reverse=True)[0]


def extract_confirm_draft_id(content):
    text = extract_message_text(content)
    lowered = text.lower()
    if not any(keyword in lowered for keyword in ("确认发送", "确认发出", "同意发送", "发布", "send")):
        return None
    match = re.search(r"\b([0-9a-f]{8})\b", lowered)
    return match.group(1) if match else ""


def send_preview_for_review(draft_id, card):
    preview = json.loads(json.dumps(card, ensure_ascii=False))
    preview["header"]["text_tag_list"] = [
        {
            "tag": "text_tag",
            "text": {"tag": "plain_text", "content": "预览待确认"},
            "color": "orange",
        }
    ]
    preview["header"]["subtitle"] = {
        "tag": "plain_text",
        "content": f"草稿 {draft_id} · 回复「确认发送 {draft_id}」后发出",
    }
    send_chat_card(PRIVATE_TEST_CHAT_ID, preview, f"preview-{draft_id}")
    send_chat_message(
        PRIVATE_TEST_CHAT_ID,
        f"结构化卡片预览已生成。确认无误后回复：确认发送 {draft_id}",
        f"preview-note-{draft_id}",
    )


def handle_confirm_send(content, chat_id=TARGET_CHAT_ID):
    draft_id = extract_confirm_draft_id(content)
    if draft_id is None:
        return False
    drafts = load_reply_drafts()
    if draft_id == "":
        draft = get_latest_pending_draft(drafts)
        if not draft:
            send_chat_message(chat_id, "没有找到待确认的卡片草稿。", "confirm-no-draft")
            return True
        draft_id = draft["draft_id"]
    else:
        draft = drafts.get(draft_id)
    if not draft or draft.get("status") != "pending_review":
        send_chat_message(chat_id, f"没有找到待确认草稿：{draft_id}", f"confirm-missing-{draft_id}")
        return True
    reply_message_card(draft["source_message_id"], draft["card"], f"publish-{draft_id}")
    draft["status"] = "sent"
    draft["sent_at"] = time.time()
    drafts[draft_id] = draft
    save_reply_drafts(drafts)
    clear_pending_task(draft["source_message_id"])
    send_chat_message(chat_id, f"已发送结构化卡片：{draft_id}", f"confirm-sent-{draft_id}")
    return True


def register_pending_task(message_id, content, command, chat_id=TARGET_CHAT_ID):
    now = time.time()
    eta_at = now + ESTIMATED_REPLY_SECONDS
    tasks = load_pending_tasks()
    tasks[message_id] = {
        "message_id": message_id,
        "chat_id": chat_id,
        "command": command or "",
        "request": extract_message_text(content)[:500],
        "created_at": now,
        "eta_at": eta_at,
        "status": "等待最终回复生成",
        "timeout_notified": False,
    }
    save_pending_tasks(tasks)
    return eta_at


def clear_pending_task(message_id):
    tasks = load_pending_tasks()
    if message_id in tasks:
        tasks.pop(message_id, None)
        save_pending_tasks(tasks)


def update_pending_status(message_id, status):
    tasks = load_pending_tasks()
    if message_id in tasks:
        tasks[message_id]["status"] = status
        save_pending_tasks(tasks)


def check_pending_tasks():
    now = time.time()
    tasks = load_pending_tasks()
    changed = False
    for message_id, task in list(tasks.items()):
        eta_at = float(task.get("eta_at", 0) or 0)
        if eta_at > now or task.get("timeout_notified"):
            continue
        task["timeout_notified"] = True
        task["timeout_notified_at"] = now
        task["status"] = "已超时，保持静默等待正式结果"
        tasks[message_id] = task
        changed = True
    if changed:
        save_pending_tasks(tasks)


def send_style_card(kind, message_id, chat_id=TARGET_CHAT_ID):
    card = build_style_card(kind)
    if not card:
        return False
    send_chat_card(chat_id, card, f"style-{kind}-{message_id}")
    return True


def is_bot_mentioned(message_or_event):
    mentions = message_or_event.get("mentions") or []
    for mention in mentions:
        mention_id = mention.get("id") or mention.get("user_id") or mention.get("open_id")
        mention_name = mention.get("name") or mention.get("text") or ""
        if mention_id in BOT_APP_IDS or mention_name in BOT_MENTION_NAMES:
            return True
    return False


def sender_type_of(message_or_event):
    sender = message_or_event.get("sender") or {}
    return message_or_event.get("sender_type") or sender.get("sender_type") or ""


def is_supported_sender(message_or_event):
    return sender_type_of(message_or_event) in {"user", "app", "bot"}


def is_request_or_question(content):
    text = extract_message_text(content).lower()
    request_words = (
        "?",
        "？",
        "请",
        "帮",
        "需要",
        "能否",
        "是否",
        "麻烦",
        "创建",
        "分析",
        "确认",
        "给出",
        "提交",
        "执行",
    )
    return any(word in text for word in request_words)


def is_answer_worthy_of_praise(content, reply_to):
    contexts = load_message_contexts()
    parent = contexts.get(reply_to, {})
    question = parent.get("content", "")
    answer = extract_message_text(content)
    if not question or len(answer) < 60:
        return False
    lowered = answer.lower()
    if any(word in lowered for word in ("无法确认", "不确定", "未完成", "失败", "error", "exception")):
        return False
    answer_markers = ("结论", "方案", "建议", "结果", "已完成", "执行", "分析")
    if not any(marker in answer for marker in answer_markers):
        return False
    raw_terms = re.findall(r"[\u4e00-\u9fff]{4,}|[a-zA-Z]{4,}", question.lower())
    terms = set()
    for term in raw_terms:
        if term in {"codex", "agent", "lark"}:
            continue
        terms.add(term)
        terms.update(term[index : index + 6] for index in range(max(0, len(term) - 5)))
    if not terms or not any(term in lowered for term in terms):
        return False
    fingerprint = hashlib.sha1(answer.strip().encode("utf-8")).hexdigest()
    if fingerprint in load_praised_answer_fingerprints():
        return False
    save_praised_answer_fingerprint(fingerprint)
    return True


def extract_high_quality_praise_target(content):
    text = extract_message_text(content).lower()
    if not any(phrase in text for phrase in ("高质量点赞", "确认点赞", "允许点赞")):
        return None
    match = re.search(r"\b(om_[a-z0-9_]+)\b", text)
    return match.group(1) if match else ""


def process_message(
    message_id,
    content,
    seen,
    sender_id=None,
    chat_id=TARGET_CHAT_ID,
    bot_mentioned=None,
    sender_type="user",
    reply_to=None,
):
    if not message_id or message_id in seen:
        return False
    if sender_id in BOT_APP_IDS:
        seen.add(message_id)
        return False
    if sender_type in {"app", "bot"}:
        try:
            if reply_to:
                add_message_reaction(message_id, REQUEST_REACTION)
                if AUTO_PRAISE_ENABLED and is_answer_worthy_of_praise(content, reply_to):
                    add_message_reaction(message_id, "THUMBSUP")
            elif bot_mentioned and is_request_or_question(content):
                add_message_reaction(message_id, REQUEST_REACTION)
        except Exception as exc:
            print(f"Bot reaction error for {message_id}: {exc}", file=sys.stderr)
        seen.add(message_id)
        return True
    praise_target = extract_high_quality_praise_target(content)
    if praise_target is not None:
        if praise_target:
            add_message_reaction(praise_target, "THUMBSUP")
        seen.add(message_id)
        return True
    if handle_confirm_send(content, chat_id):
        seen.add(message_id)
        return True
    if not bot_mentioned:
        seen.add(message_id)
        return False
    style_kind = extract_card_style_kind(content)
    if style_kind:
        print(f"Processing {message_id}: style={style_kind}", file=sys.stderr)
        send_style_card(style_kind, message_id, chat_id)
        seen.add(message_id)
        return True
    command, request = extract_command(content, bot_mentioned)
    print(f"Processing {message_id}: command={command}", file=sys.stderr)
    if command is None:
        seen.add(message_id)
        return False
    eta_at = register_pending_task(message_id, content, command, chat_id)
    try:
        add_message_reaction(message_id)
    except Exception as exc:
        print(f"Ack reaction error for {message_id}: {exc}", file=sys.stderr)
    formal_card = build_formal_card(content, command)
    if formal_card:
        draft_id = save_reply_draft(message_id, chat_id, formal_card)
        send_preview_for_review(draft_id, formal_card)
        update_pending_status(message_id, f"已生成结构化卡片预览，等待确认发送 {draft_id}")
    seen.add(message_id)
    return True


def is_recent_message(message):
    raw_time = message.get("create_time")
    if not raw_time:
        return True
    now = time.time()
    try:
        if isinstance(raw_time, str) and raw_time.isdigit():
            created = int(raw_time) / 1000
        elif isinstance(raw_time, (int, float)):
            created = float(raw_time)
            if created > 100000000000:
                created = created / 1000
        else:
            created = time.mktime(time.strptime(str(raw_time), "%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return True
    return now - created <= MAX_BACKLOG_SECONDS


def safe_process_message(
    message_id,
    content,
    seen,
    lock,
    sender_id=None,
    chat_id=TARGET_CHAT_ID,
    bot_mentioned=None,
    sender_type="user",
    reply_to=None,
):
    with lock:
        try:
            changed = process_message(
                message_id,
                content,
                seen,
                sender_id,
                chat_id,
                bot_mentioned,
                sender_type,
                reply_to,
            )
            if changed:
                save_seen_message_ids(seen)
            return changed
        except Exception as exc:
            print(f"Processing error for {message_id}: {exc}", file=sys.stderr)
            clear_pending_task(message_id)
            if message_id:
                try:
                    failure_message(message_id, chat_id, str(exc)[:600])
                except Exception as reply_exc:
                    print(f"Failure reply error for {message_id}: {reply_exc}", file=sys.stderr)
            return False


def poll_once(seen):
    changed = False
    for chat_id in ALLOWED_CHAT_IDS:
        output = run_lark_cli(
            [
                "im",
                "+chat-messages-list",
                "--chat-id",
                chat_id,
                "--as",
                "user",
                "--page-size",
                "10",
                "--no-reactions",
            ]
        )
        data = json.loads(output).get("data", {})
        messages = list(reversed(data.get("messages", [])))
        for message in messages:
            if not is_recent_message(message):
                seen.add(message.get("message_id", ""))
                continue
            sender = message.get("sender", {})
            if not is_supported_sender(message):
                seen.add(message.get("message_id", ""))
                continue
            remember_message_context(
                message.get("message_id"),
                message.get("content", ""),
                sender_type_of(message),
                message.get("reply_to"),
            )
            changed = process_message(
                message.get("message_id"),
                message.get("content", ""),
                seen,
                sender.get("id"),
                message.get("chat_id") or chat_id,
                is_bot_mentioned(message),
                sender_type_of(message),
                message.get("reply_to"),
            ) or changed
    save_seen_message_ids(seen)
    check_pending_tasks()
    return changed


def poll_messages(interval_seconds, once=False):
    seen = load_seen_message_ids()
    if once:
        poll_once(seen)
        return
    print("Polling Feishu group messages. Press Ctrl+C to stop.", file=sys.stderr)
    while True:
        try:
            poll_once(seen)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Polling error: {exc}", file=sys.stderr)
            try:
                check_pending_tasks()
            except Exception as status_exc:
                print(f"Pending status error: {status_exc}", file=sys.stderr)
        time.sleep(interval_seconds)


def consume_events():
    seen = load_seen_message_ids()
    lock = threading.RLock()
    consume_events_forever(seen, lock)


def consume_events_forever(seen, lock):
    print("Consuming Feishu im.message.receive_v1 events.", file=sys.stderr)
    while True:
        process = subprocess.Popen(
            [
                "lark-cli",
                "event",
                "consume",
                "im.message.receive_v1",
                "--as",
                "bot",
                "--quiet",
            ],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"Skipping non-JSON event: {line}", file=sys.stderr)
                continue

            chat_id = event.get("chat_id")
            if chat_id not in ALLOWED_CHAT_IDS:
                continue

            message_id = event.get("message_id") or event.get("id") or event.get("event_id")
            content = event.get("content", "")
            sender_id = event.get("sender_id")
            if not is_supported_sender(event):
                if message_id:
                    seen.add(message_id)
                    save_seen_message_ids(seen)
                continue
            remember_message_context(message_id, content, sender_type_of(event), event.get("reply_to"))
            safe_process_message(
                message_id,
                content,
                seen,
                lock,
                sender_id,
                chat_id,
                is_bot_mentioned(event),
                sender_type_of(event),
                event.get("reply_to"),
            )

        stderr = ""
        if process.stderr is not None:
            stderr = process.stderr.read().strip()
        code = process.wait()
        print(f"Event consumer exited code={code} stderr={stderr}", file=sys.stderr)
        time.sleep(3)


def hybrid_messages(interval_seconds):
    seen = load_seen_message_ids()
    lock = threading.RLock()
    thread = threading.Thread(
        target=consume_events_forever,
        args=(seen, lock),
        daemon=True,
    )
    thread.start()
    print("Hybrid Feishu bridge started: event consume + poll fallback.", file=sys.stderr)
    while True:
        try:
            with lock:
                poll_once(seen)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Hybrid polling error: {exc}", file=sys.stderr)
            try:
                with lock:
                    check_pending_tasks()
            except Exception as status_exc:
                print(f"Pending status error: {status_exc}", file=sys.stderr)
        time.sleep(interval_seconds)


def consume_events_legacy():
    seen = load_seen_message_ids()
    process = subprocess.Popen(
        [
            "lark-cli",
            "event",
            "consume",
            "im.message.receive_v1",
            "--as",
            "bot",
            "--quiet",
        ],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"Skipping non-JSON event: {line}", file=sys.stderr)
            continue

        chat_id = event.get("chat_id")
        if chat_id not in ALLOWED_CHAT_IDS:
            continue

        message_id = event.get("message_id") or event.get("id")
        content = event.get("content", "")
        sender_id = event.get("sender_id")
        if not is_supported_sender(event):
            if message_id:
                seen.add(message_id)
                save_seen_message_ids(seen)
            continue
        remember_message_context(message_id, content, sender_type_of(event), event.get("reply_to"))

        try:
            if process_message(
                message_id,
                content,
                seen,
                sender_id,
                chat_id,
                is_bot_mentioned(event),
                sender_type_of(event),
                event.get("reply_to"),
            ):
                save_seen_message_ids(seen)
        except Exception as exc:
            print(f"Processing error for {message_id or 'unknown message'}: {exc}", file=sys.stderr)
            clear_pending_task(message_id)
            if message_id:
                try:
                    failure_message(message_id, chat_id, str(exc)[:600])
                except Exception as reply_exc:
                    print(f"Failure reply error for {message_id}: {reply_exc}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["event", "poll", "hybrid"],
        default="hybrid",
        help="hybrid uses event consume plus poll fallback; event uses Feishu event consume only; poll reads recent group messages periodically.",
    )
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.mode == "poll":
        poll_messages(args.interval, once=args.once)
    elif args.mode == "hybrid":
        if args.once:
            poll_messages(args.interval, once=True)
        else:
            hybrid_messages(args.interval)
    else:
        consume_events()


if __name__ == "__main__":
    main()
