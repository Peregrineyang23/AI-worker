#!/usr/bin/env python3
"""每 5 分钟把帆½ 桥接状态推送到飞书（由 launchd 调用）。

读取 bridge_status_report.collect() 的纯文本输出，用 lark-cli 以 bot 身份发到目标群。
目标群通过环境变量 HMI_BRIDGE_STATUS_CHAT_ID 指定（默认用户私聊）。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_status_report as r

CHAT_ID = os.environ.get(
    "HMI_BRIDGE_STATUS_CHAT_ID", "oc_9e756fe1e59b5b0efa8795c438f0ae45"
)
LARK = os.environ.get("LARK_CLI", "/Users/frankyang/.npm-global/bin/lark-cli")


def main():
    text = r.render_plain(r.collect())
    content = json.dumps({"text": text}, ensure_ascii=False)
    cmd = [
        LARK,
        "im",
        "+messages-send",
        "--as",
        "bot",
        "--chat-id",
        CHAT_ID,
        "--msg-type",
        "text",
        "--content",
        content,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr or res.stdout)
        sys.exit(1)
    sys.stdout.write(res.stdout)


if __name__ == "__main__":
    main()
