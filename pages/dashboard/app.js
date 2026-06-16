/*
 * 成本控制 Plugin Page 前端逻辑。
 *
 * bridge SDK：window.AstrBotPluginPage（由 dashboard 注入的
 *   /api/plugin/page/bridge-sdk.js；index.html 用显式 <script> 标签在 app.js
 *   之前加载它，dashboard 会把该 src 替换为带 asset_token 的真实 URL）。
 *   - ready()：返回 Promise，父级 SPA 回传 context 后 resolve（握手完成）
 *   - getContext() / onContext(fn)：取 / 监听主题、locale 等上下文
 *   - apiGet(endpoint, params) / apiPost(endpoint, body)：经 postMessage 由
 *     父级 SPA 代发到后端 REST（自动带 dashboard JWT）
 *
 * endpoint 规则（已核对 message_recorder 参考实现 + bridge.js 源码 + 本机
 * astrbot 4.25.5）：父级 SPA 代发时自动补 ``/api/plug/<pluginName>/`` 前缀，
 * 故前端 endpoint **不带前导斜杠、不带插件名**（如 ``"overview"``、
 * ``"actions/cleanup"``），与后端 ``register_web_api`` 的 route
 * ``/astrbot_plugin_cost_control/<endpoint>`` 一一对应。
 *
 * 响应信封：后端用非标准 {success, data}（见 web_api.py），父级 SPA 的 API
 * 客户端只解包标准 {status, data}，对 {success} 原样透传，前端 extractData 自处理。
 *
 * 图表：Chart.js（CDN，index.html 引入）。CDN 失败时（window.__chartJsFailed
 * 或 typeof Chart === "undefined"）降级为纯 CSS 柱状条（renderCssBars）。
 */
(function () {
    "use strict";

    var Page = null;
    var bridgeReady = false;
    var currentTab = "overview";
    var currentWindow = "daily";
    var pollTimer = null;

    // 模块级状态
    var charts = {}; // 当前 tab 的 Chart 实例（key=canvas id）
    var recordsFilter = {
        preset: "7d", // today|7d|30d|custom
        start: "",
        end: "",
        model: "",
        umo: "",
        provider: "",
        order_by: "created_at",
        order_dir: "desc",
    };
    var cachedModels = []; // 模型下拉选项（首次拉 overview 后缓存）
    var aggMode = "model"; // 明细聚合视图：model|umo

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
    function fmtCompact(n) {
        n = Number(n || 0);
        if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
        if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
        if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
        return String(Math.round(n));
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
    function shortDate(iso) {
        if (!iso) return "-";
        try {
            return new Date(iso).toLocaleDateString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
            });
        } catch (e) {
            return esc(iso);
        }
    }
    function cssVar(name) {
        try {
            return getComputedStyle(document.body).getPropertyValue(name).trim();
        } catch (e) {
            return "";
        }
    }

    // ===== bridge 就绪 =====
    function waitForBridge(timeout) {
        return new Promise(function (resolve) {
            if (window.AstrBotPluginPage) return resolve(window.AstrBotPluginPage);
            var start = Date.now();
            var t = setInterval(function () {
                if (window.AstrBotPluginPage) {
                    clearInterval(t);
                    resolve(window.AstrBotPluginPage);
                } else if (Date.now() - start > (timeout || 5000)) {
                    clearInterval(t);
                    resolve(null);
                }
            }, 50);
        });
    }

    // ===== API 封装 =====
    function extractData(response) {
        if (response && typeof response === "object") {
            if (response.success === true) return response.data;
            if (response.success === false)
                throw new Error(response.error || "请求失败");
        }
        return response;
    }
    async function api(endpoint, params) {
        if (!Page || !bridgeReady) throw new Error("Bridge SDK 未就绪");
        return extractData(await Page.apiGet(endpoint, params || {}));
    }
    async function apiPost(endpoint, body) {
        if (!Page || !bridgeReady) throw new Error("Bridge SDK 未就绪");
        return extractData(await Page.apiPost(endpoint, body || {}));
    }

    function setError(msg) {
        $("content").innerHTML = '<div class="error">' + esc(msg) + "</div>";
    }
    function setLoading(msg) {
        $("content").innerHTML =
            '<div class="loading">' + esc(msg || "加载中…") + "</div>";
    }

    // ===== 图表辅助 =====
    function chartAvailable() {
        return typeof window.Chart !== "undefined" && !window.__chartJsFailed;
    }
    // 销毁当前所有 Chart 实例（切 tab / 重渲前调用，防 canvas 累积泄漏）
    function destroyCharts() {
        Object.keys(charts).forEach(function (k) {
            try {
                charts[k].destroy();
            } catch (e) {
                /* noop */
            }
            delete charts[k];
        });
    }
    // 渲染或更新一个图表；Chart 不可用时降级为 CSS 柱状条
    function ensureChart(id, type, data, options) {
        var el = document.getElementById(id);
        if (!el) return null;
        if (!chartAvailable()) {
            renderCssBars(el, data);
            return null;
        }
        try {
            if (charts[id]) {
                charts[id].data = data;
                charts[id].update();
                return charts[id];
            }
            var c = new Chart(el.getContext("2d"), {
                type: type,
                data: data,
                options: options || {},
            });
            charts[id] = c;
            return c;
        } catch (e) {
            renderCssBars(el, data);
            return null;
        }
    }
    // CDN 失败兜底：用 .bar-wrap 渲染纵向柱状条（labels + values）
    function renderCssBars(el, data) {
        var labels = (data && data.labels) || [];
        var dset = (data && data.datasets && data.datasets[0]) || {};
        var vals = dset.data || [];
        var max = 0;
        vals.forEach(function (v) {
            if (v > max) max = v;
        });
        var html = '<div class="cssbars">';
        labels.forEach(function (lbl, i) {
            var v = vals[i] || 0;
            var pct = max > 0 ? Math.round((v * 100) / max) : 0;
            html +=
                '<div class="cssbar-col"><div class="cssbar-bar" style="height:' +
                Math.max(2, pct) +
                '%"></div><div class="cssbar-val">' +
                esc(fmtCompact(v)) +
                '</div><div class="cssbar-lbl">' +
                esc(lbl) +
                "</div></div>";
        });
        html += "</div>";
        el.parentNode.innerHTML =
            '<div class="cssbars-wrap"><div class="cssbars-note">图表库加载失败，已降级显示</div>' +
            html +
            "</div>";
    }
    function baseChartOptions(extra) {
        var grid = cssVar("--border") || "#e3e6ea";
        var dim = cssVar("--text-dim") || "#6b7280";
        var o = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: dim, boxWidth: 12, font: { size: 11 } },
                },
                tooltip: { intersect: false },
            },
            scales: {
                x: { ticks: { color: dim, font: { size: 10 } }, grid: { display: false } },
                y: {
                    ticks: {
                        color: dim,
                        font: { size: 10 },
                        callback: function (v) {
                            return fmtCompact(v);
                        },
                    },
                    grid: { color: grid },
                },
            },
        };
        if (extra) o = mergeOpts(o, extra);
        return o;
    }
    function mergeOpts(a, b) {
        Object.keys(b || {}).forEach(function (k) {
            var bv = b[k];
            if (bv && typeof bv === "object" && !Array.isArray(bv) && a[k] && typeof a[k] === "object") {
                a[k] = mergeOpts(a[k], bv);
            } else {
                a[k] = bv;
            }
        });
        return a;
    }

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
                        "</div>" +
                        (c.sub ? '<div class="sub">' + esc(c.sub) + "</div>" : "") +
                        (c.delta ? '<div class="delta-row">' + c.delta + "</div>" : "") +
                        "</div>"
                    );
                })
                .join("") +
            "</div>"
        );
    }

    function windowToDays(w) {
        return w === "monthly" ? 90 : w === "weekly" ? 30 : 7;
    }

    // ===== 总览（问题导向 + 图表） =====
    async function loadOverview() {
        setLoading();
        try {
            var days = windowToDays(currentWindow);
            // 并行拉聚合报表 + 时序
            var r = await api("overview", { window: currentWindow });
            var series = [];
            var cmp = null;
            try {
                var tl = await api("timeline", { days: days, bucket: "day" });
                series = (tl && tl.series) || [];
            } catch (e) {
                /* 时序失败不阻断 */
            }
            try {
                cmp = await api("compare", { window: currentWindow });
            } catch (e) {
                /* 环比失败不阻断 */
            }
            renderOverview(r, series, days, cmp);
        } catch (e) {
            setError("加载总览失败：" + esc(e.message));
        }
    }

    function renderOverview(r, series, days, cmp) {
        destroyCharts();
        var u = r.usage || {};
        // 缓存模型列表（明细页模型下拉复用）
        try {
            cachedModels = (r.cost_by_model || []).map(function (m) {
                return m.model;
            });
        } catch (e) {
            cachedModels = [];
        }

        var cards = [
            {
                label: "成本",
                value: fmtCost(r.cost),
                sub: "USD · " + currentWindowLabel(),
                delta: deltaText(cmp, "cost"),
            },
            {
                label: "调用次数",
                value: fmtNum(u.count),
                sub: currentWindowLabel(),
                delta: deltaText(cmp, "count"),
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

        // 趋势 + 模型 两列
        html +=
            '<div class="grid-2">' +
            '<div class="panel"><h2>用量趋势（近 ' +
            days +
            ' 天）</h2><div class="chart-box"><canvas id="chart-trend"></canvas></div></div>' +
            '<div class="panel"><h2>按模型成本</h2><div class="chart-box"><canvas id="chart-model"></canvas></div></div>' +
            "</div>";
        // token 构成 + top 会话
        html +=
            '<div class="grid-2">' +
            '<div class="panel"><h2>Token 构成</h2><div class="chart-box"><canvas id="chart-tokens"></canvas></div></div>' +
            '<div class="panel"><h2>Top 会话（按 token）</h2><div class="chart-box"><canvas id="chart-sessions"></canvas></div></div>' +
            "</div>";

        $("content").innerHTML = html;

        var accent = cssVar("--accent") || "#4f7cff";
        var cached = cssVar("--ok") || "#2f9e44";
        var warn = cssVar("--warn") || "#f08c00";
        var other = "#8ab4ff";

        // 趋势折线
        if (series.length) {
            ensureChart(
                "chart-trend",
                "line",
                {
                    labels: series.map(function (s) {
                        return shortDate(s.bucket);
                    }),
                    datasets: [
                        {
                            label: "调用",
                            data: series.map(function (s) {
                                return s.count;
                            }),
                            borderColor: accent,
                            backgroundColor: accent + "22",
                            tension: 0.3,
                            fill: true,
                            yAxisID: "y",
                        },
                        {
                            label: "Token",
                            data: series.map(function (s) {
                                return (
                                    (s.token_input_other || 0) +
                                    (s.token_input_cached || 0) +
                                    (s.token_output || 0)
                                );
                            }),
                            borderColor: warn,
                            backgroundColor: "transparent",
                            tension: 0.3,
                            yAxisID: "y1",
                        },
                    ],
                },
                baseChartOptions({
                    scales: {
                        y: { position: "left", title: { display: true, text: "调用", color: accent } },
                        y1: {
                            position: "right",
                            grid: { drawOnChartArea: false },
                            title: { display: true, text: "Token", color: warn },
                            ticks: {
                                callback: function (v) {
                                    return fmtCompact(v);
                                },
                            },
                        },
                    },
                })
            );
        } else {
            var el = document.getElementById("chart-trend");
            if (el) el.parentNode.innerHTML = '<div class="empty">暂无时序数据</div>';
        }

        // 按模型成本柱状
        var byModel = (r.cost_by_model || []).slice(0, 8);
        if (byModel.length) {
            ensureChart(
                "chart-model",
                "bar",
                {
                    labels: byModel.map(function (m) {
                        return shortModelName(m.model);
                    }),
                    datasets: [
                        {
                            label: "成本 (USD)",
                            data: byModel.map(function (m) {
                                return m.cost;
                            }),
                            backgroundColor: accent,
                        },
                    ],
                },
                baseChartOptions({
                    plugins: { legend: { display: false } },
                    scales: {
                        y: {
                            ticks: {
                                callback: function (v) {
                                    return "$" + fmtCompact(v);
                                },
                            },
                        },
                    },
                })
            );
        } else {
            var em = document.getElementById("chart-model");
            if (em) em.parentNode.innerHTML = '<div class="empty">暂无模型成本数据</div>';
        }

        // token 构成（堆叠单柱）
        var tOther = u.token_input_other || 0;
        var tCached = u.token_input_cached || 0;
        var tOut = u.token_output || 0;
        if (tOther + tCached + tOut > 0) {
            ensureChart(
                "chart-tokens",
                "bar",
                {
                    labels: [currentWindowLabel()],
                    datasets: [
                        { label: "输入(非缓存)", data: [tOther], backgroundColor: other },
                        { label: "缓存命中", data: [tCached], backgroundColor: cached },
                        { label: "输出", data: [tOut], backgroundColor: accent },
                    ],
                },
                baseChartOptions({
                    scales: { x: { stacked: true }, y: { stacked: true } },
                })
            );
        } else {
            var et = document.getElementById("chart-tokens");
            if (et) et.parentNode.innerHTML = '<div class="empty">暂无 token 数据</div>';
        }

        // Top 会话横向柱状
        var top = (r.top_sessions || []).slice(0, 8);
        if (top.length) {
            var rev = top.slice().reverse();
            var labels = rev.map(function (s) {
                return shortUmo(s.umo);
            });
            var data = rev.map(function (s) {
                return s.tokens;
            });
            var costs = rev.map(function (s) {
                return s.cost || 0;
            });
            ensureChart(
                "chart-sessions",
                "bar",
                {
                    labels: labels,
                    datasets: [{ label: "Token", data: data, backgroundColor: warn }],
                },
                baseChartOptions({
                    indexAxis: "y",
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function (ctx) {
                                    var tok = ctx.parsed.x;
                                    var c = costs[ctx.dataIndex] || 0;
                                    return (
                                        "Token " + fmtCompact(tok) + " · 成本 " + fmtCost(c)
                                    );
                                },
                            },
                        },
                    },
                })
            );
        } else {
            var es = document.getElementById("chart-sessions");
            if (es) es.parentNode.innerHTML = '<div class="empty">暂无会话数据</div>';
        }
    }

    function currentWindowLabel() {
        return currentWindow === "monthly"
            ? "本月"
            : currentWindow === "weekly"
              ? "近 7 天"
              : "今日";
    }
    // 环比徽标：cmp 为 compare 端点结果，key 为 cost/count/tokens。
    // 成本/调用上升=不利（红），下降=有利（绿）；上期为 0 时显示「新增」。
    function deltaText(cmp, key) {
        if (!cmp) return "";
        var d = cmp.delta || {};
        var pct = d[key + "_pct"];
        var label = cmp.label || "上期";
        if (pct == null) {
            return '<span class="delta new">' + esc(label) + "无用量</span>";
        }
        var cls = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
        var arrow = pct > 0 ? "↑" : pct < 0 ? "↓" : "→";
        return (
            '<span class="delta ' +
            cls +
            '">' +
            arrow +
            Math.abs(pct) +
            "% vs " +
            esc(label) +
            "</span>"
        );
    }
    function shortModelName(m) {
        m = String(m || "?");
        return m.length > 24 ? m.slice(0, 22) + "…" : m;
    }
    function shortUmo(u) {
        u = String(u || "?");
        var parts = u.split(":");
        var tail = parts[parts.length - 1] || u;
        return tail.length > 16 ? tail.slice(0, 14) + "…" : tail;
    }

    // ===== 明细（筛选 + 排序 + 聚合） =====
    async function loadRecords() {
        setLoading();
        await fetchRecords();
    }

    function recordsRangeParams() {
        var now = new Date();
        var end = now.toISOString().slice(0, 10);
        var start;
        if (recordsFilter.preset === "today") {
            start = end;
        } else if (recordsFilter.preset === "7d") {
            var d = new Date(now);
            d.setDate(d.getDate() - 6);
            start = d.toISOString().slice(0, 10);
        } else if (recordsFilter.preset === "30d") {
            var d2 = new Date(now);
            d2.setDate(d2.getDate() - 29);
            start = d2.toISOString().slice(0, 10);
        } else {
            // custom
            start = recordsFilter.start || "";
            end = recordsFilter.end || "";
        }
        return { start: start, end: end };
    }

    function renderRecordsToolbar() {
        var presetBtns = ["today", "7d", "30d", "custom"]
            .map(function (p) {
                var labels = { today: "今日", "7d": "7日", "30d": "30日", custom: "自定义" };
                return (
                    '<button class="preset-btn ' +
                    (recordsFilter.preset === p ? "active" : "") +
                    '" data-preset="' +
                    p +
                    '">' +
                    labels[p] +
                    "</button>"
                );
            })
            .join("");
        var customStyle = recordsFilter.preset === "custom" ? "" : ' style="display:none"';
        var modelOpts =
            '<option value="">全部模型</option>' +
            cachedModels
                .map(function (m) {
                    return (
                        '<option value="' +
                        esc(m) +
                        '"' +
                        (recordsFilter.model === m ? " selected" : "") +
                        ">" +
                        esc(m) +
                        "</option>"
                    );
                })
                .join("");
        return (
            '<div class="toolbar records-toolbar">' +
            '<div class="preset-group">' +
            presetBtns +
            "</div>" +
            '<span class="custom-range"' +
            customStyle +
            "><input type='date' id='rec-start' value='" +
            esc(recordsFilter.start) +
            "'> ~ <input type='date' id='rec-end' value='" +
            esc(recordsFilter.end) +
            "'></span>" +
            "<select id='rec-model'>" +
            modelOpts +
            "</select>" +
            "<input id='rec-umo' placeholder='按会话 UMO 筛选' value='" +
            esc(recordsFilter.umo) +
            "'>" +
            "<input id='rec-provider' placeholder='Provider ID' value='" +
            esc(recordsFilter.provider) +
            "'>" +
            "<select id='rec-order'>" +
            "<option value='created_at'" +
            (recordsFilter.order_by === "created_at" ? " selected" : "") +
            ">按时间</option>" +
            "<option value='token_input_other'" +
            (recordsFilter.order_by === "token_input_other" ? " selected" : "") +
            ">按输入</option>" +
            "<option value='token_output'" +
            (recordsFilter.order_by === "token_output" ? " selected" : "") +
            ">按输出</option>" +
            "</select>" +
            "<button class='btn' id='rec-order-dir' title='升降序'>" +
            (recordsFilter.order_dir === "desc" ? "↓" : "↑") +
            "</button>" +
            "</div>"
        );
    }

    async function fetchRecords() {
        destroyCharts();
        var range = recordsRangeParams();
        var body =
            renderRecordsToolbar() +
            '<div id="rec-body" class="loading">加载中…</div>' +
            '<div class="panel agg-panel"><div class="agg-head"><h2 style="margin:0">交叉聚合</h2>' +
            '<span class="agg-switch"><button class="agg-btn ' +
            (aggMode === "model" ? "active" : "") +
            '" data-agg="model">按模型</button>' +
            '<button class="agg-btn ' +
            (aggMode === "umo" ? "active" : "") +
            '" data-agg="umo">按会话</button></span></div>' +
            '<div id="rec-agg" class="loading">加载聚合…</div></div>';
        $("content").innerHTML = body;
        bindRecordsToolbar();
        // 明细
        try {
            var rows = await api("records", {
                umo: recordsFilter.umo,
                provider: recordsFilter.provider,
                model: recordsFilter.model,
                start: range.start,
                end: range.end,
                order_by: recordsFilter.order_by,
                order_dir: recordsFilter.order_dir,
                limit: 300,
            });
            renderRecordsTable(rows);
        } catch (e) {
            $("rec-body").innerHTML =
                '<div class="error">加载失败：' + esc(e.message) + "</div>";
        }
        // 聚合
        try {
            var agg = await api("records/aggregate", {
                by: aggMode,
                umo: recordsFilter.umo,
                provider: recordsFilter.provider,
                model: recordsFilter.model,
                start: range.start,
                end: range.end,
            });
            renderRecordsAgg(agg);
        } catch (e) {
            $("rec-agg").innerHTML =
                '<div class="muted">聚合失败：' + esc(e.message) + "</div>";
        }
    }

    function renderRecordsTable(rows) {
        if (!rows || !rows.length) {
            $("rec-body").innerHTML = '<div class="empty">暂无明细记录</div>';
            return;
        }
        var sums = { input: 0, cached: 0, output: 0, creation: 0, cost: 0 };
        var html =
            '<div class="panel"><table><thead><tr>' +
            "<th>时间</th><th>会话</th><th>模型</th><th>Provider</th>" +
            "<th>输入</th><th>缓存</th><th>输出</th><th>cache写入</th><th>注入</th><th>成本</th>" +
            "</tr></thead><tbody>";
        rows.forEach(function (r) {
            sums.input += +r.token_input_other || 0;
            sums.cached += +r.token_input_cached || 0;
            sums.output += +r.token_output || 0;
            sums.creation += +r.cache_creation || 0;
            sums.cost += +r.cost || 0;
            html +=
                "<tr><td>" +
                shortTime(r.created_at) +
                '</td><td class="mono" title="' +
                esc(r.umo || "") +
                '">' +
                esc(shortUmo(r.umo)) +
                '</td><td class="mono" title="' +
                esc(r.provider_model || "") +
                '">' +
                esc(r.provider_model || "-") +
                "</td><td class='mono'>" +
                esc(r.provider_id || "-") +
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
                "</td><td>" +
                fmtCost(r.cost) +
                "</td></tr>";
        });
        html +=
            '</tbody><tfoot><tr class="sum-row"><td colspan="4">合计（' +
            rows.length +
            " 条）</td><td>" +
            fmtNum(sums.input) +
            "</td><td>" +
            fmtNum(sums.cached) +
            "</td><td>" +
            fmtNum(sums.output) +
            "</td><td>" +
            fmtNum(sums.creation) +
            "</td><td></td><td>" +
            fmtCost(sums.cost) +
            "</td></tr></tfoot></table></div>";
        $("rec-body").innerHTML = html;
    }

    function renderRecordsAgg(agg) {
        var groups = (agg && agg.groups) || [];
        if (!groups.length) {
            $("rec-agg").innerHTML = '<div class="empty">暂无聚合数据</div>';
            return;
        }
        var html =
            "<table><thead><tr><th>" +
            (aggMode === "model" ? "模型" : "会话") +
            "</th><th>调用</th><th>token 合计</th><th>成本</th><th>占比</th></tr></thead><tbody>";
        groups.forEach(function (g) {
            var pct = g.pct || 0;
            var cls = pct >= 50 ? "bad" : pct >= 25 ? "warn" : "";
            html +=
                "<tr><td class='mono'>" +
                esc(aggMode === "model" ? shortModelName(g.key) : shortUmo(g.key)) +
                "</td><td>" +
                fmtNum(g.count) +
                "</td><td>" +
                fmtNum(g.tokens) +
                "</td><td>" +
                fmtCost(g.cost) +
                '</td><td style="min-width:160px"><div class="row" style="align-items:center;gap:8px">' +
                '<div class="bar-wrap" style="flex:1"><div class="bar ' +
                cls +
                '" style="width:' +
                Math.min(100, pct) +
                '%"></div></div><span>' +
                pct +
                "%</span></div></td></tr>";
        });
        html += "</tbody></table>";
        $("rec-agg").innerHTML = html;
    }

    function bindRecordsToolbar() {
        var presetBtns = document.querySelectorAll(".preset-btn");
        for (var i = 0; i < presetBtns.length; i++) {
            (function (b) {
                b.onclick = function () {
                    recordsFilter.preset = b.dataset.preset;
                    fetchRecords();
                };
            })(presetBtns[i]);
        }
        var customRange = document.querySelector(".custom-range");
        var startEl = $("rec-start");
        var endEl = $("rec-end");
        if (startEl) {
            startEl.onchange = function () {
                recordsFilter.start = startEl.value;
                recordsFilter.preset = "custom";
                if (customRange) customRange.style.display = "";
            };
        }
        if (endEl) {
            endEl.onchange = function () {
                recordsFilter.end = endEl.value;
                recordsFilter.preset = "custom";
                if (customRange) customRange.style.display = "";
            };
        }
        var modelEl = $("rec-model");
        if (modelEl) {
            modelEl.onchange = function () {
                recordsFilter.model = modelEl.value;
                fetchRecords();
            };
        }
        var umoEl = $("rec-umo");
        if (umoEl) {
            umoEl.addEventListener("change", function () {
                recordsFilter.umo = umoEl.value.trim();
                fetchRecords();
            });
        }
        var provEl = $("rec-provider");
        if (provEl) {
            provEl.addEventListener("change", function () {
                recordsFilter.provider = provEl.value.trim();
                fetchRecords();
            });
        }
        var orderEl = $("rec-order");
        if (orderEl) {
            orderEl.onchange = function () {
                recordsFilter.order_by = orderEl.value;
                fetchRecords();
            };
        }
        var dirEl = $("rec-order-dir");
        if (dirEl) {
            dirEl.onclick = function () {
                recordsFilter.order_dir = recordsFilter.order_dir === "desc" ? "asc" : "desc";
                fetchRecords();
            };
        }
        var aggBtns = document.querySelectorAll(".agg-btn");
        for (var j = 0; j < aggBtns.length; j++) {
            (function (b) {
                b.onclick = function () {
                    aggMode = b.dataset.agg;
                    fetchRecords();
                };
            })(aggBtns[j]);
        }
    }

    // ===== 预算（可编辑表单） =====
    async function loadBudgets() {
        setLoading();
        try {
            var r = await api("budgets");
            var provs = [];
            try {
                var pr = await api("providers");
                provs = (pr && pr.providers) || [];
            } catch (e2) {
                provs = [];
            }
            renderBudgets(r, provs);
        } catch (e) {
            setError("加载预算失败：" + esc(e.message));
        }
    }

    function renderBudgets(r, provs) {
        var dims = r.dimensions || {};
        var budgetsTokens = Object.assign({}, r.limits || {});
        var budgetsCost = Object.assign({}, r.limits_cost || {});
        var metric = "token";
        var dimMeta = [
            ["per_session_daily", "单会话每日"],
            ["per_user_daily", "单用户每日"],
            ["per_model_daily", "单模型每日"],
            ["global_daily", "全局每日"],
            ["global_monthly", "全局每月"],
        ];
        var provHint = (provs || [])
            .map(function (p) {
                return p.id + (p.model ? "(" + p.model + ")" : "");
            })
            .join("、");
        var html =
            '<div class="panel"><div class="budget-head">' +
            '<h2>预算阈值</h2>' +
            '<div class="metric-switch">' +
            '<button type="button" class="metric-btn active" data-m="token">Token</button>' +
            '<button type="button" class="metric-btn" data-m="cost">花费 $</button>' +
            "</div></div>" +
            '<div id="budget-table"></div></div>';

        // 策略链容器（由 renderStrategies 动态填充）
        var provOpts = (provs || [])
            .map(function (p) {
                return (
                    '<option value="' +
                    esc(p.id) +
                    '">' +
                    esc(p.id + (p.model ? " (" + p.model + ")" : "")) +
                    "</option>"
                );
            })
            .join("");
        html +=
            '<div class="panel"><h2>超限处理策略（按序尝试）</h2>' +
            '<div class="muted small" style="margin-bottom:8px">超限时从上到下依次求值：<b>切换备用 Provider</b> 按其列表逐个尝试，首个成功即返回响应；全部失败或遇到 <b>拦截</b> 则终止。</div>' +
            (provHint
                ? '<div class="muted small" style="margin-bottom:8px">可用 Provider：' +
                  esc(provHint) +
                  "</div>"
                : "") +
            '<datalist id="prov-opts">' +
            provOpts +
            "</datalist>" +
            '<div id="strategy-list"></div>' +
            '<button class="btn" id="add-strategy" style="margin-top:8px">+ 添加策略</button>' +
            "</div>";

        html +=
            '<div class="row" style="align-items:center;gap:12px;margin-top:4px">' +
            '<button class="btn primary" id="save-budgets">保存（热生效）</button>' +
            "<span id='save-result' class='muted'></span></div>";

        $("content").innerHTML = html;

        // ===== 预算阈值表（token / 花费 双指标，切换重渲染） =====
        function renderBudgetTable() {
            var isCost = metric === "cost";
            var state = isCost ? budgetsCost : budgetsTokens;
            var h =
                "<table><thead><tr><th>维度</th><th>上限 " +
                (isCost ? "($)" : "(token)") +
                "，0=不限</th><th>当前消耗</th><th>进度</th></tr></thead><tbody>";
            dimMeta.forEach(function (d) {
                var key = d[0];
                var limit = state[key] || 0;
                var dim = (dims[key] || {})[metric] || {};
                var used = dim.used || 0;
                var ratio = dim.ratio || 0;
                var topKey = dim.top_key || "";
                var note = dim.note || "";
                var step = isCost ? ' step="0.01"' : "";
                var inputCell =
                    '<input type="number" min="0"' +
                    step +
                    ' class="budget-input" data-key="' +
                    key +
                    '" style="width:120px">';
                var usedInfo =
                    (isCost ? fmtCost(used) : fmtNum(used)) +
                    (topKey ? ' <span class="muted">(' + esc(topKey) + ")</span>" : "") +
                    (note ? '<div class="muted small">' + esc(note) + "</div>" : "");
                var prog =
                    limit <= 0 ? '<span class="muted">未设上限</span>' : bar(ratio, used, limit);
                h +=
                    "<tr><td>" +
                    d[1] +
                    "</td><td>" +
                    inputCell +
                    "</td><td>" +
                    usedInfo +
                    "</td><td style='min-width:200px'>" +
                    prog +
                    "</td></tr>";
            });
            h += "</tbody></table>";
            var box = $("budget-table");
            if (!box) return;
            box.innerHTML = h;
            // 回填 input 值 + onchange 写回当前指标的状态（切换指标后另一套状态保留）
            eachSel(".budget-input", function (el) {
                el.value = state[el.dataset.key] || 0;
                el.onchange = function () {
                    var v;
                    if (isCost) {
                        v = +el.value || 0;
                        if (v < 0) v = 0;
                    } else {
                        v = Math.max(0, parseInt(el.value, 10) || 0);
                    }
                    state[el.dataset.key] = v;
                };
            });
        }

        eachSel(".metric-btn", function (el) {
            el.onclick = function () {
                metric = el.dataset.m;
                eachSel(".metric-btn", function (b) {
                    b.classList.toggle("active", b.dataset.m === metric);
                });
                renderBudgetTable();
            };
        });
        renderBudgetTable();

        // 策略链状态（可变，编辑后整块重渲染）
        var strategies = (r.strategies || []).map(function (s) {
            return {
                action: s.action || "stop_llm",
                provider_ids: Array.isArray(s.provider_ids)
                    ? s.provider_ids.slice()
                    : [],
                token_limit: s.token_limit || 0,
                message: s.message || "",
                enabled: s.enabled !== false,
            };
        });

        function eachSel(sel, fn) {
            var nodes = document.querySelectorAll(sel);
            for (var k = 0; k < nodes.length; k++) fn(nodes[k]);
        }

        function renderStrategies() {
            var box = $("strategy-list");
            if (!box) return;
            if (!strategies.length) {
                box.innerHTML =
                    '<div class="muted small">暂无策略（超限时默认拦截）</div>';
                return;
            }
            var h = "";
            strategies.forEach(function (s, i) {
                var fb = s.action === "fallback_provider";
                h +=
                    '<div class="strategy-card' +
                    (s.enabled ? "" : " is-disabled") +
                    '">';
                h += '<div class="strategy-head">';
                h += '<span class="strategy-idx">' + (i + 1) + "</span>";
                h += '<select class="s-action" data-i="' + i + '">';
                h +=
                    '<option value="stop_llm"' +
                    (fb ? "" : " selected") +
                    ">拦截 LLM 请求</option>";
                h +=
                    '<option value="fallback_provider"' +
                    (fb ? " selected" : "") +
                    ">切换备用 Provider</option>";
                h += "</select>";
                h +=
                    '<label class="s-enabled"><input type="checkbox" class="s-enabled-cb" data-i="' +
                    i +
                    '"' +
                    (s.enabled ? " checked" : "") +
                    "> 启用</label>";
                h += '<span class="strategy-move">';
                h +=
                    '<button type="button" class="move-btn" data-dir="up" data-i="' +
                    i +
                    '"' +
                    (i === 0 ? " disabled" : "") +
                    ">↑</button>";
                h +=
                    '<button type="button" class="move-btn" data-dir="down" data-i="' +
                    i +
                    '"' +
                    (i === strategies.length - 1 ? " disabled" : "") +
                    ">↓</button>";
                h +=
                    '<button type="button" class="move-btn del" data-dir="del" data-i="' +
                    i +
                    '">✕</button>';
                h += "</span></div>"; // head
                h += '<div class="strategy-field">';
                if (fb) {
                    h +=
                        '<div class="field-row"><span class="muted small">备用 Provider（按序尝试）</span></div>';
                    h += '<div class="provider-tags">';
                    s.provider_ids.forEach(function (pid, j) {
                        h +=
                            '<span class="provider-tag">' +
                            esc(pid) +
                            '<button type="button" class="tag-del" data-i="' +
                            i +
                            '" data-j="' +
                            j +
                            '">✕</button></span>';
                    });
                    if (!s.provider_ids.length)
                        h +=
                            '<span class="muted small">（空，此策略将被跳过）</span>';
                    h += "</div>";
                    h +=
                        '<div class="field-row"><input type="text" list="prov-opts" class="pid-input" data-i="' +
                        i +
                        '" placeholder="选择或输入 Provider ID 后回车添加"></div>';
                    h +=
                        '<div class="field-row"><label>token 上限 <input type="number" min="0" class="s-token" data-i="' +
                        i +
                        '" value="' +
                        (s.token_limit || 0) +
                        '" style="width:100px"> <span class="muted small">截断历史，0=不限</span></label></div>';
                } else {
                    // 文本值由 JS 回填（esc 不转义引号，避免属性注入）
                    h +=
                        '<div class="field-row"><label>拦截文案 <input type="text" class="s-message" data-i="' +
                        i +
                        '" style="flex:1" placeholder="留空=默认文案"></label></div>';
                }
                h += "</div></div>"; // field + card
            });
            box.innerHTML = h;
            eachSel(".s-message", function (el) {
                el.value = strategies[+el.dataset.i].message || "";
            });
            wireStrategyEvents();
        }

        function wireStrategyEvents() {
            eachSel(".s-action", function (el) {
                el.onchange = function () {
                    strategies[+el.dataset.i].action = el.value;
                    renderStrategies();
                };
            });
            eachSel(".s-enabled-cb", function (el) {
                el.onchange = function () {
                    strategies[+el.dataset.i].enabled = el.checked;
                    renderStrategies();
                };
            });
            eachSel(".move-btn", function (el) {
                el.onclick = function () {
                    var i = +el.dataset.i;
                    var dir = el.dataset.dir;
                    if (dir === "del") {
                        strategies.splice(i, 1);
                    } else if (dir === "up" && i > 0) {
                        strategies.splice(i - 1, 2, strategies[i], strategies[i - 1]);
                    } else if (dir === "down" && i < strategies.length - 1) {
                        strategies.splice(i, 2, strategies[i + 1], strategies[i]);
                    }
                    renderStrategies();
                };
            });
            eachSel(".tag-del", function (el) {
                el.onclick = function () {
                    strategies[+el.dataset.i].provider_ids.splice(+el.dataset.j, 1);
                    renderStrategies();
                };
            });
            eachSel(".pid-input", function (el) {
                el.onkeydown = function (e) {
                    if (e.key === "Enter" || e.keyCode === 13) {
                        e.preventDefault();
                        var v = el.value.trim();
                        if (v) {
                            strategies[+el.dataset.i].provider_ids.push(v);
                            renderStrategies();
                        }
                    }
                };
            });
            eachSel(".s-token", function (el) {
                el.onchange = function () {
                    strategies[+el.dataset.i].token_limit = +el.value || 0;
                };
            });
            eachSel(".s-message", function (el) {
                el.onchange = function () {
                    strategies[+el.dataset.i].message = el.value;
                };
            });
        }

        renderStrategies();

        $("add-strategy").onclick = function () {
            strategies.push({
                action: "stop_llm",
                provider_ids: [],
                token_limit: 0,
                message: "",
                enabled: true,
            });
            renderStrategies();
        };

        $("save-budgets").onclick = async function () {
            var body = {
                budgets: budgetsTokens,
                budgets_cost: budgetsCost,
                over_limit_strategies: strategies,
            };
            $("save-result").textContent = "保存中…";
            try {
                var res = await apiPost("actions/save_config", body);
                $("save-result").textContent =
                    "✅ 已保存（" + ((res && res.saved) || []).join(", ") + "），立即生效";
                await loadBudgets();
            } catch (e) {
                $("save-result").textContent = "❌ 保存失败：" + e.message;
            }
        };
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

    // ===== 缓存 / 归因 / 定价 / 设置（沿用既有实现） =====
    async function loadCache() {
        setLoading();
        try {
            var r = await api("cache");
            var cards = [
                {
                    label: "平均缓存命中率",
                    value: (r.cache_hit_rate || 0) + "%",
                    sub: (r.samples || 0) + " 样本",
                },
                { label: "破坏事件", value: fmtNum((r.events || []).length) },
                {
                    label: "非缓存输入 token",
                    value: fmtNum(r.total_input_other || 0),
                    sub: "可经提升命中率优化",
                },
            ];
            var html = cardsBlock(cards);
            html +=
                '<div class="panel potential"><h2>优化潜力</h2>' +
                '<div class="alert-body">缓存命中单价通常为非缓存的 <strong>1/10</strong>。' +
                "当前非缓存输入 <strong>" +
                fmtNum(r.total_input_other || 0) +
                "</strong> token，提升命中率可直接降低这部分输入成本。" +
                "重点排查：system prompt 稳定性、上下文是否被重置、工具定义是否频繁变化。</div></div>";
            var events = r.events || [];
            html += '<div class="panel"><h2>缓存破坏事件（最近）</h2>';
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
            var r = await api("attribution");
            var avg = r.avg_components || {};
            var cards = [
                { label: "system 平均", value: fmtNum(avg.system) },
                { label: "tools 平均", value: fmtNum(avg.tools) },
                { label: "history 平均", value: fmtNum(avg.history) },
                { label: "user 平均", value: fmtNum(avg.user) },
            ];
            var html = cardsBlock(cards);
            // 组件占比堆叠条（平均）
            var comps = [
                { k: "system", v: avg.system || 0, c: "var(--accent)" },
                { k: "tools", v: avg.tools || 0, c: "#8ab4ff" },
                { k: "history", v: avg.history || 0, c: "var(--warn)" },
                { k: "user", v: avg.user || 0, c: "var(--ok)" },
            ];
            var totalAttr = comps.reduce(function (s, c) {
                return s + (c.v || 0);
            }, 0);
            html += '<div class="panel"><h2>组件占比（平均）</h2>';
            if (totalAttr > 0) {
                var barHtml = '<div class="stacked-bar">';
                comps.forEach(function (c) {
                    var pct = Math.round((c.v * 100) / totalAttr);
                    if (pct > 0) {
                        barHtml +=
                            '<div class="stacked-seg" style="width:' +
                            pct +
                            "%;background:" +
                            c.c +
                            '" title="' +
                            esc(c.k) +
                            " " +
                            pct +
                            '%">' +
                            (pct >= 8 ? pct + "%" : "") +
                            "</div>";
                    }
                });
                barHtml += "</div>";
                barHtml += '<div class="legend">';
                comps.forEach(function (c) {
                    var pct = totalAttr > 0 ? Math.round((c.v * 100) / totalAttr) : 0;
                    barHtml +=
                        '<span class="legend-item"><span class="legend-dot" style="background:' +
                        c.c +
                        '"></span>' +
                        esc(c.k) +
                        " " +
                        fmtNum(c.v) +
                        " (" +
                        pct +
                        "%)</span>";
                });
                barHtml += "</div>";
                html += barHtml;
                var histPct =
                    totalAttr > 0 ? Math.round(((avg.history || 0) * 100) / totalAttr) : 0;
                if (histPct >= 40) {
                    html +=
                        '<div class="alert-body" style="margin-top:10px">history 占注入的 <strong>' +
                        histPct +
                        "%</strong>，是可优化的主要部分——精简历史可显著降低每轮输入 token。</div>";
                }
            } else {
                html += '<div class="empty">暂无组件数据</div>';
            }
            html += "</div>";
            var recent = r.recent || [];
            html += '<div class="panel"><h2>最近请求归因</h2>';
            if (!recent.length) {
                html += '<div class="empty">暂无归因数据</div>';
            } else {
                html +=
                    '<table><thead><tr><th>时间</th><th>会话</th><th>注入 token</th>' +
                    "<th>system</th><th>tools</th><th>history</th><th>user</th></tr></thead><tbody>";
                recent.forEach(function (it) {
                    var a = it.attribution || {};
                    html +=
                        "<tr><td>" +
                        shortTime(it.created_at) +
                        '</td><td class="mono" title="' +
                        esc(it.umo || "") +
                        '">' +
                        esc(it.umo || "-") +
                        "</td><td>" +
                        (it.injection_total == null ? "-" : fmtNum(it.injection_total)) +
                        "</td><td>" +
                        fmtNum(a.system) +
                        "</td><td>" +
                        fmtNum(a.tools) +
                        "</td><td>" +
                        fmtNum(a.history) +
                        "</td><td>" +
                        fmtNum(a.user) +
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
            var data = await api("pricing");
            var pricing = (data && data.pricing) || {};
            var unpriced = (data && data.unpriced) || [];
            var keys = Object.keys(pricing).sort();
            var html = "";
            if (unpriced.length) {
                html +=
                    '<div class="panel alert-panel"><h2>未定价模型告警</h2>' +
                    '<div class="alert-body">以下模型有用量但未配置单价，其成本被计为 <strong>$0</strong>，' +
                    "会导致成本统计偏低。请在插件配置的 <code>pricing</code> 项补充单价。</div>" +
                    '<table><thead><tr><th>模型</th><th>用量 token</th><th>调用</th></tr></thead><tbody>';
                unpriced.forEach(function (u) {
                    html +=
                        "<tr><td class='mono'>" +
                        esc(u.model) +
                        "</td><td>" +
                        fmtNum(u.tokens) +
                        "</td><td>" +
                        fmtNum(u.count) +
                        "</td></tr>";
                });
                html += "</tbody></table></div>";
            }
            if (!keys.length) {
                html += '<div class="empty">暂无定价数据</div>';
            } else {
                html +=
                    '<div class="panel"><h2>模型单价（USD / 百万 token）</h2>' +
                    '<table><thead><tr><th>模型</th><th>输入</th><th>缓存命中</th>' +
                    "<th>输出</th><th>缓存写入</th></tr></thead><tbody>";
                keys.forEach(function (k) {
                    var p = pricing[k] || {};
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
            }
            $("content").innerHTML = html;
        } catch (e) {
            setError("加载定价失败：" + esc(e.message));
        }
    }

    async function loadSettings() {
        setLoading();
        try {
            var cfg = (await api("config")) || {};
            // 配置区块定义（_master = 顶层标量；其余按 object 键嵌套）
            var SECTIONS = [
                {
                    key: "_master",
                    title: "总开关",
                    fields: [
                        { k: "enabled", label: "启用插件", type: "bool" },
                        { k: "refresh_time", label: "每日重置时间 (HH:MM)", type: "str" },
                        { k: "match_unique_session", label: "匹配唯一会话", type: "bool" },
                        { k: "platforms", label: "生效平台（逗号分隔，空=全部）", type: "csv" },
                    ],
                },
                {
                    key: "cache_diag",
                    title: "缓存诊断",
                    fields: [
                        { k: "detect_context_reset", label: "上下文重置检测", type: "bool" },
                        { k: "detect_system_prompt_change", label: "system prompt 变更检测", type: "bool" },
                        { k: "detect_tools_change", label: "工具定义变更检测", type: "bool" },
                        { k: "detect_order_drift", label: "上下文顺序漂移检测", type: "bool" },
                        { k: "cache_hit_rate_alert_threshold", label: "命中率告警阈值 (%)，0=不告警", type: "int" },
                    ],
                },
                {
                    key: "alerts",
                    title: "告警",
                    fields: [
                        { k: "enabled", label: "启用超预算主动推送", type: "bool" },
                        { k: "cooldown_seconds", label: "冷却时间（秒）", type: "int" },
                        { k: "daily_report_time", label: "日报推送时间 (HH:MM，空=不推)", type: "str" },
                        { k: "daily_report_to", label: "日报目标 UMO（逗号分隔）", type: "csv" },
                    ],
                },
                {
                    key: "prompt_optimizer",
                    title: "提示词优化",
                    fields: [
                        { k: "enabled", label: "启用 /optimize", type: "bool" },
                        { k: "provider_id", label: "改写 Provider ID（空=当前会话）", type: "str" },
                        { k: "max_static_analysis_length", label: "静态分析最大长度（字符）", type: "int" },
                    ],
                },
                {
                    key: "attribution",
                    title: "归因分析",
                    fields: [
                        { k: "enabled", label: "启用上下文注入归因", type: "bool" },
                        { k: "sample_rate", label: "采样率 (%)，100=全采样", type: "int" },
                    ],
                },
                {
                    key: "schedule",
                    title: "定时任务",
                    fields: [
                        { k: "enable_daily_report", label: "启用每日报告 CronJob", type: "bool" },
                        { k: "retain_days", label: "历史保留天数（0=永不清理）", type: "int" },
                    ],
                },
            ];

            function valOf(sec, k) {
                var v = sec === "_master" ? cfg[k] : (cfg[sec] || {})[k];
                return v === undefined || v === null ? "" : v;
            }

            var html = "";
            SECTIONS.forEach(function (sec) {
                html += '<div class="panel"><h2>' + esc(sec.title) + "</h2>";
                sec.fields.forEach(function (f) {
                    var v = valOf(sec.key, f.k);
                    var id = "sf-" + sec.key + "-" + f.k;
                    html += '<div class="set-row">';
                    if (f.type === "bool") {
                        html +=
                            '<label><input type="checkbox" class="set-input" data-sec="' +
                            sec.key + '" data-k="' + f.k + '" data-type="bool"' +
                            (v ? " checked" : "") + "> " + esc(f.label) + "</label>";
                    } else if (f.type === "csv") {
                        html +=
                            '<label style="flex:1">' + esc(f.label) +
                            ' <input type="text" class="set-input budget-input" data-sec="' +
                            sec.key + '" data-k="' + f.k + '" data-type="csv" value="' +
                            esc(Array.isArray(v) ? v.join(", ") : String(v)) +
                            '" style="width:100%"></label>';
                    } else {
                        html +=
                            '<label style="flex:1">' + esc(f.label) +
                            ' <input type="' + (f.type === "int" ? "number" : "text") +
                            '" class="set-input budget-input" data-sec="' + sec.key +
                            '" data-k="' + f.k + '" data-type="' + f.type + '" value="' +
                            esc(String(v)) + '" style="width:160px"></label>';
                    }
                    html += "</div>";
                });
                html += "</div>";
            });

            // 定价表（动态 map，用 JSON textarea）
            html +=
                '<div class="panel"><h2>定价表（USD / 百万 token）</h2>' +
                '<div class="muted small" style="margin-bottom:6px">覆盖/新增模型单价，键=模型名，值含 input/input_cached/output/cache_creation。留空对象 {} 则只用内置默认价</div>' +
                '<textarea id="set-pricing" class="mono" style="width:100%;height:160px">' +
                esc(JSON.stringify(cfg.pricing || {}, null, 2)) +
                "</textarea></div>";

            // 手动操作
            html +=
                '<div class="panel"><h2>手动操作</h2><div class="row">' +
                '<button class="btn" id="act-cleanup">清理过期数据</button>' +
                '<button class="btn" id="act-report">推送日报</button>' +
                "</div><div id='action-result' class='muted' style='margin-top:8px'></div></div>";

            html +=
                '<div class="row" style="align-items:center;gap:12px;margin-top:4px">' +
                '<button class="btn primary" id="save-settings">保存（热生效）</button>' +
                "<span id='save-result' class='muted'></span></div>";

            $("content").innerHTML = html;

            // 回填文本值（避免属性引号注入）
            document.querySelectorAll(".set-input[data-type='str'],.set-input[data-type='int']").forEach(function (el) {
                el.value = valOf(el.dataset.sec, el.dataset.k) === "" ? "" : String(valOf(el.dataset.sec, el.dataset.k));
            });

            function collect() {
                var body = {};
                var setSec = function (sec) {
                    if (sec === "_master") return body;
                    body[sec] = body[sec] || {};
                    return body[sec];
                };
                var nodes = document.querySelectorAll(".set-input");
                for (var i = 0; i < nodes.length; i++) {
                    var el = nodes[i];
                    var sec = el.dataset.sec;
                    var k = el.dataset.k;
                    var t = el.dataset.type;
                    var target = setSec(sec);
                    if (t === "bool") target[k] = el.checked;
                    else if (t === "int") target[k] = Math.max(0, parseInt(el.value, 10) || 0);
                    else if (t === "csv") {
                        target[k] = el.value
                            .split(",")
                            .map(function (s) { return s.trim(); })
                            .filter(Boolean);
                    } else target[k] = el.value;
                }
                // pricing
                var prText = $("set-pricing").value || "{}";
                try {
                    body.pricing = JSON.parse(prText);
                    if (typeof body.pricing !== "object" || body.pricing === null) body.pricing = {};
                } catch (pe) {
                    throw new Error("定价表 JSON 解析失败：" + pe.message);
                }
                return body;
            }

            $("save-settings").onclick = async function () {
                var body;
                try {
                    body = collect();
                } catch (ce) {
                    $("save-result").textContent = "❌ " + ce.message;
                    return;
                }
                $("save-result").textContent = "保存中…";
                try {
                    var res = await apiPost("actions/save_config", body);
                    $("save-result").textContent =
                        "✅ 已保存（" + ((res && res.saved) || []).join(", ") + "），立即生效";
                    cfg = (res && res.config) || cfg;
                } catch (e) {
                    $("save-result").textContent = "❌ 保存失败：" + e.message;
                }
            };

            $("act-cleanup").onclick = async function () {
                $("action-result").textContent = "执行中…";
                try {
                    var rc = await apiPost("actions/cleanup");
                    $("action-result").textContent =
                        "已清理 " + fmtNum((rc && rc.deleted) || 0) + " 条记录";
                } catch (e) {
                    $("action-result").textContent = "失败：" + e.message;
                }
            };
            $("act-report").onclick = async function () {
                $("action-result").textContent = "执行中…";
                try {
                    await apiPost("actions/report");
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
        destroyCharts(); // 切 tab 前清理上个 tab 的 Chart 实例
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
        // 主题切换后更新 Chart 默认色 + 刷新当前图表
        if (chartAvailable()) {
            try {
                Chart.defaults.color = cssVar("--text-dim") || "#6b7280";
            } catch (e) {
                /* noop */
            }
        }
    }

    async function init() {
        Page = await waitForBridge(5000);
        if (!Page) {
            setError("bridge SDK 未注入（请在 AstrBot WebUI 插件页打开本页面）");
            return;
        }
        try {
            await Page.ready();
        } catch (e) {
            /* ready 失败也继续尝试调用 */
        }
        bridgeReady = true;

        try {
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
            /* 上下文读取失败不阻断 */
        }

        $("status").textContent = "已连接";

        var tabs = document.querySelectorAll(".tab");
        for (var i = 0; i < tabs.length; i++) {
            (function (t) {
                t.onclick = function () {
                    switchTab(t.dataset.tab);
                };
            })(tabs[i]);
        }

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
