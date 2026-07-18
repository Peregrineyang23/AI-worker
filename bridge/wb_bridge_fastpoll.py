#!/usr/bin/env python3
"""
本地高频轮询 — 飞书桥接的"文本快速通道"

定位（来自用户决策：本地高频轮询 + WorkBuddy 多模态）：
  - 文本 / 卡片类请求：调用国内可达的 OpenAI 兼容模型 API 生成回复（无需梯子，低延迟），
    复用 wb_bridge_reply.py 的 commit 写草稿 + 发预览到私有测试会话。
  - 含多模态关键词（图片 / 视频 / 3D 等）的请求：不处理，留给 WorkBuddy 自动化（每小时）生成。

严格遵守 BRIDGE_POLICY_TRAINING_BASELINE.md：
  - 只发预览到私有测试会话，绝不直发正式群（发布由用户回「确认发送 <draft_id>」触发）。
  - 幂等（复用 helper 的认领集合）、熔断（复用 helper 的发送熔断）、不自动点赞、不周期刷屏。

模型配置（路由策略）：
  - 默认（通用文本）：HMI_BRIDGE_LLM_* 或 wb_bridge_llm.json（当前 DeepSeek）
  - UI / 交互 / 前端设计类（命中 DESIGN_KEYWORDS）：优先路由 Kimi K3（帆½ 默认设计模型）
        HMI_BRIDGE_KIMI_* 或 wb_bridge_kimi.json（model=kimi-k3, base_url=https://api.moonshot.cn/v1）
        Kimi K3 未配置时自动回退默认模型，并在日志提示。
  - 多模态（图片/视频/3D，命中 MULTIMODAL_KEYWORDS）：不在此处理，defer 给 WorkBuddy 自动化。
  - 不把密钥写死在脚本里。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.environ.get(
    "HMI_BRIDGE_BASE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
HELPER = os.path.join(BASE_DIR, "wb_bridge_reply.py")
DEFERRED_FILE = os.path.join(BASE_DIR, "bridge_workbuddy_deferred.json")
MAX_PER_RUN = 3

MULTIMODAL_KEYWORDS = (
    "图片", "插画", "海报", "封面", "配图", "示意图", "架构图", "流程图",
    "视频", "动效", "动画", "短片", "3d", "3D", "三维", "模型", "渲染",
    "生成一张", "画一张", "做一张图", "做个视频", "出个图", "视觉稿",
    "image", "video", "render", "avatar",
)

# UI / 交互 / 前端设计类意图 -> 路由到 Kimi K3（帆½ 默认设计模型）
DESIGN_KEYWORDS = (
    "ui", "界面", "原型", "交互", "hmi", "前端", "网页", "网站", "web",
    "app", "小程序", "组件", "布局", "ux", "高保真", "线框", "wireframe",
    "页面设计", "dashboard", "看板", "官网", "落地页", "landing", "gui",
    "视觉设计", "设计稿", "界面设计", "产品界面", "原型图", "figma",
    "设计系统", "design system", "配色", "字体", "排版",
)

POLICY_SYSTEM = (
    "你是飞书群「AI workers for HMI Design」的回复助手（文本快速通道，由本地模型生成）。"
    "严格遵守桥接策略：回复必须是一个明确结论 + 2-5 个信息区块 + 证据/限制 + 唯一下一步动作；"
    "不发布过程性分工、不猜测介入未 @ 你的消息、不自动点赞、不把其他机器人的「已完成」当作事实。"
    "用简洁中文，飞书 markdown 风格。"
)

DESIGN_SYSTEM = (
    "你是飞书群「AI workers for HMI Design」的 UI/交互设计助手（由 Kimi K3 驱动，帆½ 默认设计模型）。"
    "遇到 UI/界面/交互/前端设计类问题，优先给出可直接落地的产出："
    "① 若要求原型/界面/网页，优先输出可运行的 HTML/CSS/JS 代码片段或完整单文件原型；"
    "② 若要求方案/方向，给出明确结论 + 信息架构 + 交互流程 + 视觉规范（配色/字体/间距/组件）；"
    "③ 用简洁中文，飞书 markdown 风格；代码片段用代码块包裹，便于复制。"
)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_deferred():
    return set(load_json(DEFERRED_FILE, []))


def save_deferred(s):
    save_json(DEFERRED_FILE, sorted(s))


def load_llm_config():
    cfg = {
        "base_url": os.environ.get("HMI_BRIDGE_LLM_BASE_URL", ""),
        "api_key": os.environ.get("HMI_BRIDGE_LLM_API_KEY", ""),
        "model": os.environ.get("HMI_BRIDGE_LLM_MODEL", ""),
        "timeout": int(os.environ.get("HMI_BRIDGE_LLM_TIMEOUT", "60")),
    }
    path = os.path.join(BASE_DIR, "wb_bridge_llm.json")
    if os.path.exists(path):
        f = load_json(path, {})
        for k in ("base_url", "api_key", "model", "timeout"):
            if f.get(k):
                cfg[k] = f[k]
    return cfg


def load_kimi_config():
    """帆½ 默认设计模型（Kimi K3）配置：优先环境变量 HMI_BRIDGE_KIMI_*，否则 wb_bridge_kimi.json。"""
    cfg = {
        "base_url": os.environ.get("HMI_BRIDGE_KIMI_BASE_URL", ""),
        "api_key": os.environ.get("HMI_BRIDGE_KIMI_API_KEY", ""),
        "model": os.environ.get("HMI_BRIDGE_KIMI_MODEL", "kimi-k3"),
        "timeout": int(os.environ.get("HMI_BRIDGE_KIMI_TIMEOUT", "120")),
    }
    path = os.path.join(BASE_DIR, "wb_bridge_kimi.json")
    if os.path.exists(path):
        f = load_json(path, {})
        for k in ("base_url", "api_key", "model", "timeout"):
            if f.get(k):
                cfg[k] = f[k]
    # Kimi K3 仅支持 reasoning_effort="max" 一档，且采样参数固定（省略 temperature）
    cfg["reasoning_effort"] = "max"
    return cfg


def is_multimodal(request):
    low = request.lower()
    return any(k.lower() in low for k in MULTIMODAL_KEYWORDS)


def is_design(request):
    low = request.lower()
    return any(k.lower() in low for k in DESIGN_KEYWORDS)


def call_llm(cfg, user_text, system=None, design=False):
    if not cfg.get("base_url") or not cfg.get("api_key"):
        raise RuntimeError("LLM 未配置：缺少 base_url 或 api_key")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_text})
    payload = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "max_tokens": 8192 if design else 1200,
    }
    # Kimi K3 需要 reasoning_effort（仅 "max" 一档）；采样参数固定，省略 temperature
    if cfg.get("reasoning_effort"):
        payload["reasoning_effort"] = cfg["reasoning_effort"]
    else:
        payload["temperature"] = 0.3
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg.get("timeout", 60)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def markdown(content, text_size=None):
    e = {"tag": "markdown", "content": content}
    if text_size:
        e["text_size"] = text_size
    return e


def info_block(title, body, color="blue", margin="0px 0px 12px 0px"):
    return {
        "tag": "column_set", "flex_mode": "none", "margin": margin,
        "columns": [{
            "tag": "column", "width": "weighted", "weight": 1,
            "background_style": f"{color}-50", "padding": "12px 12px 12px 12px",
            "vertical_spacing": "4px",
            "elements": [
                markdown(f"**<font color='{color}'>{title}</font>**"),
                markdown(body, text_size="notation"),
            ],
        }],
    }


def build_card(request, reply_text, engine="本地快速通道"):
    now = time.strftime("%Y-%m-%d %H:%M")
    # 把回复正文按段落拆成信息块，避免单条超长
    paras = [p.strip() for p in reply_text.split("\n\n") if p.strip()]
    if not paras:
        paras = [reply_text]
    elements = [info_block("结论", paras[0], "green")]
    for p in paras[1:]:
        elements.append(info_block("补充", p, "blue"))
    elements.append({
        "tag": "div", "margin": "0px",
        "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**引擎**\n{engine}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": "**下一步**\n回复「确认发送」发布；多模态需求转 WorkBuddy"}},
        ],
    })
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default", "summary": {"content": "飞书请求回复"}},
        "header": {
            "title": {"tag": "plain_text", "content": "请求已处理（预览）"},
            "subtitle": {"tag": "plain_text", "content": f"本地快速通道 · {now}"},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "info_outlined", "color": "blue"},
            "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": "预览"}, "color": "blue"}],
        },
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px", "elements": elements},
    }


def run_helper(args):
    completed = subprocess.run(
        ["python3", HELPER, *args],
        text=True, capture_output=True, check=False,
    )
    out = completed.stdout.strip()
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or out or "helper failed")
    return out


def main():
    parser = argparse.ArgumentParser(description="飞书桥接本地高频轮询（文本快速通道）")
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    parser.add_argument("--max", type=int, default=MAX_PER_RUN, help="本轮最多处理任务数")
    args = parser.parse_args()

    default_cfg = load_llm_config()
    kimi_cfg = load_kimi_config()
    if not default_cfg.get("base_url") or not default_cfg.get("api_key"):
        print("[fastpoll] 默认 LLM 未配置，跳过本轮（请在 wb_bridge_llm.json 或环境变量中配置）。", file=sys.stderr)
        return
    kimi_ready = bool(kimi_cfg.get("base_url") and kimi_cfg.get("api_key"))
    if not kimi_ready:
        print("[fastpoll] Kimi K3 未配置（wb_bridge_kimi.json / HMI_BRIDGE_KIMI_*），UI 设计任务将回退默认模型。", file=sys.stderr)

    deferred = load_deferred()
    # 本轮跳过集：已 defer 的多模态任务 + 本轮已看过/已处理的任务。
    # 传给 helper 的 next --exclude，避免 next 反复返回同一任务（死循环/文本饿死）。
    skip = set(deferred)
    handled = 0
    fails = 0
    while handled < args.max:
        try:
            out = run_helper(["next", "--exclude", ",".join(sorted(skip))])
            info = json.loads(out)
        except Exception as e:
            print(f"[fastpoll] next 读取失败: {e}", file=sys.stderr)
            break

        task = info.get("task")
        if not task:
            break
        mid = task["message_id"]
        request = task.get("request", "")
        skip.add(mid)  # 本轮不再回看这条，保证前进

        if is_multimodal(request):
            if mid not in deferred:
                deferred.add(mid)
                save_deferred(deferred)
                print(f"[fastpoll] 任务 {mid} 含多模态意图，转交 WorkBuddy 自动化（不在此处理）。", file=sys.stderr)
            continue

        # UI / 交互 / 前端设计类 -> 优先路由 Kimi K3（帆½ 默认设计模型）
        design = is_design(request)
        if design:
            if kimi_ready:
                cfg = kimi_cfg
                engine = "Kimi K3（kimi-k3）"
                sys_prompt = DESIGN_SYSTEM
            else:
                cfg = default_cfg
                engine = f"默认模型回退（{default_cfg.get('model', 'deepseek')}）"
                sys_prompt = POLICY_SYSTEM
        else:
            cfg = default_cfg
            engine = default_cfg.get("model", "deepseek")
            sys_prompt = POLICY_SYSTEM

        try:
            reply = call_llm(cfg, f"原消息：{request}\n请按桥接策略生成正式回复。", system=sys_prompt, design=design)
            card = build_card(request, reply, engine)
            card_file = f"/tmp/wb_fastpoll_{mid}.json"
            with open(card_file, "w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False, indent=2)
            res = json.loads(run_helper([
                "commit", "--message-id", mid,
                "--chat-id", task.get("chat_id", ""),
                "--card-file", card_file,
            ]))
            print(f"[fastpoll] 已为 {mid} 生成预览草稿 {res.get('draft_id')}（sent={res.get('sent')}）。", file=sys.stderr)
            handled += 1
        except Exception as e:
            # 生成/发送失败：不认领，留给 WorkBuddy 自动化（积分）兜底。
            fails += 1
            print(f"[fastpoll] 处理 {mid} 失败: {e}", file=sys.stderr)
            if fails >= 2:
                # 连续失败（多半是模型 API 不可用），停止本轮避免空转/连打。
                print("[fastpoll] 连续失败 2 次，停止本轮，交由 WorkBuddy 自动化兜底。", file=sys.stderr)
                break
            continue

        if args.once:
            break

    if not args.once:
        # 持续轮询由 launchd 触发；脚本本身一轮即退，避免常驻重复。
        pass


if __name__ == "__main__":
    main()
