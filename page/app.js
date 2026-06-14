// 成本控制插件 Plugin Page 前端入口（阶段 0 占位）。
// 阶段 4 将通过 AstrBot 注入的 plugin_page_bridge.js SDK 与后端 Web API 通信。
(function () {
    "use strict";
    console.log("cost_control page loaded");

    // 阶段 4 实现：检测 bridge SDK 并更新状态文本。
    // 注意：不要硬编码具体 SDK 名称，等待阶段 4 确认注入的 API 名。
    var statusEl = document.getElementById("bridge-text");
    if (statusEl) {
        // 简单占位：实际 bridge 检测在阶段 4 补全。
        statusEl.textContent = "bridge SDK 检测将在阶段 4 实现";
    }
})();
