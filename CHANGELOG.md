# Changelog

本文件记录成本控制插件(`astrbot_plugin_cost_control`)的版本变更。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-07-13

**AI 成本诊断**与 **定价页面交互** 的集中打磨版本。补齐诊断免责声明、修复嵌入式 webview 下按钮失效的兼容性 bug,并统一定价匹配徽章的视觉风格。

### 新增

#### AI 成本诊断

- **「仅供参考」免责声明**：诊断面板在空闲态描述下方与结果页脚均加上「诊断说明仅供参考」提示,提醒用户 LLM 输出不应作为自动处理依据,仅用于辅助判断。

#### 定价页面

- **统一定价匹配徽章**：「未定价 / 内置匹配 / 自定义」三种状态统一为圆角徽章形式 (`pricing-badge`),配色按严重度区分:
  - **未定价** — 浅红底 (`--bad` 12% 底),保持现有「有/无用量」两种背景色变体。
  - **内置匹配** — 灰色徽章 (`--text-dim` 12% 底)。
  - **自定义** — 蓝色徽章 (`--accent` 12% 底)。
- **三态视觉对齐**:徽章位置、间距、内边距在三种状态间保持一致,方便用户扫视比对。

### 变更

#### 定价页面

- **自定义定价默认折叠**:拥有自定义定价的 Provider 卡片默认收起,仅展示汇总 (输入/输出 单价 + 货币),与「内置匹配」卡片一致;只有「未定价」卡片默认展开,提示用户需要补价。仅修改初始态,用户点击展开后行为不变。

#### AI 成本诊断

- 描述文字内的免责声明独占一行排版,避免与正文挤在一起造成视觉噪音。

### 修复

- **设置页「清除选中模块」按钮无反应**:嵌入式 webview 中 `window.confirm()` 被拦截或静默返回 `false`,导致 `doPurge` 直接 return。改为**两段式点击确认**(页内交互,不依赖原生弹窗):首次点击按钮文案变为「⚠ 确认清空」并显示 4 秒倒计时提示,4 秒内再次点击执行,超时自动放弃。
- **定价页「重置全部」按钮无反应**:同上根因,一并改造为两段式确认,armed 时按钮切换为红色 `danger` 变体,提升警示性。
- 移除旧的 `.pricing-match` / `.pm-ov` 徽章样式,避免残留类名残留导致新样式被覆盖。

### 工程

- 前端 `npx tsc --noEmit` 与 `vite build` 均通过。

## [0.2.1] - 2026-07-09

增量迭代版本。聚焦 **上下文归因维度细化**、**缓存诊断体验优化** 与 **前端交互打磨**。

### 新增

#### 上下文归因

- **`extra` 维度拆分**：将 `extra_user_content_parts`（插件注入的额外内容块）从 `user` 维度中拆出，单独作为 `extra` 维度统计。`user` 现仅含当前轮 `prompt` 文本 + 图片/音频等媒体块，`extra` 独立估算所有注入块（含文本与图片/音频等非文本块），二者不再合并。
- **非文本块估算覆盖**：新增 `_content_part_tokens()`，覆盖 `text` / `think` / `image_url`（≈85 token）/ `audio_url`（≈200 token）全部块类型；`total` 改为五维之和（system + tools + history + user + extra）。
- **注入差追踪 `extra`**：`pop_injection` 的 `injected` 从 3 维扩展到 4 维，插件注入到 `extra_user_content_parts` 的内容也被捕获。
- **组件占比悬浮提示**：鼠标悬停在图例项上显示各维度（system / tools / history / user / extra）的简介与可能来源说明。
- **计算说明**：上下文归因与缓存诊断各自新增估算说明（标注算法口径并声明基于样本数据估算），展示在占比图表正下方。

#### 缓存诊断

- **优化潜力「优秀」档**：新增命中率 ≥80% 的「优秀」等级（绿色加粗），优秀时不显示排查建议。

#### 设置页

- **汇率折叠栏**：汇率展示改为默认收起的下拉栏，点击「查看 N 个汇率」展开。
- **「高级」面板**：「生效平台」从「总开关与全局」移至独立的「高级」面板，标题旁带「高级」徽标。

### 变更

- **优化潜力分档口径**：从基于 `potentialPct`（成本降低比例）改为基于平均缓存命中率直接判定：≥80% 优秀 / 60–80% 低 / 40–60% 中 / <40% 高。
- **主货币切换全局刷新**：切换主货币后自动重新拉取 config 更新货币代码，并刷新所有页面数据，确保费用显示同步更新（此前其他页面不会更新）。
- **README 安装方式**：新增「通过插件市场安装」并标记为推荐，置于三种安装方式之首。

### 修复

- **切换 tab 页面晃动**：不同页面内容高度差异导致垂直滚动条出现/消失，引起横向布局偏移。通过 `scrollbar-gutter: stable` 为滚动条槽预留固定空间解决。
- **缓存破坏事件变更内容溢出**：变更摘要行 grid 列从 `auto` 改为 `minmax(0,1fr)` 并加 `overflow-wrap: anywhere`；diff 详情文本 `white-space` 从 `pre` 改为 `pre-wrap`，长内容自动换行不再超出对话框。

## [0.2.0] - 2026-07-05

第二个稳定版本。本版本围绕 **多货币结算** 重构了成本计算链路，新增 **首页成本趋势图** 与多币种预算 / 定价 UI，确保最终结算与显示始终对齐到用户选定的主货币。

### 新增

#### 多货币支持（核心）

- **主货币切换**：设置页新增「主货币」下拉，内置 USD / CNY / EUR / GBP / JPY / KRW / INR / HKD / SGD / TWD / RUB / BRL 共 12 种货币。所有费用（预算比较、告警文案、命令输出、UI 显示）统一换算到主货币口径。
- **汇率同步**：从免费 API（`open.er-api.com`，无 key）一键拉取最新汇率并持久化到 `config.json`；默认静态汇率兜底（含 USD）。同步失败不阻断使用，回退到内置表。
- **汇率展示**：设置页新增「汇率同步」面板，展示各币种相对 USD 的当前汇率与最近同步时间；后端返回的 `currency_symbol` / `exchange_rates` / `exchange_rates_updated_at` 字段同步进前端全局状态。
- **预算独立货币**：5 维全局预算（global_daily / global_monthly / session_daily / session_monthly / user_daily）中每一维度的 cost 限额可独立选择货币（`budgets_cost_currency`），存于主货币口径前自动按汇率换算；未设置则跟随主货币。
- **override 独立货币**：每条 override（umo / provider / user）可独立指定 cost 限额货币，比较前换算到主货币；UI 状态条同步显示该条规则的消费与限额。
- **定价条目独立货币**：每个 Provider 的自定义定价条目支持指定货币（per_token / per_turn / per_request），保存到 `price.currency` 字段。
- **记录原始金额固化**：`CostSupplement` 表新增 `cost_amount` + `currency_symbol` 列，记录保存时的原始金额与货币符号；明细行展示按记录原始货币，聚合展示按主货币。新字段 `cost_original` 由后端返给前端。
- **统一金额存储模型**：内置定价统一以 USD 为基准，跨币种运算一律经过汇率换算，不依赖货币符号切换。

#### 首页可视化

- 新增「成本趋势（近 N 天）」面积图，与「用量趋势」并排显示：Y 轴标签使用当前主货币符号，Tooltip 显示 4 位小数精确成本。后端 `api_timeline` 增加 `cost_series` 字段（按 (bucket, model) 粒度核算后聚合到桶）。

#### 后端 API

- `POST /actions/sync_rates`：手动触发汇率同步，立即返回最新汇率表与同步时间。
- 所有成本相关响应（`/overview`、`/budgets`、`/records`、`/records/aggregate`、`/compare`、`/timeline`、`/pricing`、`/alerts`、`/config`）统一主货币口径，响应增加 `currency_symbol` 字段。

### 变更

- **存储列增加 + 幂等迁移**：`cost_supplements` 表新增 `cost_amount` 和 `currency_symbol` 两列；启动时自动 `ALTER TABLE ADD COLUMN`（旧库无脑覆盖，安全；多次启动无副作用）。
- **历史数据回填**：启动时一次性为 `cost_amount IS NULL` 的存量记录补算（按当前 USD 口径定价 + 汇率换算），失败行保持 NULL，由展示层回退重算。
- **`fmtCost` 接口扩展**：前端 `fmtCost(n, symbolOrCode?)` 支持传入货币代码或符号字面量；缺省回退到全局主货币。
- **预算比较全路径换算**：全局 cost 限额、override 限额、`query_user_cost_total` 用户成本，全部先按 `budgets_cost_currency` / `cost_currency` / 主货币 → 主货币换算再比较。

### 修复

- 修正 override cost 限额比较 bug：旧实现以原始货币金额直接与（已换算的）used 比较；现统一在主货币口径下比较。
- 修正 `get_main_currency` 历史 `$` 值兼容：自动归一化为 `USD`。
- 移除首页硬编码 `USD · xxx` 文案。

### 兼容性

- 配置文件通过 `deep_merge(CONFIG_DEFAULTS, ...)` 向后兼容：旧库仅 `enabled` / `refresh_time` 等字段也能正常加载；新增 `currency_symbol` / `exchange_rates` / `budgets_cost_currency` 自动按默认值生效。
- 数据库迁移幂等：已存在列不会被重复添加；`extend_existing` 保证热重载安全。
- 公共 API 字段类型不变（仅为 cost 类型响应增加可选 `currency_symbol` 字段，不影响原有调用方）。

## [0.1.0] - 2026-06-23

首个完整版本。覆盖从数据采集、预算拦截、高级分析到可视化的完整链路。

### 新增

#### 数据采集

- 基于 AstrBot 原生 `ProviderStat` 表的四维 token 用量聚合(会话 / 用户 / 模型 / 全局)。
- `on_llm_response` 钩子补充采集 `TokenUsage` 与 `raw_completion` 中的缓存字段(`cache_read` / `cache_creation`),写入自有补充表 `CostSupplement`。
- 内置 1000+ 模型定价表(OpenRouter 全量同步),支持 `per_token` / `per_turn` / `per_request` 三种计费模式;用户自定义定价可按 provider 覆盖。

#### 预算控制

- **5 维全局预算**:单会话每日 / 单用户每日 / 单模型每日 / 全局每日 / 全局每月,token 与美元双限额。
- **局部阈值(override)**:按 `umo` / `provider` / `user` 三类目标单独限流,优先级高于全局,命中即短路。
- **三种超限处理动作**:
  - `stop` —— 硬拦截(`stop_event` + 文案)。
  - `fallback` —— 自动切换备用 Provider(钩子内直接调用、复用 prompt、可截断历史、usage 归因),全失败降级拦截。
  - `warn` —— 仅发警告,不中断请求。
- **备用 Provider 库**:面板配置,与 `fallback` 动作共享。
- **per_user 跨会话聚合**:从补充表按 `user_id` 聚合真实用户用量(原生表无此字段)。
- 可配置每日刷新时刻 `refresh_time`(本地时区,遵循 AstrBot 主配置时区)。

#### 缓存破坏诊断

- 自动识别四类导致 prompt cache 失效的原因:上下文重置、system prompt 变更、工具定义变更、消息顺序漂移。
- 记录 system / tools 差异 diff,命中率低于阈值时告警。

#### 上下文归因

- 在 `on_llm_request` head / tail 双钩子快照上下文,估算 system / tools / history / user 各部分 token 占比。
- 可配置采样率。

#### 提示词优化

- system prompt 静态分析:长度、token 估算、冗余度、可缓存性评分与建议。
- `/optimize rewrite` 经配置的 Provider 改写并返回精简版。

#### 可视化面板(Plugin Page)

- AstrBot WebUI 内嵌 React 仪表盘,7 个页面:总览 / 明细 / 预算 / 缓存 / 上下文 / 定价 / 设置。
- 预算、定价、备用库所见即所得编辑,自动保存、热生效(无需重载)。
- 跟随 AstrBot 深色模式;bridge 握手轮询兜底;标准 `{success, data}` 信封。

#### 告警与报表

- 主动推送超限告警,带冷却去重。
- 定时日报(`CronJob`,可配置时间与推送目标)。
- 历史数据保留期可配置(默认 90 天)。

#### 聊天命令

- `/cost`、`/budget`、`/optimize [rewrite]`、`/cache`、`/report [daily|weekly|monthly]`、`/attribution`。

#### REST API

- 暴露 `overview` / `records` / `budgets` / `cache` / `attribution` / `pricing` / `config` / `actions` 等端点(需 dashboard JWT),供面板调用。

### 工程

- Mixin 架构:主类组合 `cost_control/` 下 12 个子域 Mixin,职责单一。
- 热重载安全:`CostSupplement` 配置 `extend_existing`;`register_web_api` 幂等;惰性状态字典。
- 全链路异常降级:钩子、命令、API 均捕获异常,绝不拖垮 AstrBot。
- 前端源码 `frontend/`(Vite + React + TypeScript + recharts),构建产物提交 git 以支持零构建部署。
- 开发工具链:`uv` + `ruff` + `pytest` + `mypy`。

[0.1.0]: https://github.com/leafliber/astrbot_plugin_cost_control/releases/tag/v0.1.0
[0.2.0]: https://github.com/leafliber/astrbot_plugin_cost_control/releases/tag/v0.2.0
[0.2.1]: https://github.com/leafliber/astrbot_plugin_cost_control/releases/tag/v0.2.1
[0.3.0]: https://github.com/leafliber/astrbot_plugin_cost_control/releases/tag/v0.3.0
