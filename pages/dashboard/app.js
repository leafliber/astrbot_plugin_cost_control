/*
 * 成本控制 Plugin Page 前端逻辑。
 *
 * bridge SDK：window.AstrBotPluginPage（由 dashboard 自动注入）。
 *   - ready() / getContext() / getLocale() / onContext(fn)
 *   - apiGet(endpoint, params) / apiPost(endpoint, body)
 *
 * endpoint 前缀自适应：后端 route 自带 /astrbot_plugin_cost_control 命名空间，
 * 但父级 dashboard SPA 给 apiGet 的 endpoint 拼接规则无法从源码确认（Vue dist
 * 不在 wheel）。故启动时探测两种格式，锁定命中者后缓存。
 */
(function () {
    "use strict";

    var Page = window.AstrBotPluginPage;
    var PLUGIN = "astrbot_plugin_cost_control";
    var API_PREFIX = null; // null=未探测；""=短格式；"/<plugin>"=全格式
    var currentTab = "overview";
    var currentWindow = "daily";
    var pollTimer = null;

    function $(id) {
        return document.getElementById(id);
    }
    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }
    function fmtNum(n) {
        n = Number(n || 0);
        return n.toLocaleString("zh-CN");
    }
    function fmtCost(n) {
        n = Number(n || 0);
        return "$" + (n < 0.01 && n > 0 ? n.toFixed(6) : n.toFixed(4));
    }
    function shortTime(iso) {
        if (!iso) return "-";
        try {
            return new Date(iso).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
            });
        } catch (e) {
            return esc(iso);
        }
    }

    // ===== API 前缀探测与封装 =====
    async function rawApi(suffix, params, method) {
        if (!Page) throw new Error("bridge SDK 未就绪");
        var endpoint = (API_PREFIX || "") + suffix;
        if (method === "POST") {
            return await Page.apiPost(endpoint, params || {});
        }
        return await Page.apiGet(endpoint, params || {});
    }

    async function probePrefix() {
        var candidates = ["", "/" + PLUGIN];
        for (var i = 0; i < candidates.length; i++) {
            var saved = API_PREFIX;
            API_PREFIX = candidates[i];
            try {
                var r = await Page.apiGet(candidates[i] + "/overview", {
                    window: "daily",
                });
                if (r && r.status === "ok") {
                    return candidates[i];
                }
            } catch (e) {
                // 试下一个
            }
        }
        API_PREFIX = ""; // 兜底默认短格式
        return API_PREFIX;
    }

    async function api(suffix, params) {
        var r = await rawApi(suffix, params, "GET");
        if (r && r.status === "ok") return r.data;
        if (r && r.status === "error")
            throw new Error(r.message || "请求失败");
        if (r && r.data != null) return r.data;
        throw new Error("无效响应");
    }
    async function apiPost(suffix, body) {
        var r = await rawApi(suffix, body, "POST");
        if (r && r.status === "ok") return r.data;
        if (r && r.status === "error")
            throw new Error(r.message || "请求失败");
        throw new Error("无效响应");
    }

    function setError(msg) {
        $("content").innerHTML = '<div class="error">' + esc(msg) + "</div>";
    }
    function setLoading(msg) {
        $("content").innerHTML =
            '<div class="loading">' + esc(msg || "加载中…") + "</div>";
    }

    // ===== 各标签渲染 =====
    function cardsBlock(items) {
        return (
            '<div class="cards">' +
            items
                .map(function (c) {
                    return (
                        '<div class="card"><div class="label">' +
                        esc(c.label) +
                        '</div><div class="value">' +
                        esc(c.value) +
                        '</div>' +
                        (c.sub
                            ? '<div class="sub">' + esc(c.sub) + "</div>"
                            : "") +
                        "</div>"
                    );
                })
                .join("") +
            "</div>"
        );
    }

    async function loadOverview() {
        setLoading();
        try {
            var r = await api("/overview", { window: currentWindow });
            var u = r.usage || {};
            var cards = [
                { label: "调用次数", value: fmtNum(u.count) },
                { label: "成本", value: fmtCost(r.cost), sub: "USD" },
                {
                    label: "输入(非缓存)",
                    value: fmtNum(u.token_input_other),
                    sub: "token",
                },
                {
                    label: "缓存命中",
                    value: fmtNum(u.token_input_cached),
                    sub: "token",
                },
                {
                    label: "输出",
                    value: fmtNum(u.token_output),
                    sub: "token",
                },
                {
                    label: "平均缓存命中率",
                    value: (r.cache_hit_rate || 0) + "%",
                    sub: (r.cache_samples || 0) + " 样本",
                },
                {
                    label: "平均上下文注入",
                    value: fmtNum(r.avg_injection),
                    sub: (r.injection_samples || 0) + " 样本 · token",
                },
            ];
            var html = cardsBlock(cards);

            var byModel = r.cost_by_model || [];
            if (byModel.length) {
                html +=
                    '<div class="panel"><h2>按模型成本</h2><table><thead><tr>' +
                    "<th>模型</th><th>调用</th><th>token</th><th>成本</th></tr>" +
                    "</thead><tbody>";
                byModel.forEach(function (m) {
                    html +=
                        "<tr><td class='mono'>" +
                        esc(m.model || "?") +
                        "</td><td>" +
                        fmtNum(m.count) +
                        "</td><td>" +
                        fmtNum(m.tokens) +
                        "</td><td>" +
                        fmtCost(m.cost) +
                        "</td></tr>";
                });
                html += "</tbody></table></div>";
            }

            var top = r.top_sessions || [];
            if (top.length) {
                html +=
                    '<div class="panel"><h2>Top 会话（按 token）</h2><table><thead><tr>' +
                    "<th>会话</th><th>调用</th><th>token</th></tr>" +
                    "</thead><tbody>";
                top.forEach(function (s) {
                    html +=
                        "<tr><td class='mono'>" +
                        esc(s.umo || "?") +
                        "</td><td>" +
                        fmtNum(s.count) +
                        "</td><td>" +
                        fmtNum(s.tokens) +
                        "</td></tr>";
                });
                html += "</tbody></table></div>";
            }
            $("content").innerHTML = html;
        } catch (e) {
            setError("加载总览失败：" + esc(e.message));
        }
    }

    async function loadRecords() {
        setLoading();
        $("content").innerHTML =
            '<div class="toolbar"><input id="rec-umo" placeholder="按会话筛选" style="flex:1"></div>' +
            '<div id="rec-body" class="loading">加载中…</div>';
        async function fetchRecords() {
            var umo = $("rec-umo").value.trim();
            try {
                var rows = await api("/records", {
                    umo: umo,
                    limit: 200,
                });
                if (!rows || !rows.length) {
                    $("rec-body").innerHTML =
                        '<div class="empty">暂无明细记录</div>';
                    return;
                }
                var html =
                    '<div class="panel"><table><thead><tr>' +
                    "<th>时间</th><th>会话</th><th>模型</th><th>输入</th><th>缓存</th>" +
                    "<th>输出</th><th>cache 写入</th><th>注入</th></tr>" +
                    "</thead><tbody>";
                rows.forEach(function (r) {
                    html +=
                        "<tr><td>" +
                        shortTime(r.created_at) +
                        "</td><td class='mono'>" +
                        esc(r.umo) +
                        "</td><td class='mono'>" +
                        esc(r.provider_model || r.provider_id || "-") +
                        "</td><td>" +
                        fmtNum(r.token_input_other) +
                        "</td><td>" +
                        fmtNum(r.token_input_cached) +
                        "</td><td>" +
                        fmtNum(r.token_output) +
                        "</td><td>" +
                        fmtNum(r.cache_creation) +
                        "</td><td>" +
                        (r.injection_total == null ? "-" : fmtNum(r.injection_total)) +
                        "</td></tr>";
                });
                html += "</tbody></table></div>";
                $("rec-body").innerHTML = html;
            } catch (e) {
                $("rec-body").innerHTML =
                    '<div class="error">加载失败：' + esc(e.message) + "</div>";
            }
        }
        $("rec-umo").addEventListener("change", fetchRecords);
        await fetchRecords();
    }

    function bar(ratio, used, limit) {
        var pct = Math.min(100, Math.max(0, ratio || 0));
        var cls = pct >= 100 ? "bad" : pct >= 80 ? "warn" : "";
        return (
            '<div class="row" style="align-items:center;gap:8px">' +
            '<div class="bar-wrap" style="flex:1"><div class="bar ' +
            cls +
            '" style="width:' +
            pct +
            '%"></div></div>' +
            "<span>" +
            fmtNum(used) +
            " / " +
            fmtNum(limit) +
            " (" +
            (ratio || 0) +
            "%)</span></div>"
        );
    }

    async function loadBudgets() {
        setLoading();
        try {
            var r = await api("/budgets");
            var limits = r.limits || {};
            var dims = r.dimensions || {};
            var dimRows = [
                ["per_session_daily", "单会话每日"],
                ["per_user_daily", "单用户每日"],
                ["per_model_daily", "单模型每日"],
                ["global_daily", "全局每日"],
                ["global_monthly", "全局每月"],
            ];
            var html =
                '<div class="panel"><h2>预算配置（token）</h2><table><thead><tr>' +
                "<th>维度</th><th>上限</th><th>当前消耗</th><th>进度</th></tr></thead><tbody>";
            dimRows.forEach(function (d) {
                var key = d[0];
                var limit = limits[key] || 0;
                var used = 0,
                    ratio = 0;
                if (dims[key]) {
                    used = dims[key].used || 0;
                    ratio = dims[key].ratio || 0;
                }
                var limitCell =
                    limit > 0 ? fmtNum(limit) : '<span class="muted">不限制</span>';
                var prog =
                    limit > 0
                        ? bar(ratio, used, limit)
                        : '<span class="muted">-</span>';
                html +=
                    "<tr><td>" +
                    d[1] +
                    "</td><td>" +
                    limitCell +
                    "</td><td>" +
                    fmtNum(used) +
                    "</td><td style='min-width:220px'>" +
                    prog +
                    "</td></tr>";
            });
            html += "</tbody></table></div>";
            $("content").innerHTML = html;
        } catch (e) {
            setError("加载预算失败：" + esc(e.message));
        }
    }

    async function loadCache() {
        setLoading();
        try {
            var r = await api("/cache");
            var cards = [
                {
                    label: "平均缓存命中率",
                    value: (r.cache_hit_rate || 0) + "%",
                    sub: (r.samples || 0) + " 样本",
                },
                { label: "破坏事件", value: fmtNum((r.events || []).length) },
            ];
            var html = cardsBlock(cards);
            var events = r.events || [];
            html +=
                '<div class="panel"><h2>缓存破坏事件（最近）</h2>';
            if (!events.length) {
                html += '<div class="empty">未检测到缓存破坏事件</div>';
            } else {
                html +=
                    '<table><thead><tr><th>类型</th><th>严重度</th><th>会话</th><th>详情</th></tr></thead><tbody>';
                events
                    .slice()
                    .reverse()
                    .forEach(function (ev) {
                        var sev = (ev.severity || "low").toLowerCase();
                        html +=
                            "<tr><td>" +
                            esc(ev.type || "?") +
                            '</td><td><span class="tag sev-' +
                            sev +
                            '">' +
                            esc(ev.severity || "-") +
                            "</span></td><td class='mono'>" +
                            esc(ev.umo || "-") +
                            "</td><td>" +
                            esc(ev.detail || "") +
                            "</td></tr>";
                    });
                html += "</tbody></table>";
            }
            html += "</div>";
            $("content").innerHTML = html;
        } catch (e) {
            setError("加载缓存诊断失败：" + esc(e.message));
        }
    }

    async function loadAttribution() {
        setLoading();
        try {
            var r = await api("/attribution");
            var avg = r.avg_components || {};
            var cards = [
                { label: "system 平均", value: fmtNum(avg.system) },
                { label: "tools 平均", value: fmtNum(avg.tools) },
                { label: "history 平均", value: fmtNum(avg.history) },
            ];
            var html = cardsBlock(cards);
            var recent = r.recent || [];
            html += '<div class="panel"><h2>最近请求归因</h2>';
            if (!recent.length) {
                html += '<div class="empty">暂无归因数据</div>';
            } else {
                html +=
                    '<table><thead><tr><th>时间</th><th>会话</th><th>注入 token</th>' +
                    "<th>system</th><th>tools</th><th>history</th></tr></thead><tbody>";
                recent.forEach(function (it) {
                    var a = it.attribution || {};
                    html +=
                        "<tr><td>" +
                        shortTime(it.created_at) +
                        "</td><td class='mono'>" +
                        esc(it.umo || "-") +
                        "</td><td>" +
                        (it.injection_total == null
                            ? "-"
                            : fmtNum(it.injection_total)) +
                        "</td><td>" +
                        fmtNum(a.system) +
                        "</td><td>" +
                        fmtNum(a.tools) +
                        "</td><td>" +
                        fmtNum(a.history) +
                        "</td></tr>";
                });
                html += "</tbody></table>";
            }
            html += "</div>";
            $("content").innerHTML = html;
        } catch (e) {
            setError("加载归因失败：" + esc(e.message));
        }
    }

    async function loadPricing() {
        setLoading();
        try {
            var data = await api("/pricing");
            var keys = Object.keys(data || {}).sort();
            if (!keys.length) {
                $("content").innerHTML =
                    '<div class="empty">暂无定价数据</div>';
                return;
            }
            var html =
                '<div class="panel"><h2>模型单价（USD / 百万 token）</h2>' +
                '<table><thead><tr><th>模型</th><th>输入</th><th>缓存命中</th>' +
                "<th>输出</th><th>缓存写入</th></tr></thead><tbody>";
            keys.forEach(function (k) {
                var p = data[k] || {};
                html +=
                    "<tr><td class='mono'>" +
                    esc(k) +
                    "</td><td>" +
                    (p.input != null ? p.input : "-") +
                    "</td><td>" +
                    (p.input_cached != null ? p.input_cached : "-") +
                    "</td><td>" +
                    (p.output != null ? p.output : "-") +
                    "</td><td>" +
                    (p.cache_creation != null ? p.cache_creation : "-") +
                    "</td></tr>";
            });
            html += "</tbody></table></div>";
            $("content").innerHTML = html;
        } catch (e) {
            setError("加载定价失败：" + esc(e.message));
        }
    }

    async function loadSettings() {
        setLoading();
        try {
            var cfg = await api("/config");
            var html = '<div class="panel"><h2>手动操作</h2><div class="row">';
            html +=
                '<button class="btn" id="act-cleanup">清理过期数据</button>';
            html += '<button class="btn" id="act-report">推送日报</button>';
            html += "</div><div id='action-result' class='muted' style='margin-top:8px'></div></div>";
            html +=
                '<div class="panel"><h2>当前配置</h2><pre class="mono" style="white-space:pre-wrap;background:var(--panel-2);padding:10px;border-radius:6px">' +
                esc(JSON.stringify(cfg, null, 2)) +
                "</pre></div>";
            $("content").innerHTML = html;

            $("act-cleanup").onclick = async function () {
                $("action-result").textContent = "执行中…";
                try {
                    var r = await apiPost("/actions/cleanup");
                    $("action-result").textContent =
                        "已清理 " + fmtNum((r && r.deleted) || 0) + " 条记录";
                } catch (e) {
                    $("action-result").textContent = "失败：" + e.message;
                }
            };
            $("act-report").onclick = async function () {
                $("action-result").textContent = "执行中…";
                try {
                    await apiPost("/actions/report");
                    $("action-result").textContent = "日报已触发推送";
                } catch (e) {
                    $("action-result").textContent = "失败：" + e.message;
                }
            };
        } catch (e) {
            setError("加载设置失败：" + esc(e.message));
        }
    }

    var LOADERS = {
        overview: loadOverview,
        records: loadRecords,
        budgets: loadBudgets,
        cache: loadCache,
        attribution: loadAttribution,
        pricing: loadPricing,
        settings: loadSettings,
    };

    function switchTab(name) {
        currentTab = name;
        var tabs = document.querySelectorAll(".tab");
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle("active", tabs[i].dataset.tab === name);
        }
        var winSwitch = $("window-switch");
        winSwitch.hidden = name !== "overview";
        stopPoll();
        var loader = LOADERS[name] || loadOverview;
        loader();
        if (name === "overview") {
            startPoll();
        }
    }

    function startPoll() {
        stopPoll();
        pollTimer = setInterval(loadOverview, 30000);
    }
    function stopPoll() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    async function refresh() {
        var loader = LOADERS[currentTab] || loadOverview;
        await loader();
    }

    function applyTheme(isDark) {
        document.body.dataset.theme = isDark ? "dark" : "light";
    }

    async function init() {
        if (!Page) {
            setError("bridge SDK 未注入（请在 AstrBot WebUI 插件页打开本页面）");
            return;
        }
        try {
            await Page.ready();
            var ctx = Page.getContext ? Page.getContext() : null;
            if (ctx) {
                applyTheme(!!ctx.isDark);
                $("bridge-info").textContent =
                    (ctx.displayName || "插件") + " · " + (ctx.locale || "");
            }
            if (Page.onContext) {
                Page.onContext(function (c) {
                    if (c) applyTheme(!!c.isDark);
                });
            }
        } catch (e) {
            /* ready 失败也继续尝试调用 */
        }

        try {
            await probePrefix();
            $("status").textContent = "已连接";
        } catch (e) {
            $("status").textContent = "连接失败";
        }

        // 标签点击
        var tabs = document.querySelectorAll(".tab");
        for (var i = 0; i < tabs.length; i++) {
            (function (t) {
                t.onclick = function () {
                    switchTab(t.dataset.tab);
                };
            })(tabs[i]);
        }

        // 窗口切换
        var winBtns = document.querySelectorAll(".win-btn");
        for (var j = 0; j < winBtns.length; j++) {
            (function (b) {
                b.onclick = function () {
                    currentWindow = b.dataset.window;
                    for (var k = 0; k < winBtns.length; k++) {
                        winBtns[k].classList.remove("active");
                    }
                    b.classList.add("active");
                    if (currentTab === "overview") loadOverview();
                };
            })(winBtns[j]);
        }

        $("refresh").onclick = refresh;

        switchTab("overview");
    }

    init();
})();
