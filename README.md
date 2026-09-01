# Token 成本控制

<p align="center">
  <img src="logo.png" alt="Token Cost Control Logo" width="128" height="128">
</p>

<p align="center">
  <img src="https://count.getloli.com/get/@astrbot_plugin_cost_control?theme=moebooru" alt="Visitor Count">
</p>

**让每一次 LLM 调用都有账可查、有价可算、有额度可控** —— 面向 AstrBot 的 Token 用量、模型成本与预算治理插件。

你的 Bot 能回答这些问题吗？

- 今天和本月分别调用了多少次模型、消耗了多少 Token、花了多少钱？
- 哪个会话、用户或模型正在快速吃掉预算？
- Prompt Cache 为什么突然失效，是上下文、System Prompt 还是工具列表发生了变化？
- 一次请求里的 System、Tools、History 和 User 各占多少上下文？
- 昂贵模型超限后，能否自动拦截或切换到更便宜的备用 Provider？
- 不同平台、服务等级、上下文阶梯和峰谷时段的价格应该怎样准确计算？

Token 成本控制通过 **用量采集 + 多源定价 + 五维预算 + 缓存诊断 + 上下文归因** 完成这一整套闭环。

## 它是如何工作的

```text
用户消息
   │
   ├── LLM 请求前（最高优先级）
   │     ├── 匹配会话 / 用户 / Provider 局部规则
   │     ├── 检查五维 Token 与花费预算
   │     └── 超限处理
   │           ├── stop：发送提示并拦截原请求
   │           ├── fallback：改用备用 Provider
   │           └── warn：只告警，继续原请求
   │
   ├── 其他插件继续加工请求
   │
   ├── LLM 请求前（最低优先级）
   │     ├── 对比请求前后上下文，完成注入归因
   │     └── 检测缓存破坏事件
   │
   └── Provider 返回响应
         ├── 复用 AstrBot ProviderStat 用量记录
         ├── 补充缓存、归因、用户与计费上下文
         ├── 按生效定价计算成本
         └── 汇总到面板、报表、预算与告警
```

插件在请求进入 Provider **之前**检查预算，在响应返回 **之后**补全实际用量。所有核心钩子均有异常兜底：某个统计或诊断环节失败时只记录日志，不应拖垮 AstrBot 的正常对话链路。

## 核心功能

### 五维预算护栏

每个维度都能同时设置 Token 上限与花费上限，任一上限到达即按配置的策略处理。

| 维度 | 配置键 | 适合场景 |
|------|--------|----------|
| 单会话每日 | `per_session_daily` | 防止单个群聊或私聊持续刷量 |
| 单用户每日 | `per_user_daily` | 按发送者跨会话聚合，限制特定用户 |
| 单模型每日 | `per_model_daily` | 单独约束高价模型 |
| 全局每日 | `global_daily` | 设置每天的账单护栏 |
| 全局每月 | `global_monthly` | 设置最终月度上限 |

预算不只会“提醒”：

| 动作 | 配置值 | 行为 |
|------|--------|------|
| 硬拦截 | `stop` | 发送超限提示并停止原 Provider 请求 |
| 切换备用 | `fallback` | 按顺序尝试备用 Provider；全部失败时降级为硬拦截 |
| 仅警告 | `warn` | 发送告警，但不阻断本次请求 |

除了全局五维默认值，还可以按 **会话 UMO、用户 ID、Provider ID** 添加局部规则。局部规则按顺序匹配，第一条命中的规则优先于全局预算，且可以使用自己的限额、动作、拦截文案和备用 Provider 列表。

### 多源模型定价

插件支持五种计费模式：

| 模式 | 计费方式 | 典型场景 |
|------|----------|----------|
| `per_token` | 输入、缓存命中、输出、缓存写入分别按百万 Token 计价 | OpenAI、Anthropic 等常规定价 |
| `per_turn` | 每次 LLM 调用固定价格 | 按调用次数收费的服务 |
| `per_request` | 每次用户请求固定价格 | 一次请求内可能包含多轮 Function Calling |
| `per_tier` | 基础价格 + 上下文阶梯 + 服务等级倍率 | 长上下文或 Priority/Fast 服务 |
| `tiered_expr` | 使用受限表达式动态计算价格 | New API 兼容的复杂倍率规则 |

价格可以来自：

- 内置常用模型价格表；
- [models.dev](https://models.dev/)；
- [LiteLLM](https://github.com/BerriAI/litellm)；
- [OpenRouter](https://openrouter.ai/)；
- AstrBot 中已经配置的 New API Provider；
- 你在面板中维护的 Provider 或 Provider + Model 手工价格。

基础价格按以下优先级解析：

```text
模型级手工价
   → 已确认的价格源候选
   → Provider 级手工价
   → 唯一高置信自动候选
   → 内置模型价格
   → 未定价（按 0 显示并在面板告警）
```

基础规则确定后，还可以继续叠加 **峰谷分时策略** 与 **Provider Source 供应商倍率**。主货币支持切换，插件会使用汇率表统一换算后再展示和比较预算。

> `per_request` 在补充明细与按用户统计中可按 `request_id` 精确计数；AstrBot 原生 `ProviderStat` 没有该字段，因此总览、全局预算和日报中的聚合值会按 `per_turn` 近似。

### 峰谷分时定价

分时策略独立叠加在已有基础价格之上，不会复制或覆盖原始定价。你可以按 IANA 时区、星期和时间段：

- 对基础规则乘 `0–100` 倍率，`0` 表示该时段免费；
- 临时切换为另一条完整的 `per_token` / `per_turn` / `per_request` / `per_tier` / `tiered_expr` 规则；
- 为整个 Provider 配置策略，或用 `provider_id|model` 精确到单个模型；
- 配置跨午夜时段，例如周一 `23:00–07:00`。

时间段采用左闭右开区间：`09:00–18:00` 包含 09:00、不包含 18:00。同一策略中的已启用时段不允许重叠，保存时会直接返回校验错误。

### Prompt Cache 破坏诊断

缓存命中率下降往往比模型涨价更隐蔽。插件会对相邻请求的上下文签名进行比较，定位四类常见问题：

| 事件 | 检测内容 |
|------|----------|
| 上下文重置 | 历史消息突然缩短、清空或发生大幅变化 |
| System Prompt 变更 | 系统提示词内容发生变化 |
| Tools 变更 | Function Calling 工具定义增删或变化 |
| 消息顺序漂移 | 历史消息顺序与上一轮不一致 |

面板会展示缓存命中率趋势、最近事件和差异内容；也可以设置命中率阈值，在低于阈值时向当前会话主动告警。

### 上下文成本归因

插件在所有 LLM 请求钩子的最前与最后分别拍摄快照，估算最终上下文及其他插件的注入量：

- `system`：系统提示词；
- `tools`：工具定义；
- `history`：历史对话；
- `user`：当前用户输入；
- `extra`：其他额外上下文；
- `injected_total`：本轮插件链累计注入量。

这能帮助你判断成本增长究竟来自对话历史、工具数量、System Prompt，还是某个上下文增强插件。

> 归因值基于字符与消息结构估算，用于趋势分析和优化定位，不等同于 Provider 最终账单 Tokenizer 的精确结果。

### Web 管理面板

插件内置 AstrBot Plugin Page，配置修改自动保存并热生效，无需重载插件。

| 页面 | 功能 |
|------|------|
| 总览 | 日 / 周 / 月调用次数、Token、成本、缓存命中率、Top 模型与会话、AI 成本诊断 |
| 明细 | 每次 LLM 调用的模型、Token 构成、缓存字段、耗时、成本和归因记录 |
| 预算 | 五维 Token + 花费预算、局部规则、备用 Provider 库与超限动作 |
| 缓存 | 缓存命中率、四类破坏事件与上下文差异 |
| 上下文 | System / Tools / History / User / Extra 的占比与注入量 |
| 定价 | 供应商聚类、多源价格同步、候选比价、手工价格、倍率与峰谷分时策略 |
| 设置 | 总开关、主货币、日报、告警、归因、AI 诊断、数据清理等 |

### AI 成本诊断与定时报表

- 一键收集近 7 天成本、用量、缓存、归因、预算与未定价信息，交给指定 LLM 生成诊断结论；
- 支持日 / 周 / 月综合报表；
- 支持每天定时向多个会话推送成本日报；
- 支持按 Cron 定时同步模型价格目录；
- 自动清理超过保留天数的补充记录；
- AI 诊断结果会在本地缓存，减少重复调用。

### 其他能力

- **多货币展示**：内置静态汇率表，可从公开汇率接口手动同步；
- **未定价告警**：有用量但没有匹配价格的 Provider 会在面板醒目标记；
- **历史成本回填**：插件升级后可为缺失成本的旧记录补算；
- **Provider 残留治理**：识别已经从 AstrBot 配置删除、但仍保留历史用量或旧定价的 Provider；
- **数据清理**：可分别清理补充记录、缓存事件、原生用量记录与 AI 诊断缓存；
- **零额外运行时依赖**：复用 AstrBot 已有的 HTTP、数据库与 Web 运行环境。

## 安装

### 前置要求

| 项目 | 要求 |
|------|------|
| AstrBot | `>= 4.24.2` |
| Python | `>= 3.12` |
| 额外运行时依赖 | 无 |

### 安装步骤

1. 在 AstrBot WebUI 的「插件管理 → 插件市场」中搜索 **Token成本控制** 并安装；
2. 或点击插件管理右下角 `+`，选择从链接安装并填写：

   ```text
   https://github.com/leafliber/astrbot_plugin_cost_control
   ```

3. 也可以将本仓库目录放入 AstrBot 的 `data/plugins/`；
4. 重启 AstrBot，或在插件管理中重载本插件。

### 安装后建议

- 先在「定价」页确认正在使用的模型已匹配价格；未定价的调用会暂按 0 计算；
- 第一次使用建议把超限动作设为 `warn`，观察几天真实用量后再切换到 `stop` 或 `fallback`；
- 如需日报，在目标会话发送 AstrBot 内置命令 `/sid` 获取 UMO，再填入「设置 → 每日日报 → 日报接收方」；
- 使用 `fallback` 前先配置至少一个可用的备用 Provider，并实际验证它能正常回复。

### 验证安装

安装并产生至少一次 LLM 对话后，发送：

```text
/cost          ← 查看当前会话今日用量和成本
/budget        ← 查看预算配置与当前超限状态
/cache         ← 查看当前会话缓存命中率与破坏事件
/attribution   ← 查看最近一次请求的上下文归因
```

同时打开 AstrBot WebUI 左侧的「成本控制面板」。能够看到调用次数、Token 或明细记录，即表示采集和面板链路正常。

## 快速开始

适合第一次安装的最小配置：

1. 打开「定价」页，确认常用 Provider 已显示内置价、价格源候选或自定义价；
2. 打开「预算」页，在「全局每日」填写一个略高于预期的花费上限；
3. 将「全局默认超限处理」设为「仅警告」；
4. 保持缓存诊断与上下文归因开启；
5. 正常使用一段时间，根据「总览」和「明细」中的真实数据调整预算；
6. 确认阈值合理后，再改为「硬拦截」或配置备用 Provider。

## 管理指令

| 命令 | 说明 |
|------|------|
| `/cost` | 当前会话今日调用次数、Token 构成、总成本与 Top 模型 |
| `/budget` | 查看已配置预算、局部规则与当前超限状态 |
| `/cache` | 查看当前会话最近缓存命中率与缓存破坏事件 |
| `/report` | 生成当日综合报表，等价于 `/report daily` |
| `/report weekly` | 生成近 7 天综合报表 |
| `/report monthly` | 生成近 30 天综合报表 |
| `/attribution` | 查看当前会话最近一次请求的上下文归因 |

命令的可用范围与权限可以在 AstrBot 的命令管理中继续调整。

## 配置

### 配置存储

AstrBot 的 `_conf_schema.json` 只保存插件总开关。其余详细配置位于本插件数据目录的 `config.json`，由面板读取和写入；这样可以避免复杂嵌套配置被 AstrBot Schema 裁剪。

> 建议优先通过 Web 面板修改。面板会做字段归一化和规则校验，手工编辑 JSON 时请先备份。

### 推荐配置

| 配置项 | 建议值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 启用插件 |
| `refresh_time` | `"00:00"` | 本地时区的日窗口起算时刻 |
| `currency_symbol` | `"USD"` | 成本展示与预算比较的主货币 |
| `budgets_cost.global_daily` | 按实际情况 | 第一次可设置宽松一些 |
| `default_on_exceeded` | `"warn"` → `"stop"` | 先观察，再启用硬护栏 |
| `cache_diag.*` | 保持默认开启 | 用于发现缓存破坏原因 |
| `attribution.enabled` | `true` | 开启上下文成本归因 |
| `attribution.sample_rate` | `100` | 请求量很大时可调低 |
| `schedule.retain_days` | `90` | 自动清理旧补充记录 |
| `price_sync.auto_enabled` | `false` | 默认手动同步，确认网络可用后再开启定时同步 |

### 完整配置参考

<details>
<summary>展开查看顶层配置</summary>

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 插件总开关 |
| `platforms` | `[]` | 生效平台列表；空表示全部平台 |
| `currency_symbol` | `USD` | 主货币；历史 `$` 值会兼容为 USD |
| `exchange_rates` | 内置静态表 | 以 USD 为锚的汇率表 |
| `exchange_rates_updated_at` | `""` | 最近一次汇率同步时间 |
| `budgets` | 五维均为 `0` | Token 上限，0 表示不限 |
| `budgets_cost` | 五维均为 `0` | 花费上限，0 表示不限 |
| `budgets_cost_currency` | `{}` | 各维花费预算的币种；空则使用主货币 |
| `budget_overrides` | `[]` | 会话 / 用户 / Provider 局部规则 |
| `fallback_providers` | `[]` | 全局备用 Provider 库 |
| `default_on_exceeded` | `"stop"` | 全局默认超限动作 |
| `refresh_time` | `"00:00"` | 每日预算与日报窗口起点 |
| `pricing` | `{}` | Provider 或 Provider + Model 手工定价 |
| `pricing_schedules` | `{}` | 峰谷分时定价策略 |
| `pricing_multipliers` | `{}` | Provider Source 倍率，范围 0–100 |
| `price_sources` | 三个公共源启用 | models.dev / LiteLLM / OpenRouter 开关及 New API 动态源 |
| `price_selections` | `{}` | 已确认的模型价格候选 |
| `price_sync` | `{"auto_enabled": false, "cron": "0 4 * * *"}` | 价格目录定时同步 |
| `alerts` | 见下表 | 告警开关、冷却与日报接收方 |
| `cache_diag` | 见下表 | 缓存诊断与命中率告警 |
| `attribution` | `{"enabled": true, "sample_rate": 100}` | 上下文归因开关与采样率 |
| `ai_diag_provider_id` | `""` | AI 诊断 Provider；空则使用默认 Provider |
| `schedule` | `{"enable_daily_report": false, "retain_days": 90}` | 日报和数据保留设置 |

</details>

<details>
<summary>展开查看告警、缓存与日报配置</summary>

**告警与日报**

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `alerts.enabled` | `true` | 启用超预算主动推送；关闭不影响拦截策略 |
| `alerts.cooldown_seconds` | `300` | 同类告警冷却秒数 |
| `alerts.daily_report_time` | `"09:00"` | 日报推送时间 |
| `alerts.daily_report_to` | `[]` | 日报接收会话 UMO 列表 |
| `schedule.enable_daily_report` | `false` | 是否注册每日日报任务 |
| `schedule.retain_days` | `90` | 补充记录保留天数；0 表示不自动清理 |

**缓存诊断**

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `cache_diag.detect_context_reset` | `true` | 检测上下文重置 |
| `cache_diag.detect_system_prompt_change` | `true` | 检测 System Prompt 变化 |
| `cache_diag.detect_tools_change` | `true` | 检测工具定义变化 |
| `cache_diag.detect_order_drift` | `true` | 检测消息顺序漂移 |
| `cache_diag.cache_hit_rate_alert_enabled` | `false` | 是否开启低命中率主动告警 |
| `cache_diag.cache_hit_rate_alert_threshold` | `0` | 命中率阈值百分比；0 表示不告警 |

</details>

### 局部预算规则

一条局部规则的典型结构：

```json
{
  "enabled": true,
  "target_type": "user",
  "target_value": "123456789",
  "token_limit": 50000,
  "cost_limit": 1.0,
  "cost_currency": "USD",
  "on_exceeded": "fallback",
  "stop_message": "今日额度已用完",
  "fallback_provider_ids": ["cheap-provider"],
  "fallback_token_limit": 8000
}
```

`target_type` 支持：

- `umo`：匹配完整会话标识；
- `user`：匹配发送者 ID，并跨会话聚合；
- `provider`：匹配 AstrBot Provider ID。

同一规则同时配置 Token 和花费上限时，任一条件达到都会触发。规则按面板中的顺序匹配，第一条命中后不再继续评估其他局部规则或全局五维预算。

### 分时定价示例

```json
{
  "pricing_schedules": {
    "openai-prod": {
      "enabled": true,
      "timezone": "Asia/Shanghai",
      "periods": [
        {
          "id": "weekday_peak",
          "name": "工作日峰时",
          "enabled": true,
          "weekdays": [1, 2, 3, 4, 5],
          "all_day": false,
          "start": "09:00",
          "end": "18:00",
          "adjustment": {
            "type": "multiplier",
            "value": 1.25
          }
        },
        {
          "id": "night_valley",
          "name": "夜间谷时",
          "enabled": true,
          "weekdays": [1, 2, 3, 4, 5, 6, 7],
          "all_day": false,
          "start": "23:00",
          "end": "07:00",
          "adjustment": {
            "type": "override",
            "rule": {
              "mode": "per_turn",
              "price": 0.002,
              "currency": "USD"
            }
          }
        }
      ]
    }
  }
}
```

星期使用 ISO 口径：周一为 1，周日为 7。实际使用时建议直接在「定价」页面展开 Provider 卡片进行编辑。

## FAQ

### Q1：面板里的成本为什么是 0？

最常见的原因是当前 Provider / Model 没有匹配到定价。打开「定价」页查看“未定价告警”，然后选择价格源候选或添加手工价格。未定价调用仍会记录 Token，但成本暂按 0 计算。

### Q2：插件计算的成本能代替供应商账单吗？

不能。插件依据 AstrBot 上报的 Token、你选择的价格规则、汇率和倍率进行估算，适合预算控制与趋势分析。供应商可能还有最小计费单位、批处理优惠、税费、赠送额度或未公开规则，最终金额应以供应商账单为准。

### Q3：已经配置预算，为什么刚好越过阈值的那一次请求仍然执行了？

预算在请求发出前根据**已产生的历史用量**检查。某次请求本身把累计值从阈值以下推到阈值以上时，该次请求已经完成；下一次请求会被识别为超限。建议为单次波动预留安全余量。

### Q4：备用 Provider 全部不可用时会怎样？

`fallback` 会按规则中的 Provider 顺序逐个尝试。全部失败或没有配置备用项时，会自动降级为 `stop`，避免已经超限的原高价 Provider 继续执行。

### Q5：为什么 `/cache` 没有命中率数据？

需要 Provider 返回缓存相关 Usage 字段，并且当前会话已经产生 LLM 请求。部分 Provider 不提供 `cache_read` / `cache_creation` 等字段，此时插件只能记录普通输入与输出 Token，无法计算可靠的缓存命中率。

### Q6：为什么 `/attribution` 显示暂无数据？

请确认 `attribution.enabled = true`、采样率大于 0，并在当前会话先完成一次 LLM 对话。归因结果按会话保存最近一次采样；命令本身不会主动触发一次模型请求。

### Q7：价格同步失败怎么办？

公共价格源需要 AstrBot 所在环境能够访问外网。同步失败时插件会保留旧目录，不会清空已有价格。你也可以关闭自动同步，改用内置价格或手工定价。

### Q8：数据会上传到哪里？

用量补充记录、缓存事件、价格目录、配置和 AI 诊断缓存默认保存在 AstrBot 本地数据目录。只有主动同步价格 / 汇率，或运行 AI 成本诊断时，才会访问对应外部服务或你选择的 LLM Provider。

### Q9：如何彻底清理统计数据？

在「设置」页的数据清理区域选择要删除的模块，可分别清理补充采集记录、缓存破坏事件、AstrBot 原生 `ProviderStat` 用量记录和 AI 诊断缓存。清理原生用量记录会影响 AstrBot 其他统计视图，请谨慎操作。

## 本地开发

项目使用 [uv](https://docs.astral.sh/uv/) 管理 Python 开发环境：

```bash
uv sync
uv run ruff format .
uv run ruff check .
uv run pytest
uv run mypy cost_control
```

Plugin Page 前端使用 React + Vite + TypeScript，源码位于 `frontend/`，构建产物输出到 `pages/dashboard/`：

```bash
cd frontend
npm install
npm run dev
npm run typecheck
npm run build
```

本地调试时，将仓库目录软链接到 AstrBot 的 `data/plugins/astrbot_plugin_cost_control`，通过 WebUI 重载插件即可。独立虚拟环境通常不包含 AstrBot 宿主，因此完整运行验证需要在真实 AstrBot 环境中完成。

## 链接

- [AstrBot 主项目](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [更新日志](./CHANGELOG.md)

## 反馈

问题与建议欢迎提交 [Issue](https://github.com/leafliber/astrbot_plugin_cost_control/issues) 或 Pull Request。
