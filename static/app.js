(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const tokenInput = $("#token");
  const logBox = $("#log");
  const busy = $("#busy");
  const appBaseUrl = new URL("./", window.location.href);
  tokenInput.value = localStorage.getItem("weeklyReportAdminToken") || "";

  const resolveUrl = (path) => new URL(String(path || "").replace(/^\//, ""), appBaseUrl).toString();

  const log = (label, value) => {
    const stamp = new Date().toLocaleString();
    const content = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    logBox.textContent = `[${stamp}] ${label}\n${content}\n\n${logBox.textContent}`;
  };

  const api = async (path, options = {}) => {
    const headers = {"Content-Type": "application/json", ...(options.headers || {})};
    if (tokenInput.value.trim()) headers.Authorization = `Bearer ${tokenInput.value.trim()}`;
    const response = await fetch(resolveUrl(path), {...options, headers});
    let body;
    try { body = await response.json(); } catch (_) { body = {detail: await response.text()}; }
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    return body;
  };

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
  const checkCard = (name, value, detail = "") => `<div class="check ${value ? "ok" : "bad"}"><strong>${escapeHtml(name)}</strong><span>${value ? "已就绪" : "待配置"}${detail ? ` · ${escapeHtml(detail)}` : ""}</span></div>`;

  const loadReadiness = async () => {
    const data = await api("/api/readiness");
    const c = data.checks;
    $("#readiness").innerHTML = [
      checkCard("钉钉应用", c.dingtalkApp), checkCard("AI 多维表", c.aiTable),
      checkCard("bi_center 人员", c.biCenter, `${c.directoryCache.count || 0} 人`),
      checkCard("AI 摘要", c.aiSummary), checkCard("公开图片链接", c.publicLinks),
      checkCard("回调鉴权", c.callbackAuth),
      checkCard("推送目标", c.deliveryTargets?.ready, `预览 ${c.deliveryTargets?.preview || 0} / 正式 ${c.deliveryTargets?.formal || 0}`),
      checkCard("周报归档", c.archive?.ready, c.archive?.enabled ? `已启用 · ${c.archive?.mappedFields || 0} 个字段` : "未启用（可选）"),
      checkCard("总体链路", data.ready)
    ].join("");
  };

  const loadCoverage = async () => {
    const kind = $("#reportKind")?.value || "combined";
    const data = await api(`/api/coverage?report_kind=${encodeURIComponent(kind)}`);
    const missing = data.missing || [];
    $("#coverage").innerHTML = `<div class="coverage-summary"><strong>应覆盖 ${data.expectedCount || 0} 人</strong><span>已覆盖 ${data.coveredCount || 0}</span><span class="${missing.length ? "warn" : "ok-text"}">缺少 ${data.missingCount || 0}</span></div>` +
      (missing.length ? `<div class="coverage-people">${missing.map((item) => `<span>${escapeHtml(item.name || item.userId)} · ${item.role === "project" ? "项目经理" : "产品经理"}${item.department ? ` · ${escapeHtml(item.department)}` : ""}</span>`).join("")}</div>` : '<p class="safe">当前名单已全部覆盖。</p>');
  };

  const loadEvents = async () => {
    const data = await api("/api/dingtalk/robot/events?limit=20");
    $("#robotEvents").innerHTML = data.items.length ? data.items.map((item) => `<article class="event"><strong>${escapeHtml(item.conversation_title || "单聊")}</strong><span>${escapeHtml(item.sender_nick || item.sender_id || "未知发送人")} · ${escapeHtml(item.command || "unknown")} · ${escapeHtml(item.handle_status || "")}</span><code>${escapeHtml(item.conversation_id || "private")} / ${escapeHtml(item.robot_code || "-")}</code></article>`).join("") : '<p class="muted">暂无回调事件。</p>';
  };

  const actionButton = (id, action, label, css = "") => `<button class="${css}" data-report-id="${id}" data-report-action="${action}">${label}</button>`;
  const loadReports = async () => {
    const data = await api("/api/reports?limit=30");
    $("#reports").innerHTML = data.items.length ? data.items.map((item) => {
      const metrics = item.metrics || {};
      return `<article class="report"><div><h3>#${item.id} ${escapeHtml(item.title)} <span class="badge">${escapeHtml(item.workflowState)}</span></h3><p>${escapeHtml(item.window?.label || item.periodKey)} · ${escapeHtml(item.reportKind)} · v${item.version}</p><p>事项 ${metrics.itemCount || 0} · 风险 ${metrics.riskCount || 0} · 逾期 ${metrics.overdueCount || 0} · 缺覆盖 ${metrics.coverage?.missingCount || 0} · AI ${escapeHtml(item.aiStatus || "-")} · 归档 ${escapeHtml(item.archive?.status || "未执行")}</p></div><div class="actions">${actionButton(item.id,"detail","查看/编辑","secondary")}${actionButton(item.id,"render","生成图片","secondary")}${actionButton(item.id,"preview","发送预览")}${actionButton(item.id,"approve","仅审核","secondary")}${actionButton(item.id,"formal","正式发送")}${actionButton(item.id,"archive","归档/重试","secondary")}${actionButton(item.id,"recall","撤回","danger")}</div></article>`;
    }).join("") : '<p class="muted">暂无周报，先同步数据并生成草稿。</p>';
  };

  let activeReportId = null;
  const openReport = async (id) => {
    const report = await api(`/api/reports/${id}`);
    activeReportId = id;
    $("#reportDialogTitle").textContent = `#${id} ${report.title}`;
    $("#reportDialogMeta").textContent = `${report.window?.label || report.periodKey} · ${report.reportKind} · v${report.version} · ${report.workflowState} · 归档 ${report.archive?.status || "未执行"}${report.archive?.error ? `（${report.archive.error}）` : ""}`;
    $("#sectionEditor").value = JSON.stringify(report.sections || {}, null, 2);
    $("#reportSources").innerHTML = (report.sources || []).length ? report.sources.map((item) => `<article><strong>${escapeHtml(item.title || "未命名事项")}</strong><span>${escapeHtml(item.category || "")} · ${escapeHtml(item.status || "未标记")}</span><p>${escapeHtml(item.progressText || item.planText || item.riskText || "暂无详情")}</p></article>`).join("") : '<p class="muted">本版周报未纳入事实记录。</p>';
    $("#reportDialog").showModal();
  };

  const loadConfig = async () => {
    const data = await api("/api/config");
    $("#configEditor").value = JSON.stringify(data.config, null, 2);
    $("#defaultRobotCode").value = data.config.defaultRobotCode || "";
    $("#archiveWriteEnabled").checked = Boolean(data.config.archiveWriteEnabled);
    $("#archiveTableId").value = data.config.archiveTableId || "";
    $("#archiveFieldMap").value = JSON.stringify(data.config.archiveFieldMap || {}, null, 2);
  };

  const searchRecipients = async () => {
    const query = $("#recipientSearch").value.trim();
    const data = await api(`/api/directory?query=${encodeURIComponent(query)}&limit=100`);
    $("#recipientSelect").innerHTML = data.items.length ? data.items.map((item) => `<option value="${escapeHtml(item.userId)}" data-name="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${escapeHtml(item.department || item.bizGroup || item.title || "未标注部门")}</option>`).join("") : '<option value="">未找到人员，请先同步 bi_center</option>';
    return data;
  };

  const refresh = async () => {
    busy.textContent = "读取中…";
    try { await Promise.all([loadReadiness(), loadReports(), loadConfig(), loadCoverage(), loadEvents()]); }
    catch (error) { log("刷新失败", error.message); }
    finally { busy.textContent = ""; }
  };

  const run = async (label, callback) => {
    busy.textContent = `${label}处理中…`;
    try { const data = await callback(); log(`${label}成功`, data); await Promise.all([loadReadiness(), loadReports()]); }
    catch (error) { log(`${label}失败`, error.message); }
    finally { busy.textContent = ""; }
  };

  $("#saveToken").addEventListener("click", () => { localStorage.setItem("weeklyReportAdminToken", tokenInput.value.trim()); refresh(); });
  $("#refreshAll").addEventListener("click", refresh);
  $("#refreshCoverage").addEventListener("click", () => run("刷新人员覆盖", loadCoverage));
  $("#remindCoverage").addEventListener("click", () => {
    if (!window.confirm("确定只向当前缺报人员发送一次钉钉单聊提醒？")) return;
    run("提醒缺报人员", () => api("/api/coverage/remind", {
      method:"POST", body:JSON.stringify({reportKind:$("#reportKind").value})
    }));
  });
  $("#refreshEvents").addEventListener("click", () => run("刷新机器人事件", loadEvents));
  $("#searchRecipient").addEventListener("click", () => run("搜索个人接收人", searchRecipients));
  $("#applyPersonalTarget").addEventListener("click", () => {
    const option = $("#recipientSelect").selectedOptions[0];
    const userId = option?.value || "";
    if (!userId) return log("配置个人接收人失败", "请先选择人员");
    try {
      const config = JSON.parse($("#configEditor").value || "{}");
      const target = {name: option.dataset.name || option.textContent.split(" · ")[0], userId, enabled: true};
      config.defaultRobotCode = $("#defaultRobotCode").value.trim();
      config.previewPersonalTargets = [target];
      config.formalPersonalTargets = [target];
      config.approverTargets = [target];
      config.previewGroupTargets = [];
      config.formalGroupTargets = [];
      $("#configEditor").value = JSON.stringify(config, null, 2);
      log("已生成个人推送配置", `${target.name}（${target.userId}），请点击“保存配置”生效。`);
    } catch (error) { log("配置个人接收人失败", error.message); }
  });
  $("#clearLog").addEventListener("click", () => { logBox.textContent = "等待操作。"; });
  $("#closeReportDialog").addEventListener("click", () => $("#reportDialog").close());
  $("#saveSections").addEventListener("click", () => run("保存周报正文", async () => {
    if (!activeReportId) throw new Error("未选择周报");
    const sections = JSON.parse($("#sectionEditor").value);
    const data = await api(`/api/reports/${activeReportId}/sections`, {method:"PUT", body:JSON.stringify({sections})});
    $("#reportDialog").close();
    return data;
  }));
  $("#saveConfig").addEventListener("click", () => run("保存配置", async () => {
    const config = JSON.parse($("#configEditor").value);
    config.defaultRobotCode = $("#defaultRobotCode").value.trim();
    config.archiveWriteEnabled = $("#archiveWriteEnabled").checked;
    config.archiveTableId = $("#archiveTableId").value.trim();
    config.archiveFieldMap = JSON.parse($("#archiveFieldMap").value || "{}");
    const data = await api("/api/config", {method:"PUT", body:JSON.stringify({config})});
    $("#configEditor").value = JSON.stringify(data.config, null, 2);
    return data;
  }));

  document.addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "sync-source") run("同步 AI 表", () => api("/api/sync/source", {method:"POST"}));
    if (action === "sync-directory") run("同步人员目录", () => api("/api/sync/directory", {method:"POST"}));
    if (action === "generate") run("生成周报", () => api("/api/reports/generate", {method:"POST", body:JSON.stringify({reportKind:$("#reportKind").value,useAI:true})}));

    const reportButton = event.target.closest("[data-report-action]");
    if (!reportButton) return;
    const id = reportButton.dataset.reportId;
    const reportAction = reportButton.dataset.reportAction;
    if (reportAction === "detail") {
      run("读取周报详情", () => openReport(id));
      return;
    }
    const routes = {render:["生成图片",`/api/reports/${id}/render`],preview:["发送预览",`/api/reports/${id}/preview`],approve:["审核通过",`/api/reports/${id}/approve`],formal:["正式发送",`/api/reports/${id}/formal-send`],archive:["归档",`/api/reports/${id}/archive`],recall:["撤回消息",`/api/reports/${id}/recall`]};
    const [label, path] = routes[reportAction];
    if (["formal","archive","recall"].includes(reportAction) && !window.confirm(`确定要${label}周报 #${id}？`)) return;
    run(label, () => api(path, {method:"POST"}));
  });

  if (tokenInput.value) refresh();
})();
