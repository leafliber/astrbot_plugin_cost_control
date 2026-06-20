# astrbot_plugin_cost_control

细粒度 token 监控、超预算提醒、缓存破坏诊断、提示词优化与全数据可视化的 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件。

> **状态：开发中（阶段 0 骨架）**。当前仅包含目录结构、模块签名与占位代码，具体逻辑在后续阶段实现。

## 功能

- **细粒度 token 监控**：基于原生 `ProviderStat` 表，按会话 / 用户 / 模型 / 全局四维聚合 token 用量。
- **超预算提醒**：按日 / 月维度配置 token 上限，超限可拦截请求或切换备用 Provider，并主动推送告警。
- **缓存破坏诊断**：检测上下文重置、system prompt 变更、工具定义变更、消息顺序漂移四类导致 prompt cache 失效的原因。
- **提示词优化**：静态分析 system prompt 冗余，并支持 LLM 改写以降低 token 消耗、提升缓存命中率。
- **Plugin Page 可视化**：通过 AstrBot WebUI 内嵌面板查看用量、成本、缓存命中、归因报表。

## 安装

将本目录放入 AstrBot 的 `data/plugins/` 目录（或软链）：

```bash
ln -s /path/to/astrbot_plugin_cost_control /path/to/AstrBot/data/plugins/astrbot_plugin_cost_control
```

重启或通过 WebUI 重载插件即可。本插件运行时无额外第三方依赖，复用 AstrBot 自带依赖。

## 本地开发

需要 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                                    # 创建 .venv 并安装 dev 依赖
uv run ruff format .                       # 格式化
uv run ruff check .                        # lint
uv run pytest                              # 运行测试
uv run pytest tests/test_cost.py::test_placeholder   # 单测
uv run mypy cost_control                   # 类型检查
```

调试流程：本地启动 AstrBot，把本目录软链到 `data/plugins/astrbot_plugin_cost_control`，通过 WebUI 重载插件即可热加载。

## Plugin Page 前端（React）

Plugin Page（`pages/dashboard/`）由 `frontend/` 的 Vite + React + TypeScript + recharts 源码构建。产物为 `index.html` + `app.js` + `style.css` 三文件（固定名、相对路径），提交 git 以便零构建部署。

```bash
cd frontend
npm install        # 首次安装依赖
npm run dev        # 本地预览（自动注入 mock bridge，仅验布局；真实数据须在 AstrBot 内验证）
npm run build      # 构建到 ../pages/dashboard/
npm run typecheck  # TypeScript 类型检查
```

修改前端后 `npm run build` 覆盖 `pages/dashboard/`，再在 AstrBot WebUI 重载插件即可热生效。注意 `pages/dashboard/` 全为构建产物，勿手写文件（`emptyOutDir` 构建时会清空重建）。

## 配置

插件配置通过 AstrBot WebUI 的插件配置面板编辑（对应 `_conf_schema.json`）。主要配置项：

- `enabled`：总开关
- `budgets`：日 / 月预算阈值
- `pricing`：模型单价表
- `over_limit_policy`：超限动作（拦截 / 切换备用 Provider）
- `cache_diag`：缓存诊断开关与阈值
- `alerts`：告警推送与冷却
- `prompt_optimizer`：提示词优化设置
- `attribution`：上下文归因采样
- `schedule`：定时任务

## 命令

| 命令 | 说明 | 状态 |
| --- | --- | --- |
| `/cost` | 查询当前会话 token 用量与成本 | 开发中 |
| `/budget` | 查询 / 设置预算阈值 | 开发中 |
| `/optimize` | 分析并优化 system prompt | 开发中 |
| `/cache` | 缓存命中率与破坏诊断 | 开发中 |
| `/report` | 生成用量 / 成本报表 | 开发中 |
| `/attribution` | 查看 token 注入归因 | 开发中 |

## 阶段划分

- **阶段 0**：目录骨架、模块签名、占位代码（当前）
- **阶段 1**：usage_query / cost / supplement 数据采集
- **阶段 2**：budget / notifier / schedule 预算告警
- **阶段 3**：cache_diag / prompt_optimizer / attribution 高级分析
- **阶段 4**：web_api + Plugin Page 可视化
