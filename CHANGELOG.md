# Changelog

本文件记录成本控制插件(`astrbot_plugin_cost_control`)的版本变更。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
