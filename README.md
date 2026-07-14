# AI-worker · HMI 飞书 CLI 桥接

把飞书（Lark）群聊里的 `@帆½` 消息，桥接成「本地网关 + 大模型大脑」的自动回复方案。
本仓库是 **2026-07-14** 从 Codex 迁移到 WorkBuddy 后的版本化管理沉淀。

---

## 1. 为什么做这次迁移

之前用 **Codex** 作为飞书 CLI 桥接的回复引擎，但 Codex 依赖梯子 / VPN 才能联网，
导致监听飞书对话时**反馈不及时**。现改为以 **WorkBuddy** 作为大脑（不依赖梯子），
**DeepSeek API** 作为文本兜底，本地网关只做「耳朵 + 手」。

---

## 2. 架构

```
飞书群聊
   │  @帆½ 消息
   ▼
┌─────────────────────────────────────────────┐
│  Gateway  bridge.py  (本地, 不依赖梯子)        │
│   · 监听 lark-cli 事件 + 轮询兜底              │
│   · 加 OK 表情、登记任务、发预览到私聊          │
│   · 收到「确认发送 <draft_id>」→ 发布到原消息   │
│   · 不调用任何大模型                           │
└─────────────────────────────────────────────┘
   │  任务(等待最终回复生成)
   ├──────────────┬──────────────────────────┐
   ▼              ▼                          ▼
DeepSeek       WorkBuddy 自动化           (失败时)
文本兜底        每小时生成回复              DeepSeek/人工
(wb_bridge_    (文本 + 多模态)             (交回兜底)
 fastpoll.py)   (大脑)
 每 120s
```

| 角色 | 组件 | 说明 |
|------|------|------|
| 耳朵+手（网关） | `bridge/bridge.py` | 本地直连 `open.feishu.cn`，不调 LLM、不依赖梯子 |
| 大脑 | WorkBuddy 自动化 | 每小时生成回复内容（文本 + 多模态） |
| 文本兜底 | `bridge/wb_bridge_fastpoll.py` | 每 120s 用 DeepSeek 生成文本，多模态交 WorkBuddy |
| 飞书 CLI | `lark-cli` | bot app `cli_aa96499f7ce29cbd`（帆½ / 杨帆的飞书 CLI） |

---

## 3. 触发与路由规则

- **仅当消息 `@帆½`（结构化 mention）才登记任务并触发回复** —— 代码级强制
  （`bridge.py` 的 `is_bot_mentioned` 只认结构化 mentions；非 @ 消息直接跳过）。
- **第一优先级：及时响应**。文本回复走 DeepSeek 兜底（≤120s），不阻塞等待大脑。
- 机器人自身发言、非白名单群消息均静默跳过。

---

## 4. 监听白名单（ALLOWED_CHAT_IDS）

`bridge.py` 只对以下群做监听与处理，未授权群静默跳过：

| 群 | chat_id |
|----|---------|
| fufu 001 主群 | `oc_98ba0dac9941993769d2b8908dcce3a0` |
| 私聊测试 | `oc_9e756fe1e59b5b0efa8795c438f0ae45` |
| DM01 认知对齐群 | `oc_d87083ba011523de6b62119cd7137284` |
| AIOS PETS 群 | `oc_5e5cd2c549b5e4325ffcb4e0cbb5fcd1` |
| B76 测试群 | `oc_b76fc129711da7ac28ee7279741cc33f` |

---

## 5. 任务生命周期

1. 网关收到 `@帆½` → 加 OK 表情、登记任务、发**预览卡片**到私聊测试群。
2. 你在私聊回「**确认发送 `<draft_id>`**」→ 网关把草稿发布到原消息。
3. 任务 **10 分钟过期**（`eta_at = created_at + 600s`）；`next` 只认 `等待最终回复生成` 状态。
4. 幂等：同一 `message_id` 的 draft_id 固定为 `sha1(message_id)[:8]`，重复提交覆盖不重复。

> ⚠️ **已知缺口**：多模态任务 10 分钟过期，但 WorkBuddy 自动化粒度最细为**每小时**，
> 多模态请求大概率错过窗口。修法：`HMI_BRIDGE_ESTIMATED_REPLY_SECONDS` 环境变量延至 ~90 分钟
> （改环境变量、不动代码、重启一次网关即可）。**待拍板。**

---

## 6. 安全约定

- **DeepSeek key 绝不入库**：存 `bridge/wb_bridge_llm.json`（`chmod 600`），
  已被 `.gitignore` + `git/info/exclude` 双重忽略。
- **推荐**改用环境变量 `HMI_BRIDGE_LLM_API_KEY`（避免密钥落盘），参考
  `bridge/wb_bridge_llm.json.example`。
- 运行时状态 JSON（`bridge_state.json` 等）含聊天内容 / PII，**已加入 `.gitignore`**。

---

## 7. 文件说明

```
AI-worker/
├── README.md                         # 本文件（架构与决策提炼）
├── .gitignore                        # 密钥 / 运行时状态 / 日志 不提交
├── bridge/
│   ├── bridge.py                     # 网关：监听/登记/预览/发布
│   ├── wb_bridge_reply.py            # 辅助：next / commit / status
│   ├── wb_bridge_fastpoll.py         # DeepSeek 文本兜底轮询（每 120s）
│   └── wb_bridge_llm.json.example    # 密钥模板（无真实 key）
├── deploy/
│   ├── com.yousandi.hmi-ai-workers-bridge.plist   # 网关 launchd 守护
│   └── com.yousandi.wb-bridge-fastpoll.plist       # 兜底轮询 launchd 守护
└── docs/
    └── 桥接规则_v0.2_草案.md          # 桥接规则（基于 fufu 001 云文档 v0.1 重写）
```

---

## 8. 部署

```bash
# 1. 安装并登录 lark-cli，确认 bot 已加入目标群
# 2. 放置代码
cp -r bridge /path/to/run/
cp deploy/*.plist ~/Library/LaunchAgents/

# 3. 配置密钥（二选一）
#    a) 环境变量（推荐）
export HMI_BRIDGE_LLM_API_KEY="sk-..."
#    b) 落盘文件（权限 600）
cp bridge/wb_bridge_llm.json.example bridge/wb_bridge_llm.json
chmod 600 bridge/wb_bridge_llm.json
#    然后填入真实 key

# 4. 加载守护
launchctl load ~/Library/LaunchAgents/com.yousandi.hmi-ai-workers-bridge.plist
launchctl load ~/Library/LaunchAgents/com.yousandi.wb-bridge-fastpoll.plist

# 5. 在 WorkBuddy 配置每小时自动化（生成回复 + 多模态兜底）
```

---

## 9. 版本历史

- **v0.1** — fufu 001 维护的「多 bot 协作桥接规则」（飞书云文档
  `SoYldEvdEoW5zWxRIGicanSCnQb`），帆½ 当时状态为「待登记」。
- **v0.2（本仓库草案）** — 在 v0.1 基础上登记帆½ 真实能力、明确 @触发与及时响应、
  新增「实践方案」章节（架构 / 白名单 / 安全 / 10 分钟过期缺口）。
  已作为讨论提案提交 fufu 001 主群，待团队评审合入主线。

> 本仓库是对「当前桥接方案」的代码与文档版本化，便于回滚与协作；
> 规则本身的权威来源仍是 fufu 001 云文档。
