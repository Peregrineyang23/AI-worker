#!/usr/bin/env python3
"""
WorkBuddy 桥接回复引擎 — 安全 I/O 辅助脚本

职责（只做机械活，不做智能）：
  1. 读取 bridge_pending_tasks.json，找出"未认领 + 在时效内 + 等待最终回复生成"的任务
  2. 把 WorkBuddy 生成的 Card 2.0 草稿写入 bridge_reply_drafts.json（status=pending_review）
  3. 用 lark-cli 把预览卡 + 提示发到私有测试会话（PRIVATE_TEST_CHAT_ID）
  4. 维护认领集合（幂等）与发送熔断状态

设计约束（来自 BRIDGE_POLICY_TRAINING_BASELINE.md）：
  - 绝不向正式群直接发消息，只发预览到私有测试会话，由用户回 "确认发送 <draft_id>" 触发 bridge.py 既有发布流程
  - 以 message_id 为幂等键
  - 发送连续失败 -> 熔断出站，仅本地留草稿
  - 过期任务（超过 MAX_BACKLOG_SECONDS）作废，不回放

本脚本不修改 bridge.py，复用其 draft_id 约定与确认发布链路。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

BASE_DIR = os.environ.get(
    "HMI_BRIDGE_BASE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
LARK = os.environ.get("HMI_LARK_CLI", "/Users/frankyang/.npm-global/bin/lark-cli")
PRIVATE_TEST_CHAT_ID = "oc_9e756fe1e59b5b0efa8795c438f0ae45"
MAX_BACKLOG_SECONDS = int(os.environ.get("HMI_BRIDGE_MAX_BACKLOG_SECONDS", "3600"))
REGISTERED_STATUS = "等待最终回复生成"

PENDING_FILE = os.path.join(BASE_DIR, "bridge_pending_tasks.json")
DRAFTS_FILE = os.path.join(BASE_DIR, "bridge_reply_drafts.json")
CLAIMED_FILE = os.path.join(BASE_DIR, "bridge_workbuddy_claimed.json")
CIRCUIT_FILE = os.path.join(BASE_DIR, "bridge_workbuddy_circuit.json")

CIRCUIT_THRESHOLD = 3  # 连续失败次数达到即熔断


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def draft_id_for(message_id):
    return hashlib.sha1(message_id.encode("utf-8")).hexdigest()[:8]


def idem_key(seed):
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"hmi{digest}{int(time.time())}"


def load_claimed():
    return set(load_json(CLAIMED_FILE, []))


def save_claimed(claimed):
    save_json(CLAIMED_FILE, sorted(claimed))


def load_circuit():
    return load_json(CIRCUIT_FILE, {"failures": 0, "last_failure": 0})


def save_circuit(state):
    save_json(CIRCUIT_FILE, state)


def circuit_open():
    return load_circuit().get("failures", 0) >= CIRCUIT_THRESHOLD


def record_send_failure(detail):
    state = load_circuit()
    state["failures"] = state.get("failures", 0) + 1
    state["last_failure"] = int(time.time())
    state["last_detail"] = str(detail)[:300]
    save_circuit(state)


def record_send_success():
    state = load_circuit()
    if state.get("failures", 0) != 0:
        state["failures"] = 0
        state["last_detail"] = None
        save_circuit(state)


def run_lark_cli(args):
    completed = subprocess.run(
        [LARK, *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "lark-cli failed")
    return completed.stdout


def send_card(chat_id, card, seed):
    return run_lark_cli([
        "im", "+messages-send",
        "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", json.dumps(card, ensure_ascii=False, separators=(",", ":")),
        "--idempotency-key", idem_key(seed),
    ])


def send_text(chat_id, text, seed):
    return run_lark_cli([
        "im", "+messages-send",
        "--as", "bot",
        "--chat-id", chat_id,
        "--text", text,
        "--idempotency-key", idem_key(seed),
    ])


def cmd_next(exclude=None):
    exclude = exclude or set()
    tasks = load_json(PENDING_FILE, {})
    claimed = load_claimed()
    now = time.time()
    candidates = []
    for message_id, task in tasks.items():
        if message_id in claimed:
            continue
        if message_id in exclude:
            continue
        if task.get("status") != REGISTERED_STATUS:
            continue
        created = float(task.get("created_at", 0) or 0)
        age = now - created
        if age > MAX_BACKLOG_SECONDS:
            continue
        candidates.append((message_id, task, age))
    # 最老的优先，保证顺序稳定
    candidates.sort(key=lambda c: c[2], reverse=True)
    pending_total = sum(1 for t in tasks.values() if t.get("status") == REGISTERED_STATUS)
    if not candidates:
        print(json.dumps({
            "task": None,
            "pending_total": pending_total,
            "unclaimed_fresh": 0,
            "claimed": len(claimed),
            "circuit_open": circuit_open(),
        }, ensure_ascii=False))
        return
    message_id, task, age = candidates[0]
    print(json.dumps({
        "task": {
            "message_id": message_id,
            "chat_id": task.get("chat_id", ""),
            "command": task.get("command", ""),
            "request": task.get("request", ""),
            "created_at": task.get("created_at"),
            "age_seconds": int(age),
        },
        "pending_total": pending_total,
        "unclaimed_fresh": len(candidates),
        "claimed": len(claimed),
        "circuit_open": circuit_open(),
    }, ensure_ascii=False))


def cmd_commit(message_id, chat_id, card_file):
    # 读取并校验卡片
    try:
        with open(card_file, "r", encoding="utf-8") as f:
            card = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": f"card 文件无效: {e}"}, ensure_ascii=False))
        return 1

    if not isinstance(card, dict) or "header" not in card or "body" not in card:
        print(json.dumps({"ok": False, "error": "card 缺少 header/body，不是合法 interactive card"}, ensure_ascii=False))
        return 1

    did = draft_id_for(message_id)
    drafts = load_json(DRAFTS_FILE, {})
    drafts[did] = {
        "draft_id": did,
        "source_message_id": message_id,
        "target_chat_id": chat_id,
        "created_at": time.time(),
        "card": card,
        "status": "pending_review",
        "engine": "workbuddy",
    }
    save_json(DRAFTS_FILE, drafts)

    if circuit_open():
        # 熔断：仅留本地草稿，不发预览
        claimed = load_claimed()
        claimed.add(message_id)
        save_claimed(claimed)
        update_pending_status(message_id, "已生成草稿（熔断：预览未发送，待恢复）")
        print(json.dumps({
            "ok": True,
            "sent": False,
            "reason": "circuit_open",
            "draft_id": did,
            "note": "发送连续失败已熔断，草稿已本地留存，恢复后需人工处理或重跑。",
        }, ensure_ascii=False))
        return 0

    try:
        # 1) 预览卡发到私有测试会话
        send_card(PRIVATE_TEST_CHAT_ID, card, f"{did}-preview")
        # 2) 发送确认提示
        send_text(
            PRIVATE_TEST_CHAT_ID,
            f"结构化卡片预览已生成（WorkBuddy）。确认无误后回复：确认发送 {did}",
            f"{did}-note",
        )
        record_send_success()
        claimed = load_claimed()
        claimed.add(message_id)
        save_claimed(claimed)
        update_pending_status(message_id, "WorkBuddy已生成草稿待确认")
        print(json.dumps({
            "ok": True,
            "sent": True,
            "draft_id": did,
            "preview_chat": PRIVATE_TEST_CHAT_ID,
            "note": f"预览已发到私有测试会话，请回复 '确认发送 {did}' 发布到原群。",
        }, ensure_ascii=False))
        return 0
    except Exception as e:
        record_send_failure(e)
        print(json.dumps({
            "ok": True,
            "sent": False,
            "reason": "send_error",
            "draft_id": did,
            "error": str(e)[:300],
            "note": "草稿已本地留存，但预览发送失败，已计入熔断计数。",
        }, ensure_ascii=False))
        return 0


def update_pending_status(message_id, status):
    tasks = load_json(PENDING_FILE, {})
    if message_id in tasks:
        tasks[message_id]["status"] = status
        save_json(PENDING_FILE, tasks)


def cmd_status():
    tasks = load_json(PENDING_FILE, {})
    claimed = load_claimed()
    circuit = load_circuit()
    by_status = {}
    for t in tasks.values():
        by_status[t.get("status", "unknown")] = by_status.get(t.get("status", "unknown"), 0) + 1
    print(json.dumps({
        "pending_total": len(tasks),
        "by_status": by_status,
        "claimed": len(claimed),
        "circuit_failures": circuit.get("failures", 0),
        "circuit_open": circuit_open(),
        "max_backlog_seconds": MAX_BACKLOG_SECONDS,
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="WorkBuddy 桥接回复引擎辅助脚本")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_next = sub.add_parser("next", help="取出一个待处理任务（只读）")
    p_next.add_argument("--exclude", default="", help="逗号分隔的 message_id，跳过这些任务")
    p_commit = sub.add_parser("commit", help="写入草稿并发送预览")
    p_commit.add_argument("--message-id", required=True)
    p_commit.add_argument("--chat-id", required=True)
    p_commit.add_argument("--card-file", required=True)
    sub.add_parser("status", help="打印队列与熔断状态")
    args = parser.parse_args()

    if args.cmd == "next":
        cmd_next(set(filter(None, (args.exclude or "").split(","))))
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "commit":
        sys.exit(cmd_commit(args.message_id, args.chat_id, args.card_file))


if __name__ == "__main__":
    main()
