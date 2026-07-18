#!/usr/bin/env python3
"""帆½ 桥接状态监控：读取网关运行时状态文件，输出精简状态卡。

用法:
    python3 bridge_status_report.py [--format md|plain]
环境变量:
    HMI_BRIDGE_BASE_DIR  状态文件所在目录（默认 ~/Library/Application Support/HMIAIWorkersBridge）

--format md    : Markdown（给 WorkBuddy 自动化/阅读用）
--format plain: 纯文本（给飞书文本消息用，飞书文本不渲染 Markdown）
"""
import argparse
import json
import os
from collections import Counter
from datetime import datetime

BASE = os.environ.get(
    "HMI_BRIDGE_BASE_DIR",
    os.path.expanduser("~/Library/Application Support/HMIAIWorkersBridge"),
)


def load(name, default=None):
    p = os.path.join(BASE, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def rel(ts):
    if not ts:
        return "—"
    try:
        dt = datetime.fromtimestamp(float(ts))
    except Exception:
        return str(ts)
    d = (datetime.now() - dt).total_seconds()
    if d < 60:
        return f"{int(d)}秒前"
    if d < 3600:
        return f"{int(d // 60)}分钟前"
    if d < 86400:
        return f"{int(d // 3600)}小时前"
    return f"{int(d // 86400)}天前"


def short(mid):
    if not isinstance(mid, str):
        return str(mid)
    return mid if len(mid) <= 24 else mid[:12] + "…" + mid[-8:]


def collect():
    state = load("bridge_state.json", {}) or {}
    pending = load("bridge_pending_tasks.json", {}) or {}
    drafts = load("bridge_reply_drafts.json", {}) or {}
    claimed = load("bridge_workbuddy_claimed.json", []) or []
    deferred = load("bridge_workbuddy_deferred.json", []) or {}

    pc = Counter(v.get("status", "?") for v in pending.values() if isinstance(v, dict))
    dc = Counter(v.get("status", "?") for v in drafts.values() if isinstance(v, dict))

    recent_tasks = sorted(
        (v for v in pending.values() if isinstance(v, dict)),
        key=lambda t: t.get("created_at", 0),
        reverse=True,
    )[:6]

    log_lines = []
    logp = os.path.join(BASE, "logs", "bridge.err.log")
    try:
        with open(logp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "Processing om_" in line:
                    log_lines.append(line.strip())
    except FileNotFoundError:
        pass
    recent_log = log_lines[-5:]

    return {
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "seen_n": len(state.get("seen_message_ids", [])),
        "pc": pc,
        "dc": dc,
        "claimed_n": len(claimed),
        "deferred_n": len(deferred),
        "recent_tasks": recent_tasks,
        "recent_log": recent_log,
        "base": BASE,
    }


def render_md(d):
    L = []
    L.append(f"# 帆½ 桥接状态 · {d['now']}")
    L.append("")
    L.append("## 接入")
    L.append(f"- 已监听消息(seen): **{d['seen_n']}** 条")
    if d["recent_log"]:
        L.append(f"- 最近处理: `{d['recent_log'][-1]}`")
    L.append("")
    L.append("## 任务队列 (bridge_pending_tasks)")
    if d["pc"]:
        for k, v in d["pc"].most_common():
            L.append(f"- {k}: **{v}**")
    else:
        L.append("- 无")
    L.append("")
    L.append("## 草稿 (bridge_reply_drafts)")
    if d["dc"]:
        for k, v in d["dc"].most_common():
            L.append(f"- {k}: **{v}**")
    else:
        L.append("- 无")
    L.append("")
    L.append("## WorkBuddy 握手")
    L.append(f"- 已认领(处理中): **{d['claimed_n']}** 条")
    L.append(f"- 推迟: **{d['deferred_n']}** 条")
    L.append("")
    L.append("## 最近活动")
    for t in d["recent_tasks"]:
        L.append(
            f"- {rel(t.get('created_at'))} · {short(t.get('message_id', ''))} "
            f"· {t.get('command', '')} · {t.get('status', '')}"
        )
    if d["recent_log"]:
        L.append("")
        L.append("### 网关处理流(bridge.err.log 尾部)")
        for ln in d["recent_log"]:
            L.append(f"- {ln}")
    L.append("")
    L.append(f"> 数据来源: `{d['base']}`")
    return "\n".join(L)


def render_plain(d):
    L = []
    L.append(f"帆½ 桥接状态 · {d['now']}")
    L.append("=" * 32)
    L.append(f"[接入] 已监听消息(seen): {d['seen_n']} 条")
    if d["recent_log"]:
        L.append(f"[接入] 最近处理: {d['recent_log'][-1]}")
    L.append("")
    L.append("[任务队列]")
    if d["pc"]:
        for k, v in d["pc"].most_common():
            L.append(f"  - {k}: {v}")
    else:
        L.append("  - 无")
    L.append("")
    L.append("[草稿]")
    if d["dc"]:
        for k, v in d["dc"].most_common():
            L.append(f"  - {k}: {v}")
    else:
        L.append("  - 无")
    L.append("")
    L.append(f"[WorkBuddy 握手] 已认领(处理中): {d['claimed_n']} 条；推迟: {d['deferred_n']} 条")
    L.append("")
    L.append("[最近活动]")
    for t in d["recent_tasks"]:
        L.append(
            f"  - {rel(t.get('created_at'))} · {short(t.get('message_id', ''))} "
            f"· {t.get('command', '')} · {t.get('status', '')}"
        )
    if d["recent_log"]:
        L.append("")
        L.append("[网关处理流]")
        for ln in d["recent_log"]:
            L.append(f"  - {ln}")
    L.append("")
    L.append(f"数据来源: {d['base']}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["md", "plain"], default="md")
    args = ap.parse_args()
    d = collect()
    print(render_md(d) if args.format == "md" else render_plain(d))


if __name__ == "__main__":
    main()
