#!/usr/bin/env python3
"""帆½ 桥接状态监控：读取网关运行时状态文件，输出精简状态卡。

用法:
    python3 bridge_status_report.py
环境变量:
    HMI_BRIDGE_BASE_DIR  状态文件所在目录（默认 ~/Library/Application Support/HMIAIWorkersBridge）
"""
import json
import os
import sys
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


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    state = load("bridge_state.json", {}) or {}
    pending = load("bridge_pending_tasks.json", {}) or {}
    drafts = load("bridge_reply_drafts.json", {}) or {}
    claimed = load("bridge_workbuddy_claimed.json", []) or []
    deferred = load("bridge_workbuddy_deferred.json", []) or {}

    seen_n = len(state.get("seen_message_ids", []))
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

    print(f"# 帆½ 桥接状态 · {now}")
    print()
    print("## 接入")
    print(f"- 已监听消息(seen): **{seen_n}** 条")
    if recent_log:
        print(f"- 最近处理: `{recent_log[-1]}`")
    print()
    print("## 任务队列 (bridge_pending_tasks)")
    if pc:
        for k, v in pc.most_common():
            print(f"- {k}: **{v}**")
    else:
        print("- 无")
    print()
    print("## 草稿 (bridge_reply_drafts)")
    if dc:
        for k, v in dc.most_common():
            print(f"- {k}: **{v}**")
    else:
        print("- 无")
    print()
    print("## WorkBuddy 握手")
    print(f"- 已认领(处理中): **{len(claimed)}** 条")
    print(f"- 推迟: **{len(deferred)}** 条")
    print()
    print("## 最近活动")
    for t in recent_tasks:
        print(
            f"- {rel(t.get('created_at'))} · {short(t.get('message_id', ''))} "
            f"· {t.get('command', '')} · {t.get('status', '')}"
        )
    if recent_log:
        print()
        print("### 网关处理流(bridge.err.log 尾部)")
        for ln in recent_log:
            print(f"- {ln}")
    print()
    print(f"> 数据来源: `{BASE}`")


if __name__ == "__main__":
    main()
