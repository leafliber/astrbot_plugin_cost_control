# 成本控制 · AstrBot 插件

> LLM 便宜好用,直到某一天账单失控 —— 有人在某个群里狂刷、缓存莫名失效导致每一轮都全量计费、昂贵模型被随手调用、system prompt 冗长又每轮重算。

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.24.2-blue)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)](https://www.python.org/)

---

## ✨ 它能做什么

- **5 维细粒度预算** —— 会话 / 用户 / 模型 / 全局每日 / 全局每月,token 与美元双限额,任一维度超限即刻生效。
- **超限不只是告警** —— 三种动作可选:**硬拦截**、**自动切换备用 Provider**(便宜模型兜底)、**仅警告**。局部规则可覆盖全局策略。
- **缓存破坏诊断** —— 自动识别 4 类让 prompt cache 失效的元凶:上下文重置、system prompt 变更、工具定义变更、消息顺序漂移。命中率掉了立刻知道为什么。
- **成本归因** —— 每轮请求拆解 system / tools / history / user 各占多少 token,找出谁在悄悄吃掉预算。
- **内置 1000+ 模型定价** —— OpenRouter 全量同步,开箱即用;也支持自定义 per_token / per_turn / per_request 三种计费。
- **全数据可视化面板** —— AstrBot WebUI 内嵌 7 页仪表盘,所见即所得地编辑预算、定价、备用库,改完自动保存、热生效。
- **零额外依赖** —— 复用 AstrBot 自带栈,装上即跑。

---

## 📦 安装

两种安装方式（推荐方式1）：

1. 在Astrbot管理面板，插件-Astrbot插件-右下角+号-从链接安装，复制本页面的链接到对话框安装：
    https://github.com/leafliber/astrbot_plugin_cost_control
2. 把本插件目录直接放入 AstrBot 的 `data/plugins/`

重启 AstrBot,或通过 WebUI「插件管理」重载本插件。**无第三方依赖**,复用 AstrBot 自带环境。

> 最低版本要求:AstrBot ≥ 4.24.2
---

## 🚀 快速开始(3 分钟)

1. **装好插件** → WebUI 左侧出现「成本控制面板」页面。
2. **打开面板** → 「定价」tab 确认你的模型已被内置表覆盖(否则手动加一条)。
3. **设一个每日花费上限** → 「预算」tab → 全局每日花费填 `$1.00` → 处理动作为「硬拦截」。
4. **发起几次对话** → 回到「总览」看用量、成本、缓存命中率实时刷新。

到这一步,你已经有了最基础的成本护栏。下面的「预算控制实战」给出更进阶的组合玩法。

---

## 🖥 可视化面板(WebUI)

WebUI 内嵌 7 个页面,配置改动**自动保存、热生效**,无需重载插件。

| 页面 | 功能 |
| --- | --- |
| **总览** | 今日 / 本月调用次数、token、成本、缓存命中率、Top 模型与会话 |
| **明细** | 每次 LLM 调用的逐条记录(模型、token 构成、缓存字段、耗时、归因) |
| **预算** | 5 维 token + 花费双限额、局部阈值、备用 Provider 库、全局默认处理动作 |
| **缓存** | 命中率趋势、4 类破坏诊断事件、system/tools 差异 diff |
| **上下文** | 每轮请求的 system / tools / history / user 注入量分解 |
| **定价** | 内置模型表浏览 + 自定义 provider 单价(per_token/per_turn/per_request) |
| **设置** | 功能开关、刷新时刻、缓存告警阈值、定时日报、归因采样率等 |

---

## 🎯 预算控制实战(常用组合)

预算系统的核心是两层:**全局 5 维默认** + **局部规则覆盖**(override 优先级更高,命中即短路)。每个维度都能同时设 token 与美元限额,超限时三选一处理:`stop`(拦截)/ `fallback`(切备用)/ `warn`(警告)。

以下配置均在面板「预算」tab 编辑

### 组合 1 · 每日硬上限（最常用）

适合个人或小团队,设一条全局每日花费上限,超了直接拦。

在「预算」tab:

1. 找到顶部「预算总览（5 维全局默认）」表格里的**「全局每日」**行。
2. 在该行**「花费 $」**列的输入框填 `2`（每日限额 $2）。
3. 滚到页面底部「全局默认超限处理」,选**「硬拦截」**。
4. 改动自动保存,立即对后续请求生效。

### 组合 2 · 超限自动切便宜模型(降本，但不中断)

贵模型超预算后,自动把后续请求切到备用便宜模型,用户无感知、服务不中断。

在「预算」tab:

1. 先到中部「备用 Provider 库」,点「+」添加便宜模型,填入 provider id（如 `openrouter/deepseek-chat`）,勾选启用,备注写「便宜兜底」。
2. 回到顶部「预算总览」,在「全局每日」行的**「花费 $」**列填 `5`。
3. 底部「全局默认超限处理」,选**「切换备用 Provider(按备用库顺序)」**。
4. 改动自动保存,立即生效。

> 切换机制:插件在请求前钩子里**直接调用备用 Provider** 复用本轮 prompt(可截断历史),`stop_event` 中止原 Provider,把备用回复发给用户。全失败则降级拦截,绝不让已超限的贵模型继续跑。

### 组合 3 · 给「大量使用的某用户 / 高频会话」单独限流

全局放宽松,但对特定 user_id 或会话单独收紧 —— 防滥用。

在「预算」tab 中部「局部阈值（优先级高于全局）」:

**规则 A · 限制特定用户(先警告):**

1. 点「+」新增一条规则,勾选启用。
2. 类型选**「用户」**,值填发送者 ID（QQ 号 / 微信 ID 等,如 `123456789`）。
3. Token≤`50000`、$≤`0.5`（任一超限即触发）。
4. 处理动作选**「仅警告」**。

**规则 B · 限制特定会话(硬拦截):**

1. 再点「+」新增一条,勾选启用。
2. 类型选**「会话」**,值填会话标识（如 `aibot:group:88888888`）。
3. Token≤`200000`。
4. 处理动作选**「硬拦截」**,在展开的「拦截文案」框填 `本群今日额度已用完,明日重置~`（留空则用默认文案）。
5. 用卡片右侧的 ↑↓ 调整规则先后顺序。

> override 按顺序匹配,**第一条命中即生效**,后续规则与全局 5 维都被短路。`target_type` 支持 `umo`(会话)/ `provider`(指定 Provider)/ `user`(发送者,跨会话聚合)。

### 组合 4 · 仅观察(只告警不拦截)

刚装上,先摸清用量底细,设个预期值但只发警告。

在「预算」tab:

1. 「预算总览」表格「全局每日」行的**「花费 $」**列填 `10`。
2. 底部「全局默认超限处理」,选**「仅警告（不中断）」**。
3. 超限时只发一条警告消息,请求继续走原 Provider,不做拦截。

### 组合 5 · 月度护栏 + 每日提醒

设一个宽松的月度上限做最终底线,配合每日定时日报掌握节奏。

在「预算」tab:

1. 「预算总览」表格「全局每月」行的**「花费 $」**列填 `50`（月度最终底线）。
2. 同表「全局每日」行的**「花费 $」**列填 `3`（日度次级护栏）。
3. 底部「全局默认超限处理」选**「硬拦截」**。

再到**「设置」tab** 配置日报推送:

4. 打开「告警」开关,「每日日报时间」填 `09:00`。
5. 「日报推送目标」添加会话标识（如群号）。

### 5 个维度怎么选

| 维度 | key | 适合场景 |
| --- | --- | --- |
| 单会话每日 | `per_session_daily` | 防单个群 / 单个对话刷量 |
| 单用户每日 | `per_user_daily` | 防特定人跨群刷量(按 user_id 聚合) |
| 单模型每日 | `per_model_daily` | 给昂贵模型单独设卡 |
| 全局每日 | `global_daily` | 日度账单护栏 |
| 全局每月 | `global_monthly` | 月度最终底线 |

> **刷新时刻**:`refresh_time`(默认 `00:00`,本地时区)控制每日窗口的重置点。设 `06:00` 则每日 6 点清零。

---

## ⚙️ 完整配置项

详细配置存于插件 `data` 目录的 `config.json`(不受 AstrBot schema 裁剪影响),由面板「设置」与各功能页编辑。主要项:

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `budgets` | `{各维 0}` | 5 维 token 上限(0 = 不限) |
| `budgets_cost` | `{各维 0.0}` | 5 维美元上限 |
| `budget_overrides` | `[]` | 局部阈值规则(优先级高于全局) |
| `fallback_providers` | `[]` | 备用 Provider 库 |
| `default_on_exceeded` | `"stop"` | 全局默认超限处理 |
| `refresh_time` | `"00:00"` | 每日预算重置时刻(本地时区) |
| `pricing` | `{}` | 自定义 provider 单价(覆盖内置表) |
| `cache_diag` | 见下 | 缓存诊断开关与告警阈值 |
| `alerts` | 见下 | 告警推送、冷却、日报 |
| `prompt_optimizer` | 见下 | prompt 优化器 |
| `attribution` | 见下 | 上下文归因采样 |
| `schedule` | 见下 | 定时任务 |

```jsonc
{
  "cache_diag": {
    "detect_context_reset": true,        // 上下文重置检测
    "detect_system_prompt_change": true, // system prompt 变更
    "detect_tools_change": true,         // 工具定义变更
    "detect_order_drift": true,          // 消息顺序漂移
    "cache_hit_rate_alert_threshold": 0  // 命中率低于此值告警(0=不告警)
  },
  "alerts": {
    "enabled": true,
    "cooldown_seconds": 300,             // 同一目标告警冷却
    "daily_report_time": "09:00",
    "daily_report_to": []                // 日报推送目标
  },
  "prompt_optimizer": {
    "enabled": true,
    "provider_id": "",                   // 改写用的 provider(留空则用默认)
    "max_static_analysis_length": 8000
  },
  "attribution": { "enabled": true, "sample_rate": 100 },
  "schedule": { "enable_daily_report": false, "retain_days": 90 }
}
```

---

## 🔧 本地开发

需要 [uv](https://docs.astral.sh/uv/):

```bash
uv sync                    # 创建 .venv 并安装 dev 依赖(ruff/pytest/mypy)
uv run ruff format .       # 格式化
uv run ruff check .        # lint
uv run pytest              # 测试
uv run mypy cost_control   # 类型检查
```

Plugin Page 前端(React + Vite + TypeScript,源码在 `frontend/`,构建产物到 `pages/dashboard/`):

```bash
cd frontend
npm install               # 首次
npm run dev               # 本地预览(自动注入 mock bridge)
npm run build             # 构建到 ../pages/dashboard/
npm run typecheck
```

调试:本地启动 AstrBot,把本目录软链到 `data/plugins/astrbot_plugin_cost_control`,改完代码通过 WebUI 重载插件即可热加载。

> 本地 `.venv` 不含 astrbot 本体(它是运行宿主),`import main` 会失败 —— 用 `ast.parse` / 静态检查验证语法,运行验证必须在真实 AstrBot 中。

---

## 📐 工作原理速览

- **数据源**:主表为 AstrBot 原生 `ProviderStat`(每次 LLM 调用的 token 与耗时);`on_llm_response` 钩子补充采集缓存字段。
- **拦截点**:`on_llm_request` 最高优先级钩子(`priority=100000`),在所有其他插件之前判定预算。
- **评估顺序**:局部 override 优先(命中即短路)→ 全局 5 维逐维比较。
- **降级原则**:所有钩子、命令、API 都 try/except 兜底,绝不抛未捕获异常拖垮 AstrBot。

更多架构细节见 [CLAUDE.md](./CLAUDE.md)。

## 📝 更新日志

见 [CHANGELOG.md](./CHANGELOG.md)。

## 🤝 反馈

问题与建议欢迎提 [Issue](https://github.com/leafliber/astrbot_plugin_cost_control/issues)。
