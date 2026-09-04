(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const tokenInput = $("#token");
  const logBox = $("#log");
  const busy = $("#busy");
  const toast = $("#toast");
  const appBaseUrl = new URL("./", window.location.href);
  const SECTION_KEYS = [
    "executiveSummary", "productHighlights", "projectHighlights",
    "risks", "nextPlans", "supportNeeds",
  ];
  const ROUTES = {
    overview: {eyebrow: "WORKSPACE", title: "工作台概览", subtitle: "掌握数据、生成、审核与推送状态"},
    teambition: {eyebrow: "TEAM WORK EXECUTION", title: "TB 重点项目看板", subtitle: "仅展示多维表重点项目匹配到的任务与项目状态"},
    "key-project-status": {eyebrow: "KEY PROJECT STATUS", title: "重点项目TB项目状态", subtitle: "多维表重点项目与TB最新状态的一一对应视图"},
    reports: {eyebrow: "REPORT CENTER", title: "周报展示", subtitle: "当前成稿、正式周报与历史版本"},
    "personal-reports": {eyebrow: "PERSONAL WEEKLY", title: "个人周报", subtitle: "我的总结与授权成员明细"},
    "report-config": {eyebrow: "REPORT SETTINGS", title: "周报配置", subtitle: "周期口径、项目背景与存档规则"},
    "model-config": {eyebrow: "MODEL GATEWAY", title: "模型配置", subtitle: "沿用 bi_center 的统一模型配置"},
    delivery: {eyebrow: "DELIVERY CONTROL", title: "推送设置", subtitle: "测试目标、正式目标与人工确认"},
  };
  const STATUS_LABELS = {
    draft_generated: "待编辑", rendered: "已生成图片", awaiting_approval: "待确认", approved: "已确认",
    formal_sent: "已正式发送", need_changes: "待修改", retryable_error: "可重试",
    recalled: "已撤回", cancelled: "已取消",
  };
  const KIND_LABELS = {combined: "综合版", product: "产品经理版", project: "项目经理版"};
  const PROJECT_STATUS = {active: "进行中", waiting: "等待需求", paused: "暂停", done: "已完成"};

  let workflowConfig = {};
  let modelConfig = null;
  let reportItems = [];
  let eventItems = [];
  let latestReadiness = null;
  let latestCoverage = null;
  let teambitionData = null;
  let keyProjectStatusData = null;
  let personalContext = null;
  let activePersonalUserId = "";
  let personalMemberSearch = "";
  let activePersonalReport = null;
  let personalOriginalEdit = "";
  let personalIsDirty = false;
  let activeReportId = null;
  let reportOriginalEdit = "";
  let reportIsDirty = false;
  let reportFilter = "current";
  let toastTimer = null;
  let accessConnected = false;
  let ssoConfigured = false;
  let sessionAuthenticated = false;
  let currentIdentityName = "";
  let currentIdentityUserId = "";
  let authRedirecting = false;

  tokenInput.value = localStorage.getItem("weeklyReportAdminToken") || "";

  const resolveUrl = (path) => new URL(String(path || "").replace(/^\//, ""), appBaseUrl).toString();
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
  const escapeMultiline = (value) => escapeHtml(value).replace(/\r?\n/g, "<br>");
  const setValue = (selector, value) => { const element = $(selector); if (element) element.value = value ?? ""; };
  const setChecked = (selector, value) => { const element = $(selector); if (element) element.checked = Boolean(value); };
  const toInt = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const formatTime = (hour, minute = 0) => `${String(toInt(hour)).padStart(2, "0")}:${String(toInt(minute)).padStart(2, "0")}`;
  const parseTime = (value, fallbackHour = 0, fallbackMinute = 0) => {
    const match = String(value || "").match(/^(\d{1,2}):(\d{2})$/);
    return match ? {hour: toInt(match[1], fallbackHour), minute: toInt(match[2], fallbackMinute)} : {hour: fallbackHour, minute: fallbackMinute};
  };

  const showToast = (message, type = "success") => {
    toast.textContent = message;
    toast.className = `toast show${type === "error" ? " error" : ""}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.className = "toast"; }, 3200);
  };

  const beginSsoLogin = () => {
    if (!ssoConfigured || authRedirecting) return;
    authRedirecting = true;
    sessionStorage.removeItem("weeklyReportManualLogout");
    const loginUrl = new URL("api/auth/dingtalk/login", appBaseUrl);
    loginUrl.searchParams.set("next", window.location.hash || "#/overview");
    window.location.assign(loginUrl.toString());
  };

  const setAccessDisconnected = (message = "请先连接管理端。", {clearToken = false, open = true} = {}) => {
    accessConnected = false;
    if (clearToken) {
      tokenInput.value = "";
      localStorage.removeItem("weeklyReportAdminToken");
    }
    $(".auth-menu").classList.add("invalid");
    $("#authSummary").textContent = "需要登录";
    $("#authIdentity").textContent = message;
    $("#loginWithDingTalk").hidden = !ssoConfigured;
    $("#logoutSession").hidden = true;
    if (open) $(".auth-menu").open = true;
    $(".sidebar-footer").classList.remove("ready");
    $("#sidebarReadyText").textContent = "管理端未连接";
    $("#sidebarReadyDetail").textContent = message;
    $("#reportFilterSummary").textContent = "连接管理端后读取周报";
    $("#reports").innerHTML = `<div class="empty-state"><strong>需要登录管理端</strong><p>${escapeHtml(message)}</p><div class="actions"><button data-start-login type="button">${ssoConfigured ? "使用钉钉登录" : "连接管理端"}</button></div></div>`;
    $("#personalMembers").innerHTML = '<p class="muted">个人周报需要使用钉钉账号登录。</p>';
    $("#openLatestReport").disabled = true;
  };

  const setAccessConnected = (identityName = currentIdentityName) => {
    accessConnected = true;
    $(".auth-menu").classList.remove("invalid");
    $("#authSummary").textContent = identityName ? `${identityName} · 已登录` : "运维连接";
    $("#authIdentity").textContent = identityName ? `${identityName}，钉钉身份已验证` : "已使用运维令牌连接";
    $("#loginWithDingTalk").hidden = true;
    $("#logoutSession").hidden = !sessionAuthenticated;
  };

  const log = (label, value = "") => {
    const stamp = new Date().toLocaleString();
    const content = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    logBox.textContent = `[${stamp}] ${label}${content ? `\n${content}` : ""}\n\n${logBox.textContent}`;
  };

  const compactResult = (data) => {
    if (!data || typeof data !== "object") return "操作已完成。";
    if (data.report?.id) return `周报 #${data.report.id}，成功 ${data.sent ?? data.recalled ?? 0}，失败 ${data.failed ?? 0}`;
    if (data.id && data.title) return `周报 #${data.id} ${data.title}`;
    if (data.tasks != null && data.members != null) return `匹配 ${data.keyProjects || 0} 个重点项目、读取 ${data.projectStatuses || 0} 条项目状态`;
    if (data.runId) return `同步 ${data.tableCount || 0} 张表、${data.recordCount || 0} 条记录`;
    if (data.count != null) return `处理 ${data.count} 条记录`;
    if (data.message) return String(data.message);
    return "操作已完成。";
  };

  const api = async (path, options = {}) => {
    const headers = {"Content-Type": "application/json", ...(options.headers || {})};
    if (tokenInput.value.trim()) headers.Authorization = `Bearer ${tokenInput.value.trim()}`;
    const response = await fetch(resolveUrl(path), {...options, headers, credentials: "same-origin"});
    let body;
    try { body = await response.json(); } catch (_) { body = {detail: await response.text()}; }
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
      if (response.status === 401) {
        const usingToken = Boolean(tokenInput.value.trim());
        setAccessDisconnected(usingToken ? "运维令牌与服务器不匹配。" : "钉钉登录已过期，请重新登录。", {clearToken: usingToken, open: false});
        if (ssoConfigured) beginSsoLogin();
      }
      const error = new Error(response.status === 401 ? "管理登录已失效，请重新连接" : (detail || `HTTP ${response.status}`));
      error.status = response.status;
      throw error;
    }
    return body;
  };

  const setRoute = (route) => {
    const next = ROUTES[route] ? route : "overview";
    const meta = ROUTES[next];
    $$("[data-page]").forEach((page) => { page.hidden = page.dataset.page !== next; });
    $$("[data-route]").forEach((item) => item.classList.toggle("active", item.dataset.route === next));
    $("#pageEyebrow").textContent = meta.eyebrow;
    $("#pageTitle").textContent = meta.title;
    $("#pageSubtitle").textContent = meta.subtitle;
    document.title = `${meta.title} · 产品与项目经理周报助手`;
    closeSidebar();
    if (next === "teambition" && (accessConnected || tokenInput.value.trim()) && !teambitionData) {
      loadTeambitionDashboard().catch((error) => showToast(`TB 看板读取失败：${error.message}`, "error"));
    }
    if (next === "key-project-status" && (accessConnected || tokenInput.value.trim()) && !keyProjectStatusData) {
      loadKeyProjectStatuses().catch((error) => showToast(`重点项目状态读取失败：${error.message}`, "error"));
    }
    if (next === "personal-reports" && sessionAuthenticated) {
      loadPersonalContext(personalReportIdFromHash()).catch((error) => showToast(`个人周报读取失败：${error.message}`, "error"));
    }
  };

  const routeFromHash = () => String(window.location.hash || "").replace(/^#\/?/, "").split("?")[0] || "overview";
  const routeQuery = () => new URLSearchParams(String(window.location.hash || "").split("?", 2)[1] || "");
  const personalReportIdFromHash = () => toInt(routeQuery().get("reportId"), 0);
  const personalUserIdFromHash = () => String(routeQuery().get("userId") || "").trim();
  const editReportIdFromHash = () => toInt(routeQuery().get("editReportId"), 0);
  const personalReportHref = (reportId, userId = "") => {
    const url = new URL(window.location.href);
    const params = new URLSearchParams({reportId: String(reportId || "")});
    if (userId) params.set("userId", String(userId));
    url.search = "";
    url.hash = `#/personal-reports?${params.toString()}`;
    return url.toString();
  };
  const openSidebar = () => {
    $("#appSidebar").classList.add("show");
    $("#sidebarBackdrop").classList.add("show");
    $("#menuToggle").setAttribute("aria-expanded", "true");
  };
  const closeSidebar = () => {
    $("#appSidebar").classList.remove("show");
    $("#sidebarBackdrop").classList.remove("show");
    $("#menuToggle").setAttribute("aria-expanded", "false");
  };

  const checkCard = (name, state, detail = "") => `<div class="check ${state}"><strong>${escapeHtml(name)}</strong><span>${state === "ok" ? "已就绪" : state === "neutral" ? "需关注" : "待处理"}${detail ? ` · ${escapeHtml(detail)}` : ""}</span></div>`;

  const loadReadiness = async () => {
    const data = await api("/api/readiness");
    latestReadiness = data;
    const c = data.checks || {};
    const aiDetail = c.aiSummaryDetail || {};
    const directoryDetail = c.biCenterDetail || {};
    const directoryCache = c.directoryCache || {};
    $("#readiness").innerHTML = [
      checkCard("钉钉应用", c.dingtalkApp ? "ok" : "bad"),
      checkCard("管理端登录", c.adminSso ? "ok" : "neutral", c.adminSso ? "钉钉 OAuth" : "运维令牌兜底"),
      checkCard("AI 多维表", c.sourceData?.ready ? "ok" : "bad", c.sourceData?.reason || "最近同步成功"),
      checkCard("人员目录", c.biCenter ? "ok" : "bad", `${c.directoryCache?.count || 0} 人`),
      checkCard("TB 重点项目", c.teambition?.ready ? "ok" : (c.teambition?.required ? "bad" : "neutral"), c.teambition?.configured ? `匹配 ${c.teambition?.keyProjectCount || 0} · 状态 ${c.teambition?.projectStatusCount || 0}` : "未配置密钥"),
      checkCard("AI 摘要", c.aiSummary ? "ok" : (aiDetail.configured ? "neutral" : "bad"), c.aiSummary ? "连接通过" : (aiDetail.error || "未配置")),
      checkCard("推送目标", c.deliveryTargets?.ready ? "ok" : "bad", `测试 ${c.deliveryTargets?.preview || 0} / 正式 ${c.deliveryTargets?.formal || 0} / 周六最终 ${c.deliveryTargets?.saturdayFinal || 0}`),
      checkCard("回调鉴权", c.callbackAuth ? "ok" : "bad"),
      checkCard("公开链接", c.publicLinks ? "ok" : "bad"),
      checkCard("周报存档", c.archive?.ready ? "ok" : "bad", c.archive?.enabled ? `已启用 · ${c.archive?.mappedFields || 0} 个字段` : "未启用（可选）"),
      checkCard("总体链路", data.ready ? "ok" : "bad"),
    ].join("");
    const footer = $(".sidebar-footer");
    footer.classList.toggle("ready", Boolean(data.ready));
    $("#sidebarReadyText").textContent = data.ready ? "业务链路已就绪" : "仍有待处理项";
    $("#sidebarReadyDetail").textContent = `人员 ${c.directoryCache?.count || 0} · 测试目标 ${c.deliveryTargets?.preview || 0}`;
    $("#directoryConnectionState").textContent = directoryDetail.configured ? "已配置" : "未配置";
    $("#directoryTokenState").textContent = directoryDetail.tokenConfigured ? "只读 Token 已配置" : "只读 Token 未配置";
    $("#directoryCacheCount").textContent = `${directoryCache.count || 0} 人`;
    $("#directoryOrganizationCount").textContent = `${directoryCache.organizationCount || 0} 个组织 · ${directoryCache.relationCount || 0} 条关系`;
    $("#directoryVersion").textContent = directoryCache.directoryVersion || "尚未同步";
    $("#directoryRefreshedAt").textContent = directoryCache.refreshedAt ? `刷新于 ${directoryCache.refreshedAt}` : "暂无刷新记录";
    $("#directoryEndpoint").textContent = directoryDetail.baseUrl || "未配置";
    const scheduler = c.scheduler || {};
    $("#schedulerBanner").innerHTML = scheduler.processEnabled
      ? `<span>调度已启用</span><strong>周六 09:00 测试群 · 周六 17:00 最终版单聊 · 周日 20:00 正式检查</strong><p>周日仅发送已人工审核、内容哈希未变化的当前综合版。</p>`
      : `<span>安全策略</span><strong>服务器定时任务当前关闭</strong><p>配置可保存，但不会自动生成或推送；正式发送始终需要人工审核。</p>`;
    renderOverview();
    return data;
  };

  const loadCoverage = async () => {
    const kind = $("#reportKind")?.value || "combined";
    const data = await api(`/api/coverage?report_kind=${encodeURIComponent(kind)}`);
    latestCoverage = data;
    const missing = data.missing || [];
    $("#coverage").innerHTML = `<div class="coverage-summary"><strong>应覆盖 ${data.expectedCount || 0} 人</strong><span>已覆盖 ${data.coveredCount || 0}</span><span class="${missing.length ? "warn" : "ok-text"}">缺少 ${data.missingCount || 0}</span></div>` +
      (missing.length ? `<div class="coverage-people">${missing.map((item) => `<span>${escapeHtml(item.name || item.userId)} · ${item.role === "project" ? "项目经理" : "产品经理"}${item.department ? ` · ${escapeHtml(item.department)}` : ""}</span>`).join("")}</div>` : '<p class="safe">当前名单已全部覆盖。</p>');
    renderOverview();
    return data;
  };

  const statusLabel = (value) => STATUS_LABELS[value] || value || "未知状态";
  const statusBadgeClass = (value) => value === "formal_sent" ? "formal" : ["need_changes", "retryable_error"].includes(value) ? "warning" : "";

  const renderOverview = () => {
    const latest = reportItems[0];
    if (latest) {
      const metrics = latest.metrics || {};
      $("#overviewLatest").innerHTML = `<span>LATEST REPORT</span><h3>#${latest.id} ${escapeHtml(latest.title)}</h3><p>${escapeHtml(latest.window?.label || latest.periodKey)} · ${escapeHtml(KIND_LABELS[latest.reportKind] || latest.reportKind)} · v${latest.version}</p><p>事项 ${metrics.itemCount || 0} · 风险 ${metrics.riskCount || 0} · 逾期 ${metrics.overdueCount || 0}</p><span class="badge ${statusBadgeClass(latest.workflowState)}">${escapeHtml(statusLabel(latest.workflowState))}</span>`;
    } else {
      $("#overviewLatest").innerHTML = '<p class="muted">尚未生成周报。</p>';
    }
    const metrics = latest?.metrics || {};
    const coverage = latestCoverage || metrics.coverage || {};
    const cards = [
      ["有效事实", latest ? (metrics.itemCount || 0) : "—", latest ? `风险 ${metrics.riskCount || 0} · 逾期 ${metrics.overdueCount || 0}` : "尚未生成"],
      ["最新版本", latest ? `v${latest.version}` : "—", latest ? `${KIND_LABELS[latest.reportKind] || latest.reportKind} · #${latest.id}` : "尚未生成"],
      ["人员覆盖", coverage.expectedCount != null ? `${coverage.coveredCount || 0}/${coverage.expectedCount || 0}` : "—", coverage.missingCount ? `缺少 ${coverage.missingCount} 人` : "当前覆盖完整"],
      ["推送状态", latest ? statusLabel(latest.workflowState) : "—", latest?.sentAt ? `发送于 ${latest.sentAt}` : "尚未正式发送"],
    ];
    $("#overviewStats").innerHTML = cards.map(([label, value, detail]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`).join("");
  };

  const reportActionButton = (id, action, label, css = "") => `<button class="${css}" data-report-id="${id}" data-report-action="${action}" type="button">${label}</button>`;
  const reportMoreMenu = (id, actions) => actions.length ? `<details class="report-more"><summary>更多</summary><div class="report-more-menu">${actions.map(([action, label, css = "secondary"]) => reportActionButton(id, action, label, css)).join("")}</div></details>` : "";
  const visibleReports = () => {
    const kind = $("#reportKindFilter").value;
    const query = $("#reportSearch").value.trim().toLowerCase();
    let items = [...reportItems];
    if (reportFilter === "current") {
      const latestPeriod = items[0]?.periodKey || "";
      const seenKinds = new Set();
      items = items.filter((item) => {
        if (item.periodKey !== latestPeriod || seenKinds.has(item.reportKind)) return false;
        seenKinds.add(item.reportKind);
        return true;
      });
    } else if (reportFilter === "formal") {
      items = items.filter((item) => item.workflowState === "formal_sent");
    }
    if (kind !== "all") items = items.filter((item) => item.reportKind === kind);
    if (query) items = items.filter((item) => `${item.id} ${item.title} ${item.periodKey} ${item.window?.label || ""}`.toLowerCase().includes(query));
    return items;
  };

  const renderReports = () => {
    const items = visibleReports();
    const latest = reportItems[0];
    $("#openLatestReport").disabled = !latest;
    $("#openLatestReport").textContent = latest ? `外部打开 #${latest.id} ↗` : "外部打开最新周报 ↗";
    const modeLabel = {current: "当前周期最新版本", formal: "已正式发送", history: "全部历史记录"}[reportFilter];
    $("#reportFilterSummary").textContent = `${modeLabel} · 共 ${items.length} 条`;
    $("#reports").innerHTML = items.length ? items.map((item) => {
      const metrics = item.metrics || {};
      const editable = !["formal_sent", "recalled", "cancelled"].includes(item.workflowState);
      const actions = [
        editable ? reportActionButton(item.id, "edit", "编辑正文") : "",
        reportActionButton(item.id, "browse", "外部打开 ↗", "secondary"),
        !item.previewedAt && ["draft_generated", "rendered", "retryable_error"].includes(item.workflowState) ? reportActionButton(item.id, "preview", "发送预览") : "",
        item.workflowState === "awaiting_approval" ? reportActionButton(item.id, "approve", "审核通过") : "",
        item.workflowState === "approved" ? reportActionButton(item.id, "formal", "正式发送") : "",
        item.workflowState === "formal_sent" ? reportActionButton(item.id, "recall", "撤回", "danger") : "",
        reportMoreMenu(item.id, [
          ...(editable ? [["render", "重新生成图片", "secondary"]] : []),
          ...(item.workflowState === "formal_sent" ? [["archive", "归档", "secondary"]] : []),
        ]),
      ].join("");
      return `<article class="report"><div><div class="report-title-row"><h3>#${item.id} ${escapeHtml(item.title)}</h3><span class="badge ${statusBadgeClass(item.workflowState)}">${escapeHtml(statusLabel(item.workflowState))}</span></div><p class="report-meta"><span>${escapeHtml(item.window?.label || item.periodKey)}</span><span>${escapeHtml(KIND_LABELS[item.reportKind] || item.reportKind)}</span><span>版本 v${item.version}</span><span>AI ${escapeHtml(item.aiStatus || "-")}</span><span>归档 ${escapeHtml(item.archive?.status || "未执行")}</span></p><p>事项 ${metrics.itemCount || 0} · 风险 ${metrics.riskCount || 0} · 逾期 ${metrics.overdueCount || 0} · 缺覆盖 ${metrics.coverage?.missingCount || 0}</p></div><div class="actions">${actions}</div></article>`;
    }).join("") : '<div class="empty-state"><strong>当前筛选下没有周报</strong><p>可以切换范围，或同步事实后生成一个新版本。</p></div>';
    renderOverview();
  };

  const loadReports = async () => {
    const data = await api("/api/reports?limit=100");
    reportItems = data.items || [];
    renderReports();
    return data;
  };

  const sectionLines = (value) => String(value || "").split(/\r?\n/).map((line) => line.replace(/^[-•*]\s*/, "").trim()).filter(Boolean);

  const personalMemberInitial = (item) => Array.from(String(item.name || item.userId || "员").trim())[0] || "员";

  const updatePersonalReportRoute = (reportId, userId) => {
    if (!reportId || routeFromHash() !== "personal-reports") return;
    window.history.replaceState(null, "", personalReportHref(reportId, userId));
  };

  const renderPersonalMembers = () => {
    const members = personalContext?.members || [];
    const query = personalMemberSearch.trim().toLocaleLowerCase("zh-CN");
    const filteredMembers = query ? members.filter((item) => [
      item.name, item.userId, item.department, item.title, ...(item.roles || []),
    ].some((value) => String(value || "").toLocaleLowerCase("zh-CN").includes(query))) : members;
    $("#personalMemberHint").textContent = personalContext?.canViewMembers
      ? (personalContext.viewerHasReport ? "已优先定位到本人，可搜索并切换授权成员。" : "本人本周无事项，已自动展示首位有内容的成员。")
      : "当前仅有本人周报可查看。";
    $("#personalMemberCount").textContent = query ? `${filteredMembers.length} / ${members.length} 人` : `${members.length} 人`;
    $("#personalMembers").innerHTML = filteredMembers.length ? filteredMembers.map((item, index) => {
      const details = [item.department, item.title].filter(Boolean).join(" · ") || (item.isSelf ? "当前登录成员" : "未标注部门");
      const roles = (item.roles || []).join(" · ") || "周报责任人";
      const active = item.userId === activePersonalUserId;
      return `
        <button class="personal-member ${active ? "active" : ""}" data-personal-user-id="${escapeHtml(item.userId)}" data-tone="${index % 4}" type="button" aria-pressed="${active}">
          <span class="personal-member-avatar" aria-hidden="true">${escapeHtml(personalMemberInitial(item))}</span>
          <span class="personal-member-main">
            <span class="personal-member-name"><strong>${escapeHtml(item.name || item.userId)}</strong>${item.isSelf ? "<i>本人</i>" : ""}</span>
            <small>${escapeHtml(details)}</small>
            <span class="personal-member-role">${escapeHtml(roles)}</span>
          </span>
          <span class="personal-member-total"><strong>${item.itemCount || 0}</strong><small>事项</small></span>
        </button>`;
    }).join("") : `<div class="personal-member-empty"><strong>${members.length ? "没有匹配成员" : "暂无可查看成员"}</strong><span>${members.length ? "换个姓名、部门或角色试试。" : "当前周报没有可查看的个人事项。"}</span></div>`;
  };

  const renderPersonalReport = (data) => {
    const person = data.person || {};
    const metrics = data.metrics || {};
    const externalHref = personalReportHref(data.reportId, person.userId);
    $("#personalHero").innerHTML = `<div><span>PERSONAL WEEKLY REPORT</span><h2>${escapeHtml(person.name || "个人周报")}</h2><p>${escapeHtml(data.window?.label || data.periodKey || "")} · 团队周报 v${data.version || 0}${data.edit?.edited ? ` · 已编辑 ${escapeHtml(String(data.edit.updatedAt || "").replace("T", " ").slice(0, 16))}` : ""}</p></div><div class="personal-hero-actions"><div class="personal-hero-tag">${escapeHtml(statusLabel(data.workflowState))}</div>${data.canEdit ? '<button class="personal-edit-button" data-personal-edit type="button">✎ 编辑个人周报</button>' : ""}<a class="personal-external-link" href="${escapeHtml(externalHref)}" target="_blank" rel="noopener noreferrer">外部打开 ↗</a></div>`;
    const cards = [
      ["关联事项", metrics.itemCount || 0, `涉及 ${Object.keys(metrics.byCategory || {}).length} 个分类`],
      ["已完成", metrics.completedCount || 0, `进行中/待处理 ${metrics.inProgressCount || 0}`],
      ["风险事项", metrics.riskCount || 0, "按事实状态识别"],
      ["逾期事项", metrics.overdueCount || 0, "未关闭且已过截止"],
      ["高优先级", metrics.highPriorityCount || 0, "高或紧急"],
      ["承担角色", Object.keys(metrics.byRole || {}).length, Object.entries(metrics.byRole || {}).map(([role, count]) => `${role} ${count}`).join(" · ") || "暂无归属"],
    ];
    $("#personalStats").innerHTML = cards.map(([label, value, detail]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`).join("");
    const total = Number(metrics.itemCount || 0);
    const completed = Number(metrics.completedCount || 0);
    const inProgress = Number(metrics.inProgressCount || 0);
    const risks = Number(metrics.riskCount || 0);
    const completionRate = total ? Math.round((completed / total) * 100) : 0;
    const riskRate = total ? Math.round((risks / total) * 100) : 0;
    const categories = Object.entries(metrics.byCategory || {}).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 6);
    const categoryMax = Math.max(1, ...categories.map(([, count]) => Number(count || 0)));
    $("#personalCharts").innerHTML = `
      <article class="panel personal-chart-card">
        <div class="personal-chart-head"><div><p class="eyebrow">PROGRESS</p><h3>工作进展</h3></div><span>${total} 项</span></div>
        <div class="personal-progress-chart">
          <div class="personal-donut" style="--complete-angle:${completionRate * 3.6}deg"><div><strong>${completionRate}%</strong><small>完成率</small></div></div>
          <div class="personal-chart-legend">
            <div><i class="done"></i><span>已完成</span><strong>${completed}</strong></div>
            <div><i class="active"></i><span>进行中/待处理</span><strong>${inProgress}</strong></div>
            <div><i class="risk"></i><span>风险占比</span><strong>${riskRate}%</strong></div>
          </div>
        </div>
      </article>
      <article class="panel personal-chart-card">
        <div class="personal-chart-head"><div><p class="eyebrow">DISTRIBUTION</p><h3>工作分类分布</h3></div><span>${categories.length} 类</span></div>
        <div class="personal-bar-list">${categories.length ? categories.map(([label, count]) => `<div class="personal-bar-row"><span>${escapeHtml(label)}</span><div><i style="width:${Math.max(6, Math.round((Number(count || 0) / categoryMax) * 100))}%"></i></div><strong>${Number(count || 0)}</strong></div>`).join("") : '<p class="muted">本周期暂无分类数据。</p>'}</div>
      </article>`;
    $("#personalSummary").textContent = data.summary || "本周期暂无归属事实。";
    const items = data.items || [];
    $("#personalCategories").innerHTML = (data.categorySections || []).length ? data.categorySections.map((section) => {
      const categoryItems = items.filter((item) => String(item.categoryKey || item.tableId) === String(section.key));
      const subtypeChips = Object.entries(section.bySubcategory || {}).map(([name, count]) => `<span>${escapeHtml(name)} ${count}</span>`).join("");
      const categorySummary = section.digest ? `<div class="personal-category-summary">${sectionLines(section.digest).map((line) => `<p>${escapeHtml(line)}</p>`).join("")}</div>` : "";
      return `<section class="panel personal-category"><div class="personal-category-head"><div><p class="eyebrow">${String(section.order || "").padStart(2, "0")}</p><h2>${escapeHtml(section.label || "未分类")}</h2></div><div class="personal-category-count"><strong>${section.itemCount || 0}</strong><span>项工作</span></div></div>${categorySummary}${subtypeChips ? `<div class="personal-subtypes">${subtypeChips}</div>` : ""}<div class="personal-item-list">${categoryItems.map((item) => {
        const tbProject = item.teambitionProject || null;
        const tags = [...(item.roles || []), item.subcategory, item.status || "未标记", item.priority ? `${item.priority}优先级` : "", tbProject?.statusName ? `TB · ${tbProject.statusName}` : ""].filter(Boolean);
        const dates = [item.eventAt ? `业务日期 ${String(item.eventAt).split("T")[0]}` : "", item.dueAt ? `截止 ${String(item.dueAt).split("T")[0]}` : ""].filter(Boolean).join(" · ");
        const tbMeta = tbProject ? [tbProject.statusName, tbProject.statusDegreeLabel, tbProject.progressPercent != null ? `进度 ${tbProject.progressPercent}%` : "", tbProject.statusCreatedAt ? `更新 ${String(tbProject.statusCreatedAt).split("T")[0]}` : ""].filter(Boolean).join(" · ") : "";
        const tbDetails = tbProject ? `<details class="personal-tb-status"><summary><span><strong>TB 项目状态</strong><small>${escapeHtml(tbMeta || "已关联重点项目")}</small></span><em>${tbProject.statusContent ? "查看详情" : "暂无状态正文"}</em></summary>${tbProject.statusContent ? `<div>${escapeMultiline(tbProject.statusContent)}</div>` : ""}</details>` : "";
        return `<article class="personal-item"><div class="personal-item-title"><h3>${escapeHtml(item.title || "未命名事项")}</h3><div>${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div></div>${item.progressText ? `<p><strong>本周进展</strong>${escapeHtml(item.progressText)}</p>` : ""}${item.planText ? `<p><strong>下周计划</strong>${escapeHtml(item.planText)}</p>` : ""}${item.riskText ? `<p class="personal-risk"><strong>风险提示</strong>${escapeHtml(item.riskText)}</p>` : ""}${tbDetails}${dates ? `<small>${escapeHtml(dates)}</small>` : ""}</article>`;
      }).join("")}</div></section>`;
    }).join("") : '<div class="empty-state"><strong>本周期暂无个人事项</strong><p>团队周报已生成，但没有匹配到该成员的责任人字段。</p></div>';
  };

  const loadPersonalReport = async (reportId = Number($("#personalReportPeriod").value || personalContext?.selectedReportId || 0), userId = activePersonalUserId) => {
    if (!reportId) {
      renderPersonalMembers();
      $("#personalCharts").innerHTML = '<div class="panel personal-chart-card"><div class="empty-state"><strong>暂无图表</strong><p>请先生成综合版周报。</p></div></div>';
      $("#personalCategories").innerHTML = '<div class="empty-state"><strong>暂无综合周报</strong><p>请先在周报展示中生成综合版周报。</p></div>';
      return null;
    }
    const params = new URLSearchParams();
    if (userId) params.set("user_id", userId);
    const data = await api(`/api/personal-reports/${reportId}?${params.toString()}`);
    activePersonalReport = data;
    activePersonalUserId = data.person?.userId || userId || currentIdentityUserId;
    updatePersonalReportRoute(data.reportId || reportId, activePersonalUserId);
    renderPersonalMembers();
    renderPersonalReport(data);
    return data;
  };

  const personalItemKey = (item) => String(item.recordId || item.id || "").trim();
  const currentPersonalEditPayload = () => {
    const categoryDigests = Object.fromEntries($$("[data-personal-category-key]", $("#personalEditCategories")).map((row) => [row.dataset.personalCategoryKey, $("textarea", row).value.trim()]));
    const itemOverrides = Object.fromEntries($$("[data-personal-item-key]", $("#personalEditItems")).map((row) => {
      const values = {};
      $$('[data-personal-item-field]', row).forEach((input) => { values[input.dataset.personalItemField] = input.value.trim(); });
      return [row.dataset.personalItemKey, values];
    }));
    return {
      userId: activePersonalReport?.person?.userId || activePersonalUserId,
      summary: $("#personalEditSummary").value.trim(),
      categoryDigests,
      itemOverrides,
    };
  };

  const updatePersonalDirtyState = () => {
    personalIsDirty = JSON.stringify(currentPersonalEditPayload()) !== personalOriginalEdit;
    $("#savePersonalEdit").disabled = !personalIsDirty;
    $("#personalDirtyHint").textContent = personalIsDirty ? "有未保存修改；保存后需要重新预览和审核团队周报。" : "保存后需要重新预览和审核团队周报。";
    $("#personalDirtyHint").className = personalIsDirty ? "dirty" : "";
  };

  const openPersonalEditor = () => {
    const data = activePersonalReport;
    if (!data?.canEdit) throw new Error("当前个人周报不可编辑");
    const person = data.person || {};
    $("#personalEditTitle").textContent = `编辑${person.name || "成员"}的个人周报`;
    $("#personalEditMeta").textContent = `${data.window?.label || data.periodKey || ""} · 团队周报 v${data.version || 0}`;
    $("#personalEditSummary").value = data.summary || "";
    $("#personalEditCategories").innerHTML = (data.categorySections || []).map((section) => `<label data-personal-category-key="${escapeHtml(section.key || "")}"><span><strong>${escapeHtml(section.label || "未分类")}</strong><small>${section.itemCount || 0} 项</small></span><textarea maxlength="12000">${escapeHtml(section.digest || section.content || "")}</textarea></label>`).join("") || '<p class="muted">当前没有可编辑分类。</p>';
    $("#personalEditItems").innerHTML = (data.items || []).map((item, index) => {
      const itemKey = personalItemKey(item);
      const tb = item.teambitionProject || null;
      const tbMeta = tb ? [tb.statusName, tb.statusDegreeLabel, tb.progressPercent != null ? `进度 ${tb.progressPercent}%` : ""].filter(Boolean).join(" · ") : "";
      return `<details class="personal-item-editor" data-personal-item-key="${escapeHtml(itemKey)}" ${index < 2 ? "open" : ""}><summary><span><strong>${escapeHtml(item.title || "未命名事项")}</strong><small>${escapeHtml(item.category || "未分类")} · ${escapeHtml(item.status || "未标记")}</small></span><em>展开编辑</em></summary><div class="personal-item-editor-grid"><label class="full-span">事项标题<input data-personal-item-field="title" maxlength="12000" value="${escapeHtml(item.title || "")}"></label><label>状态<input data-personal-item-field="status" maxlength="12000" value="${escapeHtml(item.status || "")}"></label><label>优先级<input data-personal-item-field="priority" maxlength="12000" value="${escapeHtml(item.priority || "")}"></label><label class="full-span">本周进展<textarea data-personal-item-field="progressText" maxlength="12000">${escapeHtml(item.progressText || "")}</textarea></label><label class="full-span">下周计划<textarea data-personal-item-field="planText" maxlength="12000">${escapeHtml(item.planText || "")}</textarea></label><label class="full-span">风险与问题<textarea data-personal-item-field="riskText" maxlength="12000">${escapeHtml(item.riskText || "")}</textarea></label>${tb ? `<div class="personal-tb-readonly full-span"><strong>TB项目状态（只读）</strong><span>${escapeHtml(tbMeta || "已关联TB重点项目")}</span><p>${escapeHtml(tb.statusSummary || tb.statusContent || "暂无状态正文")}</p></div>` : ""}</div></details>`;
    }).join("") || '<p class="muted">当前没有可编辑事项。</p>';
    personalIsDirty = false;
    personalOriginalEdit = JSON.stringify(currentPersonalEditPayload());
    $("#savePersonalEdit").disabled = true;
    $("#personalDirtyHint").textContent = "保存后需要重新预览和审核团队周报。";
    $("#personalDirtyHint").className = "";
    $("#personalEditDialog").showModal();
    return data;
  };

  const closePersonalEditor = () => {
    if (personalIsDirty && !window.confirm("当前个人周报修改尚未保存，确定放弃吗？")) return false;
    personalIsDirty = false;
    $("#personalEditDialog").close();
    return true;
  };

  const loadPersonalContext = async (reportId = Number($("#personalReportPeriod")?.value || 0)) => {
    if (!sessionAuthenticated) throw new Error("个人周报需要使用钉钉账号登录");
    const suffix = reportId ? `?report_id=${encodeURIComponent(reportId)}` : "";
    personalContext = await api(`/api/personal-reports/context${suffix}`);
    const reports = personalContext.reports || [];
    const selected = Number(personalContext.selectedReportId || reports[0]?.id || 0);
    $("#personalReportPeriod").innerHTML = reports.length ? reports.map((item) => `<option value="${item.id}" ${Number(item.id) === selected ? "selected" : ""}>${escapeHtml(item.window?.label || item.periodKey)} · v${item.version}</option>`).join("") : '<option value="">暂无综合周报</option>';
    const members = personalContext.members || [];
    const requestedUserId = personalUserIdFromHash();
    if (requestedUserId && members.some((item) => item.userId === requestedUserId)) {
      activePersonalUserId = requestedUserId;
    } else {
      activePersonalUserId = personalContext.defaultUserId || members[0]?.userId || personalContext.viewer?.userId || currentIdentityUserId;
    }
    renderPersonalMembers();
    return loadPersonalReport(selected, activePersonalUserId);
  };

  const tbStatusLabel = (value) => ({
    overdue: "已逾期", due_soon: "7 天内到期", in_progress: "进行中", completed: "已完成",
  }[value] || value || "未知");

  const renderTeambition = (data) => {
    teambitionData = data || {};
    const summary = teambitionData.summary || {};
    const sync = teambitionData.sync || {};
    const latestRun = sync.latestRun || {};
    const stats = [
      ["进行中", summary.inProgressCount || 0, `未完成总计 ${summary.openCount || 0}`],
      ["已逾期", summary.overdueCount || 0, "未完成且已超过截止时间"],
      ["7 天内到期", summary.dueSoonCount || 0, `本月到期 ${summary.dueInMonthCount || 0}`],
      ["本月完成", summary.completedInMonthCount || 0, `按时完成 ${summary.onTimeInMonthCount || 0}`],
    ];
    $("#teambitionStats").innerHTML = stats.map(([label, value, detail]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`).join("");
    $("#teambitionConnection").innerHTML = [
      ["接口来源", sync.source || teambitionData.source || "native", sync.configured ? "只读密钥已配置" : "只读密钥未配置"],
      ["任务缓存", `${sync.taskCount || 0} 条`, `${sync.projectCount || 0} 个项目`],
      ["人员映射", `${sync.memberCount || 0} 人`, latestRun.fail_count ? `最近失败 ${latestRun.fail_count} 人` : "最近无失败"],
      ["最近同步", latestRun.status || "尚未同步", latestRun.finished_at || sync.syncedAt || "暂无时间"],
    ].map(([label, value, detail]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`).join("");
    const departmentSelect = $("#teambitionDepartment");
    const selectedDepartment = departmentSelect.value;
    departmentSelect.innerHTML = '<option value="">全部部门 / 业务组</option>' + (teambitionData.filters?.departments || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
    if ([...departmentSelect.options].some((option) => option.value === selectedDepartment)) departmentSelect.value = selectedDepartment;
    const columns = [
      ["overdue", "已逾期"], ["due_soon", "7 天内到期"], ["in_progress", "进行中"], ["completed", "已完成"],
    ];
    const items = teambitionData.items || [];
    $("#teambitionSummary").textContent = `${teambitionData.month || "当前月"} · 当前筛选 ${teambitionData.total || 0} 条 · 执行人 ${summary.executorCount || 0} · 项目 ${summary.projectCount || 0}`;
    $("#teambitionBoard").innerHTML = columns.map(([key, label]) => {
      const tasks = items.filter((item) => item.status === key);
      const body = tasks.length ? tasks.map((item) => {
        const due = item.dueAt ? item.dueAt.replace("T", " ").slice(0, 16) : "未设置截止时间";
        const owner = [item.executorName, item.bizGroup || item.department].filter(Boolean).join(" · ");
        return `<article class="tb-task"><div class="tb-task-meta"><span>${escapeHtml(item.projectName || "未归属项目")}</span>${item.priority >= 2 ? '<span>高优先级</span>' : ""}</div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(owner || "未识别执行人")}</p><div class="tb-task-footer"><span>${escapeHtml(tbStatusLabel(item.status))}</span><span>${escapeHtml(due)}</span></div></article>`;
      }).join("") : '<div class="tb-empty-column">当前没有任务</div>';
      return `<section class="tb-column" data-status="${key}"><div class="tb-column-head"><strong>${label}</strong><span>${tasks.length}</span></div><div class="tb-task-list">${body}</div></section>`;
    }).join("");
  };

  const loadTeambitionDashboard = async () => {
    const month = $("#teambitionMonth").value;
    const department = $("#teambitionDepartment").value;
    const status = $("#teambitionStatus").value;
    const query = $("#teambitionSearch").value.trim();
    const params = new URLSearchParams({month, department, status, query, limit: "1000"});
    const data = await api(`/api/teambition/dashboard?${params.toString()}`);
    renderTeambition(data);
    return data;
  };

  const keyProjectMatchLabel = (value) => ({
    matched: "已匹配", risky: "风险关注", status_missing: "暂无TB状态", unmatched: "待匹配TB",
  }[value] || "待确认");

  const compactDateTime = (value) => String(value || "").replace("T", " ").slice(0, 16) || "—";

  const renderKeyProjectStatuses = (data) => {
    keyProjectStatusData = data || {};
    const summary = keyProjectStatusData.summary || {};
    const sync = keyProjectStatusData.sync || {};
    const latestRun = sync.latestRun || {};
    const pendingCount = (summary.unmatchedCount || 0) + (summary.statusMissingCount || 0);
    const stats = [
      ["多维表重点项目", summary.sourceCount || 0, "随多维表当前记录动态变化"],
      ["TB 已匹配", summary.matchedCount || 0, `待匹配 ${summary.unmatchedCount || 0}`],
      ["已有最新状态", summary.withStatusCount || 0, `风险关注 ${summary.riskyCount || 0}`],
      ["待匹配 / 待补状态", pendingCount, `缺状态 ${summary.statusMissingCount || 0}`],
    ];
    $("#keyProjectStatusStats").innerHTML = stats.map(([label, value, detail]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`).join("");
    $("#keyProjectStatusConnection").innerHTML = [
      ["项目来源", keyProjectStatusData.source?.tableName || "重点项目跟踪", `当前 ${summary.sourceCount || 0} 条有效记录`],
      ["多维表同步", compactDateTime(keyProjectStatusData.source?.syncedAt), "删除记录自动退出页面范围"],
      ["TB 状态同步", compactDateTime(latestRun.finished_at || sync.syncedAt), latestRun.status || "尚未同步"],
      ["匹配规则", "唯一匹配", "项目编号优先，其次项目名称"],
    ].map(([label, value, detail]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`).join("");
    $("#keyProjectStatusSummary").textContent = `当前筛选 ${keyProjectStatusData.total || 0} 项 · 多维表 ${summary.sourceCount || 0} 项 · TB匹配 ${summary.matchedCount || 0} 项 · 有状态 ${summary.withStatusCount || 0} 项`;
    const items = keyProjectStatusData.items || [];
    $("#keyProjectStatusList").innerHTML = items.length ? items.map((item) => {
      const source = item.source || {};
      const tb = item.teambition || null;
      const status = tb?.status || {};
      const managers = [...new Set([...(source.productManagers || []), ...(source.projectManagers || []), ...(source.assignees || []).map((person) => person.name)].filter(Boolean))];
      const sourceFacts = [
        ["多维表状态", source.status || "未标记"],
        ["优先级", source.priority || "未标记"],
        ["负责人", managers.join("、") || "未填写"],
        ["本周进展", source.progressText || "未填写"],
        ["风险与问题", source.riskText || "未填写"],
        ["下一步计划", source.planText || "未填写"],
        ["多维表更新", compactDateTime(source.updatedAt)],
      ];
      const progress = tb?.progressPercent == null ? null : Math.max(0, Math.min(100, Number(tb.progressPercent)));
      const matchMeta = tb ? `${tb.matchType === "project_code" ? "项目编号" : "项目名称"}唯一匹配 · ${compactDateTime(tb.syncedAt)}同步` : "尚未找到唯一TB项目；下一次TB同步会继续按编号和名称匹配";
      const fields = (source.fields || []).filter((field) => !["项目名称", "项目编号"].includes(field.name));
      return `<article class="key-project-card state-${escapeHtml(item.matchState || "unmatched")}">
        <header class="key-project-card-head"><div><div class="key-project-code">${escapeHtml(source.projectCode || tb?.projectCode || "未填写项目编号")}</div><h3>${escapeHtml(source.name || "未命名重点项目")}</h3><p>${escapeHtml(matchMeta)}</p></div><span class="key-project-state">${escapeHtml(keyProjectMatchLabel(item.matchState))}</span></header>
        <div class="key-project-columns">
          <section class="key-project-source"><div class="key-project-section-title"><span>AI</span><div><strong>多维表项目信息</strong><small>当前有效记录</small></div></div><dl>${sourceFacts.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>${fields.length ? `<details class="key-project-fields"><summary>查看多维表更多字段（${fields.length}）</summary><div>${fields.map((field) => `<p><span>${escapeHtml(field.name)}</span><strong>${escapeHtml(field.value)}</strong></p>`).join("")}</div></details>` : ""}</section>
          <section class="key-project-tb"><div class="key-project-section-title"><span>TB</span><div><strong>Teambition 项目状态</strong><small>${tb ? escapeHtml(tb.name || tb.projectId) : "等待唯一匹配"}</small></div></div>${tb ? `<div class="tb-project-facts"><span>项目编号 <strong>${escapeHtml(tb.projectCode || "未填写")}</strong></span><span>TB项目ID <strong>${escapeHtml(tb.projectId || "未填写")}</strong></span><span>项目周期 <strong>${escapeHtml([compactDateTime(tb.startAt).slice(0, 10), compactDateTime(tb.endAt).slice(0, 10)].join(" → "))}</strong></span><span>TB更新 <strong>${escapeHtml(compactDateTime(tb.updatedAt))}</strong></span></div><div class="tb-project-progress"><div><span>项目进度</span><strong>${progress == null ? "未填写" : `${progress}%`}</strong></div><div class="tb-progress-track"><i style="width:${progress == null ? 0 : progress}%"></i></div></div><div class="tb-latest-status"><div><span>最新项目状态</span><div>${tb.isArchived ? '<em>已归档</em>' : ""}${tb.isSuspended ? '<em>已暂停</em>' : ""}${status.degreeLabel ? `<em>${escapeHtml(status.degreeLabel)}</em>` : ""}</div></div><h4>${escapeHtml(status.name || "暂无项目状态名称")}</h4><p>${status.content ? escapeMultiline(status.content) : "TB 暂无状态正文"}</p><small>状态时间 ${escapeHtml(compactDateTime(status.createdAt))}</small></div>` : '<div class="tb-unmatched"><strong>等待匹配 TB 项目</strong><p>该项目仍会保留在页面中；只有项目编号或规范化名称能够唯一对应时，才会读取 TB 项目信息和最新状态。</p></div>'}</section>
        </div></article>`;
    }).join("") : '<div class="empty-state"><strong>当前筛选没有项目</strong><p>可调整搜索词或匹配状态；如多维表刚有变更，请执行“同步多维表 + TB”。</p></div>';
  };

  const loadKeyProjectStatuses = async () => {
    const params = new URLSearchParams({
      query: $("#keyProjectStatusSearch").value.trim(),
      state: $("#keyProjectStatusFilter").value,
      limit: "1000",
    });
    const data = await api(`/api/teambition/key-project-statuses?${params.toString()}`);
    renderKeyProjectStatuses(data);
    return data;
  };

  const openReport = async (id) => {
    const report = await api(`/api/reports/${id}`);
    activeReportId = Number(id);
    reportIsDirty = false;
    $("#reportSourceDetails").open = false;
    $("#reportDialogTitle").textContent = `#${id} ${report.title}`;
    $("#reportDialogMeta").textContent = `${report.window?.label || report.periodKey} · ${KIND_LABELS[report.reportKind] || report.reportKind} · v${report.version} · ${statusLabel(report.workflowState)} · 归档 ${report.archive?.status || "未执行"}${report.archive?.error ? `（${report.archive.error}）` : ""}`;
    $("#reportEditTitle").value = report.title || "";
    SECTION_KEYS.forEach((key) => setValue(`#section-${key}`, report.sections?.[key] || ""));
    $("#saveSections").disabled = true;
    $("#reportDirtyHint").textContent = "修改后保存会清除旧图片、预览和审核状态。";
    $("#reportDirtyHint").className = "";
    const categorySections = report.sections?.categorySections || [];
    $("#reportCategorySections").innerHTML = categorySections.length ? categorySections.map((section) => `<label data-report-category-key="${escapeHtml(section.key || "")}"><span><strong>${escapeHtml(section.label || "未分类")}</strong><small>${section.itemCount || 0} 项 · 风险 ${section.riskCount || 0} · 逾期 ${section.overdueCount || 0}</small></span><textarea maxlength="12000">${escapeHtml(section.digest || section.content || "")}</textarea></label>`).join("") : '<p class="muted">本版没有结构化分类，事实清单仍可正常查看。</p>';
    $("#reportSources").innerHTML = (report.sources || []).length ? report.sources.map((item) => `<article><strong>${escapeHtml(item.title || "未命名事项")}</strong><span>${escapeHtml(item.category || "")} · ${escapeHtml(item.status || "未标记")}</span><p>${escapeHtml(item.progressText || item.planText || item.riskText || "暂无详情")}</p></article>`).join("") : '<p class="muted">本版周报未纳入事实记录。</p>';
    reportOriginalEdit = JSON.stringify(currentReportEditPayload());
    $("#reportDialog").showModal();
    return report;
  };

  const currentReportEditPayload = () => ({
    title: $("#reportEditTitle").value.trim(),
    sections: {
      ...Object.fromEntries(SECTION_KEYS.map((key) => [key, $(`#section-${key}`).value.trim()])),
      categorySections: $$("[data-report-category-key]", $("#reportCategorySections")).map((row) => ({
        key: row.dataset.reportCategoryKey,
        digest: $("textarea", row).value.trim(),
      })),
    },
  });
  const updateReportDirtyState = () => {
    reportIsDirty = JSON.stringify(currentReportEditPayload()) !== reportOriginalEdit;
    $("#saveSections").disabled = !reportIsDirty;
    $("#reportDirtyHint").textContent = reportIsDirty ? "有未保存修改；保存后需重新生成图片并再次预览。" : "修改后保存会清除旧图片和审核状态。";
    $("#reportDirtyHint").className = reportIsDirty ? "dirty" : "";
  };

  const closeReportEditor = () => {
    if (reportIsDirty && !window.confirm("当前修改尚未保存，确定放弃吗？")) return false;
    reportIsDirty = false;
    $("#reportDialog").close();
    if (routeFromHash() === "reports" && editReportIdFromHash()) window.history.replaceState(null, "", "#/reports");
    return true;
  };

  const openPublicReport = async (id) => {
    const data = await api(`/api/reports/${id}/public-urls`);
    if (!data.reportUrl) throw new Error("公开访问地址尚未配置");
    const link = document.createElement("a");
    link.href = data.reportUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    link.remove();
    return {message: "已在新窗口打开只读周报"};
  };

  const renderProjectRows = () => {
    const projects = Array.isArray(workflowConfig.projectBaseline) ? workflowConfig.projectBaseline : [];
    $("#projectRows").innerHTML = projects.map((item, index) => `<tr data-project-row="${index}"><td><input class="seq-input" data-project-field="seq" type="number" min="1" value="${escapeHtml(item.seq || index + 1)}"></td><td><input class="direction-input" data-project-field="direction" value="${escapeHtml(item.direction || "")}" placeholder="平台/产品"></td><td><input data-project-field="name" value="${escapeHtml(item.name || "")}" placeholder="项目名称"></td><td><input class="owner-input" data-project-field="owner" value="${escapeHtml(item.owner || "")}" placeholder="责任人"></td><td><select data-project-field="status">${Object.entries(PROJECT_STATUS).map(([value, label]) => `<option value="${value}" ${item.status === value ? "selected" : ""}>${label}</option>`).join("")}</select></td><td><textarea class="description-input" data-project-field="description" placeholder="项目定位、目标与边界">${escapeHtml(item.description || "")}</textarea></td><td><input class="visibility-input" data-project-field="visible" type="checkbox" ${item.visible === false ? "" : "checked"}></td><td><button class="remove-row" data-remove-project="${index}" type="button" aria-label="删除项目">×</button></td></tr>`).join("");
    $("#projectEmpty").hidden = projects.length > 0;
  };

  const readProjectRows = () => $$("[data-project-row]").map((row, index) => ({
    seq: toInt($("[data-project-field='seq']", row).value, index + 1),
    direction: $("[data-project-field='direction']", row).value.trim(),
    name: $("[data-project-field='name']", row).value.trim(),
    owner: $("[data-project-field='owner']", row).value.trim(),
    status: $("[data-project-field='status']", row).value,
    description: $("[data-project-field='description']", row).value.trim(),
    visible: $("[data-project-field='visible']", row).checked,
  })).filter((item) => item.name);

  const renderGroupRows = (key) => {
    const target = $(`#${key}`);
    const groups = Array.isArray(workflowConfig[key]) ? workflowConfig[key] : [];
    target.innerHTML = groups.length ? groups.map((item, index) => `<div class="group-row" data-group-row="${index}" data-group-key="${key}"><input data-group-field="name" value="${escapeHtml(item.name || "")}" placeholder="群名称"><input data-group-field="openConversationId" value="${escapeHtml(item.openConversationId || "")}" placeholder="openConversationId"><input data-group-field="robotCode" value="${escapeHtml(item.robotCode || "")}" placeholder="robotCode"><button data-remove-group="${key}:${index}" type="button" aria-label="删除群">×</button></div>`).join("") : '<div class="group-empty">暂未配置群目标</div>';
  };

  const readGroupRows = (key) => $$(`[data-group-key='${key}']`).map((row) => ({
    name: $("[data-group-field='name']", row).value.trim() || "钉钉群",
    openConversationId: $("[data-group-field='openConversationId']", row).value.trim(),
    robotCode: $("[data-group-field='robotCode']", row).value.trim(),
    enabled: true,
  })).filter((item) => item.openConversationId || item.robotCode);

  const renderPeopleTargets = (key) => {
    const people = Array.isArray(workflowConfig[key]) ? workflowConfig[key] : [];
    $(`#${key}`).innerHTML = people.length ? people.map((item, index) => `<span class="target-chip">${escapeHtml(item.name || item.userId)}<button data-remove-person="${key}:${index}" type="button" aria-label="移除">×</button></span>`).join("") : '<span class="muted">未配置</span>';
  };

  const renderConfigCollections = () => {
    renderProjectRows();
    ["previewGroupTargets", "formalGroupTargets"].forEach(renderGroupRows);
    ["previewPersonalTargets", "formalPersonalTargets", "approverTargets", "saturdayFinalPersonalTargets"].forEach(renderPeopleTargets);
  };

  const applyConfigToForm = (config, updateEditor = true) => {
    workflowConfig = config && typeof config === "object" ? structuredClone(config) : {};
    setValue("#reportTitle", workflowConfig.reportTitle || "产品与项目管理周报");
    setValue("#periodEndWeekday", workflowConfig.periodEndWeekday ?? 4);
    setValue("#periodEndTime", formatTime(workflowConfig.periodEndHour ?? 18, 0));
    setValue("#dueSoonDays", workflowConfig.dueSoonDays ?? 14);
    setChecked("#workflowEnabled", workflowConfig.enabled !== false);
    setChecked("#sourceSyncEnabled", workflowConfig.sourceSyncEnabled !== false);
    setChecked("#directorySyncEnabled", workflowConfig.directorySyncEnabled !== false);
    setChecked("#enforceDirectory", workflowConfig.enforceDirectoryForFormalSend !== false);
    setValue("#sourceSyncInterval", workflowConfig.sourceSyncIntervalMinutes ?? 60);
    setValue("#sourceFreshnessHours", workflowConfig.sourceFreshnessHours ?? 26);
    setChecked("#teambitionSyncEnabled", workflowConfig.teambitionSyncEnabled !== false);
    setChecked("#teambitionIncludeInReports", workflowConfig.teambitionIncludeInReports !== false);
    setValue("#teambitionSyncInterval", workflowConfig.teambitionSyncIntervalMinutes ?? 60);
    setValue("#teambitionDepartments", (workflowConfig.teambitionDepartmentNames || []).join("\n"));
    setValue("#projectManagerKeywords", (workflowConfig.projectManagerTitleKeywords || []).join("，"));
    setChecked("#archiveWriteEnabled", workflowConfig.archiveWriteEnabled);
    setValue("#archiveTableId", workflowConfig.archiveTableId || "");
    setValue("#archiveFieldMap", JSON.stringify(workflowConfig.archiveFieldMap || {}, null, 2));
    setValue("#defaultRobotCode", workflowConfig.defaultRobotCode || "");
    setValue("#generateWeekday", workflowConfig.generateWeekday ?? 4);
    setValue("#generateTime", formatTime(workflowConfig.generateHour ?? 18, workflowConfig.generateMinute ?? 10));
    setValue("#quietStartHour", workflowConfig.quietStartHour ?? 21);
    setValue("#quietEndHour", workflowConfig.quietEndHour ?? 8);
    setChecked("#autoGenerateEnabled", workflowConfig.autoGenerateEnabled);
    setChecked("#autoPreviewEnabled", workflowConfig.autoPreviewEnabled);
    setChecked("#requireApproval", workflowConfig.requireApproval !== false);
    setChecked("#requirePreviewBeforeFormal", workflowConfig.requirePreviewBeforeFormal !== false);
    setChecked("#sendGroupImages", workflowConfig.sendGroupImages !== false);
    renderConfigCollections();
    if (updateEditor) setValue("#configEditor", JSON.stringify(workflowConfig, null, 2));
  };

  const syncWorkflowConfigFromForm = ({strictJson = false} = {}) => {
    const period = parseTime($("#periodEndTime").value, 18, 0);
    const generate = parseTime($("#generateTime").value, 18, 10);
    workflowConfig = {
      ...workflowConfig,
      reportTitle: $("#reportTitle").value.trim(),
      periodEndWeekday: toInt($("#periodEndWeekday").value, 4),
      periodEndHour: period.hour,
      dueSoonDays: toInt($("#dueSoonDays").value, 14),
      enabled: $("#workflowEnabled").checked,
      sourceSyncEnabled: $("#sourceSyncEnabled").checked,
      directorySyncEnabled: $("#directorySyncEnabled").checked,
      enforceDirectoryForFormalSend: $("#enforceDirectory").checked,
      sourceSyncIntervalMinutes: toInt($("#sourceSyncInterval").value, 60),
      sourceFreshnessHours: toInt($("#sourceFreshnessHours").value, 26),
      teambitionSyncEnabled: $("#teambitionSyncEnabled").checked,
      teambitionIncludeInReports: $("#teambitionIncludeInReports").checked,
      teambitionSyncIntervalMinutes: toInt($("#teambitionSyncInterval").value, 60),
      teambitionDepartmentNames: $("#teambitionDepartments").value.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean),
      projectManagerTitleKeywords: $("#projectManagerKeywords").value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
      projectBaseline: readProjectRows(),
      archiveWriteEnabled: $("#archiveWriteEnabled").checked,
      archiveTableId: $("#archiveTableId").value.trim(),
      defaultRobotCode: $("#defaultRobotCode").value.trim(),
      previewGroupTargets: readGroupRows("previewGroupTargets"),
      formalGroupTargets: readGroupRows("formalGroupTargets"),
      generateWeekday: toInt($("#generateWeekday").value, 4),
      generateHour: generate.hour,
      generateMinute: generate.minute,
      quietStartHour: toInt($("#quietStartHour").value, 21),
      quietEndHour: toInt($("#quietEndHour").value, 8),
      autoGenerateEnabled: $("#autoGenerateEnabled").checked,
      autoPreviewEnabled: $("#autoPreviewEnabled").checked,
      requireApproval: $("#requireApproval").checked,
      requirePreviewBeforeFormal: $("#requirePreviewBeforeFormal").checked,
      sendGroupImages: $("#sendGroupImages").checked,
      autoFormalSendEnabled: false,
    };
    try { workflowConfig.archiveFieldMap = JSON.parse($("#archiveFieldMap").value || "{}"); }
    catch (error) { if (strictJson) throw new Error(`存档字段映射不是有效 JSON：${error.message}`); }
    return workflowConfig;
  };

  const loadConfig = async () => {
    const data = await api("/api/config");
    applyConfigToForm(data.config || {});
    return data;
  };

  const saveWorkflowConfig = async (label) => {
    const config = syncWorkflowConfigFromForm({strictJson: true});
    const data = await api("/api/config", {method: "PUT", body: JSON.stringify({config})});
    applyConfigToForm(data.config || {});
    await loadReadiness();
    return data;
  };

  const searchRecipients = async () => {
    const query = $("#recipientSearch").value.trim();
    const data = await api(`/api/directory?query=${encodeURIComponent(query)}&limit=100`);
    $("#recipientSelect").innerHTML = data.items.length ? data.items.map((item) => `<option value="${escapeHtml(item.userId)}" data-name="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${escapeHtml(item.department || item.bizGroup || item.title || "未标注部门")}</option>`).join("") : '<option value="">未找到人员，请先同步 bi_center</option>';
    return data;
  };

  const selectedPerson = () => {
    const option = $("#recipientSelect").selectedOptions[0];
    return option?.value ? {name: option.dataset.name || option.textContent.split(" · ")[0], userId: option.value, enabled: true} : null;
  };

  const addPersonTarget = (key) => {
    syncWorkflowConfigFromForm();
    const person = selectedPerson();
    if (!person) return showToast("请先搜索并选择人员。", "error");
    workflowConfig[key] = Array.isArray(workflowConfig[key]) ? workflowConfig[key] : [];
    if (key === "saturdayFinalPersonalTargets" && workflowConfig[key].length && !workflowConfig[key].some((item) => item.userId === person.userId)) {
      return showToast("周六最终版只能配置一名接收人；请先移除现有接收人。", "error");
    }
    if (!workflowConfig[key].some((item) => item.userId === person.userId)) workflowConfig[key].push(person);
    renderPeopleTargets(key);
    showToast("已加入配置草稿，请保存推送设置。", "success");
  };

  const renderEvents = () => {
    $("#robotEvents").innerHTML = eventItems.length ? eventItems.map((item, index) => `<article class="event"><div><strong>${escapeHtml(item.conversation_title || "单聊")}</strong><span>${escapeHtml(item.sender_nick || item.sender_id || "未知发送人")} · ${escapeHtml(item.command || "unknown")} · ${escapeHtml(item.handle_status || "")}</span><code>${escapeHtml(item.conversation_id || "private")} / ${escapeHtml(item.robot_code || "-")}</code></div><div class="actions">${item.conversation_id ? `<button class="secondary" data-event-target="preview:${index}" type="button">设为测试群</button><button class="secondary" data-event-target="formal:${index}" type="button">设为正式群</button>` : ""}<button class="secondary" data-event-target="approver:${index}" type="button">设为确认人</button></div></article>`).join("") : '<div class="empty-state"><strong>暂无真实回调事件</strong><p>私聊机器人发送 help，或在群里 @机器人 help 后再刷新。</p></div>';
  };

  const loadEvents = async () => {
    const data = await api("/api/dingtalk/robot/events?limit=30");
    eventItems = data.items || [];
    renderEvents();
    return data;
  };

  const addEventTarget = (type, index) => {
    syncWorkflowConfigFromForm();
    const event = eventItems[index];
    if (!event) return;
    if (type === "approver") {
      if (!event.sender_id) return showToast("该事件未识别 senderId。", "error");
      workflowConfig.approverTargets = workflowConfig.approverTargets || [];
      if (!workflowConfig.approverTargets.some((item) => item.userId === event.sender_id)) workflowConfig.approverTargets.push({name: event.sender_nick || event.sender_id, userId: event.sender_id, enabled: true});
      renderPeopleTargets("approverTargets");
    } else {
      if (!event.conversation_id || !event.robot_code) return showToast("该事件缺少群 ID 或 Robot Code。", "error");
      const key = type === "formal" ? "formalGroupTargets" : "previewGroupTargets";
      const otherKey = type === "formal" ? "previewGroupTargets" : "formalGroupTargets";
      if ((workflowConfig[otherKey] || []).some((item) => item.openConversationId === event.conversation_id)) return showToast("该群已配置在另一推送阶段，测试群和正式群不能重叠。", "error");
      workflowConfig[key] = workflowConfig[key] || [];
      if (!workflowConfig[key].some((item) => item.openConversationId === event.conversation_id)) workflowConfig[key].push({name: event.conversation_title || "钉钉群", openConversationId: event.conversation_id, robotCode: event.robot_code, enabled: true});
      renderGroupRows(key);
    }
    showToast("已加入配置草稿，请保存推送设置。", "success");
  };

  const modelProviderMeta = (value) => (modelConfig?.providers || []).find((item) => item.value === value) || {};
  const renderModelOptions = () => {
    const meta = modelProviderMeta($("#modelProvider").value);
    const values = [...new Set([$("#modelName").value, ...(meta.defaultModels || [])].filter(Boolean))];
    $("#modelNameOptions").innerHTML = values.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
  };
  const applyModelConfig = (data) => {
    modelConfig = data || {};
    const effective = modelConfig.effective || {};
    $("#modelProvider").innerHTML = (modelConfig.providers || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
    $("#modelProvider").value = effective.provider || "compatible";
    setValue("#modelApiBase", effective.apiBase || "");
    setValue("#modelName", effective.modelName || "");
    setValue("#modelApiKey", "");
    $("#modelApiKey").placeholder = effective.apiKeyMasked ? `留空保留 ${effective.apiKeyMasked}` : "请输入 API Key";
    $("#modelSource").textContent = effective.source === "weekly_assistant" ? "周报助手自定义" : "沿用 bi_center 部署配置";
    $("#modelEffectiveName").textContent = effective.modelName || "未配置";
    $("#modelKeyStatus").textContent = effective.hasApiKey ? `已配置 · ${effective.apiKeyMasked || "已脱敏"}` : "未配置";
    $("#resetModelConfig").disabled = !modelConfig.override;
    const lastTest = modelConfig.lastTest || {};
    if (lastTest.tested) {
      $("#modelFeedback").className = `feedback ${lastTest.ok ? "ok-text" : "warn"}`;
      $("#modelFeedback").textContent = lastTest.ok ? `最近连接测试通过 · ${lastTest.testedAt || ""}` : `最近连接测试失败 · ${lastTest.error || "未知错误"}`;
    }
    renderModelOptions();
  };
  const loadModelConfig = async () => applyModelConfig(await api("/api/model-config"));
  const modelPayload = () => ({provider: $("#modelProvider").value, apiBase: $("#modelApiBase").value.trim(), modelName: $("#modelName").value.trim(), apiKey: $("#modelApiKey").value.trim()});

  const run = async (label, callback, {refresh = true} = {}) => {
    if (!accessConnected && !tokenInput.value.trim()) {
      if (ssoConfigured) beginSsoLogin();
      else setAccessDisconnected("请先使用运维令牌连接管理端。");
      showToast("请先登录管理端。", "error");
      return null;
    }
    busy.textContent = `${label}处理中…`;
    try {
      const data = await callback();
      log(`${label}成功`, compactResult(data));
      showToast(`${label}成功`);
      if (refresh) await Promise.all([loadReadiness(), loadReports()]);
      return data;
    } catch (error) {
      log(`${label}失败`, error.message);
      showToast(`${label}失败：${error.message}`, "error");
      return null;
    } finally {
      busy.textContent = "";
    }
  };

  const refreshAll = async () => {
    if (!accessConnected && !tokenInput.value.trim()) {
      setAccessDisconnected(ssoConfigured ? "请先使用钉钉登录。" : "请先使用运维令牌连接管理端。", {open: false});
      if (ssoConfigured) beginSsoLogin();
      return false;
    }
    busy.textContent = "读取中…";
    try {
      await loadReadiness();
      setAccessConnected();
    } catch (error) {
      if (error.status !== 401) {
        log("连接管理端失败", error.message);
        showToast(`连接失败：${error.message}`, "error");
      }
      busy.textContent = "";
      return false;
    }
    const tasks = [loadReports(), loadConfig(), loadModelConfig(), loadCoverage(), loadEvents(), loadTeambitionDashboard(), loadKeyProjectStatuses()];
    const results = await Promise.allSettled(tasks);
    const errors = [...new Set(results.filter((item) => item.status === "rejected").map((item) => item.reason?.message || "未知错误"))];
    if (errors.length) {
      log("刷新部分失败", errors.join("\n"));
      showToast(`刷新失败：${errors[0]}`, "error");
    } else {
      showToast("数据已刷新");
    }
    busy.textContent = "";
    return accessConnected;
  };

  $("#saveToken").addEventListener("click", async () => {
    const candidate = tokenInput.value.trim();
    if (!candidate) return setAccessDisconnected("请输入管理令牌后再连接。");
    localStorage.removeItem("weeklyReportAdminToken");
    const connected = await refreshAll();
    if (!connected) return;
    localStorage.setItem("weeklyReportAdminToken", candidate);
    $(".auth-menu").removeAttribute("open");
    showToast("管理端连接成功");
  });
  $("#loginWithDingTalk").addEventListener("click", beginSsoLogin);
  $("#logoutSession").addEventListener("click", async () => {
    await fetch(resolveUrl("/api/auth/logout"), {method: "POST", credentials: "same-origin"});
    sessionStorage.setItem("weeklyReportManualLogout", "1");
    sessionAuthenticated = false;
    currentIdentityName = "";
    currentIdentityUserId = "";
    setAccessDisconnected("已退出当前钉钉账号。", {open: true});
  });
  $("#refreshAll").addEventListener("click", refreshAll);
  $("#refreshPersonalReport").addEventListener("click", () => run("刷新个人周报", () => loadPersonalContext(), {refresh: false}));
  $("#personalReportPeriod").addEventListener("change", () => run("切换个人周报周期", () => loadPersonalContext(Number($("#personalReportPeriod").value || 0)), {refresh: false}));
  $("#personalMemberSearch").addEventListener("input", (event) => {
    personalMemberSearch = event.target.value || "";
    renderPersonalMembers();
  });
  $("#openLatestReport").addEventListener("click", () => {
    const latest = reportItems[0];
    if (!latest) return showToast("暂无可打开的周报。", "error");
    run("外部打开周报", () => openPublicReport(latest.id), {refresh: false});
  });
  $("#refreshOverview").addEventListener("click", () => run("检查链路", loadReadiness, {refresh: false}));
  $("#refreshTeambition").addEventListener("click", () => run("刷新 TB 看板", loadTeambitionDashboard, {refresh: false}));
  $("#applyTeambitionFilters").addEventListener("click", () => run("筛选 TB 看板", loadTeambitionDashboard, {refresh: false}));
  $("#refreshKeyProjectStatuses").addEventListener("click", () => run("刷新重点项目状态", loadKeyProjectStatuses, {refresh: false}));
  $("#applyKeyProjectStatusFilters").addEventListener("click", () => run("筛选重点项目状态", loadKeyProjectStatuses, {refresh: false}));
  $("#keyProjectStatusFilter").addEventListener("change", () => run("筛选重点项目状态", loadKeyProjectStatuses, {refresh: false}));
  $("#keyProjectStatusSearch").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); run("搜索重点项目状态", loadKeyProjectStatuses, {refresh: false}); } });
  $("#syncKeyProjectStatuses").addEventListener("click", () => run("同步重点项目及TB状态", async () => {
    await api("/api/sync/source", {method: "POST"});
    const result = await api("/api/sync/teambition", {method: "POST"});
    await Promise.all([loadKeyProjectStatuses(), loadTeambitionDashboard(), loadReadiness()]);
    return result;
  }, {refresh: false}));
  $("#refreshCoverage").addEventListener("click", () => run("刷新人员覆盖", loadCoverage, {refresh: false}));
  $("#remindCoverage").addEventListener("click", () => {
    if (!window.confirm("确定只向当前缺报人员发送一次钉钉单聊提醒？")) return;
    run("提醒缺报人员", () => api("/api/coverage/remind", {method: "POST", body: JSON.stringify({reportKind: $("#reportKind").value})}));
  });
  $("#clearLog").addEventListener("click", () => { logBox.textContent = "等待操作。"; });
  $("#menuToggle").addEventListener("click", () => $("#appSidebar").classList.contains("show") ? closeSidebar() : openSidebar());
  $("#sidebarBackdrop").addEventListener("click", closeSidebar);
  window.addEventListener("hashchange", () => {
    setRoute(routeFromHash());
    if (routeFromHash() === "reports" && editReportIdFromHash() && (accessConnected || tokenInput.value.trim())) {
      run("打开周报编辑器", () => openReport(editReportIdFromHash()), {refresh: false});
    }
  });

  $$("[data-go]").forEach((button) => button.addEventListener("click", () => { window.location.hash = `#/${button.dataset.go}`; }));
  $$("[data-report-filter]").forEach((button) => button.addEventListener("click", () => {
    reportFilter = button.dataset.reportFilter;
    $$("[data-report-filter]").forEach((item) => item.classList.toggle("active", item === button));
    renderReports();
  }));
  $("#reportKindFilter").addEventListener("change", renderReports);
  $("#reportSearch").addEventListener("input", renderReports);
  $("#reportKind").addEventListener("change", () => run("刷新人员覆盖", loadCoverage, {refresh: false}));

  $("#addProject").addEventListener("click", () => {
    syncWorkflowConfigFromForm();
    workflowConfig.projectBaseline = workflowConfig.projectBaseline || [];
    const seq = workflowConfig.projectBaseline.length + 1;
    workflowConfig.projectBaseline.push({seq, direction: "", name: `新项目 ${seq}`, owner: "", status: "active", description: "", visible: true});
    renderProjectRows();
  });
  $$("[data-add-group]").forEach((button) => button.addEventListener("click", () => {
    syncWorkflowConfigFromForm();
    const key = button.dataset.addGroup;
    workflowConfig[key] = workflowConfig[key] || [];
    workflowConfig[key].push({name: "", openConversationId: "", robotCode: workflowConfig.defaultRobotCode || "", enabled: true});
    renderGroupRows(key);
  }));
  $$("[data-add-person]").forEach((button) => button.addEventListener("click", () => addPersonTarget(button.dataset.addPerson)));
  $("#searchRecipient").addEventListener("click", () => run("搜索人员", searchRecipients, {refresh: false}));
  $("#recipientSearch").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); run("搜索人员", searchRecipients, {refresh: false}); } });
  $("#applyPersonalTarget").addEventListener("click", () => {
    const person = selectedPerson();
    if (!person) return showToast("请先选择人员。", "error");
    if (!window.confirm("设为个人全流程会清空测试群和正式群草稿，是否继续？")) return;
    syncWorkflowConfigFromForm();
    workflowConfig.previewPersonalTargets = [person];
    workflowConfig.formalPersonalTargets = [person];
    workflowConfig.approverTargets = [person];
    workflowConfig.previewGroupTargets = [];
    workflowConfig.formalGroupTargets = [];
    renderConfigCollections();
    showToast("已生成个人全流程配置草稿，请保存推送设置。");
  });
  $("#saveReportConfig").addEventListener("click", () => run("保存周报配置", () => saveWorkflowConfig("周报配置"), {refresh: false}));
  $("#saveTeambitionConfig").addEventListener("click", () => run("保存 TB 配置", () => saveWorkflowConfig("TB 配置"), {refresh: false}));
  $("#saveDeliveryConfig").addEventListener("click", () => run("保存推送设置", () => saveWorkflowConfig("推送设置"), {refresh: false}));
  $("#applyConfigJson").addEventListener("click", () => {
    try { applyConfigToForm(JSON.parse($("#configEditor").value || "{}"), false); showToast("JSON 已应用到表单，尚未保存。"); }
    catch (error) { showToast(`JSON 格式错误：${error.message}`, "error"); }
  });
  $("#copyConfigJson").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#configEditor").value); showToast("配置 JSON 已复制。"); }
    catch (_) { showToast("复制失败，请手动选择文本。", "error"); }
  });

  $("#refreshEvents").addEventListener("click", () => run("刷新机器人事件", loadEvents, {refresh: false}));
  $("#modelProvider").addEventListener("change", () => {
    const meta = modelProviderMeta($("#modelProvider").value);
    setValue("#modelApiBase", meta.defaultApiBase || "");
    setValue("#modelName", meta.defaultModel || "");
    renderModelOptions();
  });
  $("#modelName").addEventListener("input", renderModelOptions);
  $("#testModelConfig").addEventListener("click", () => run("模型连接测试", async () => {
    const data = await api("/api/model-config/test", {method: "POST", body: JSON.stringify(modelPayload())});
    $("#modelFeedback").className = `feedback ${data.ok ? "ok-text" : "warn"}`;
    $("#modelFeedback").textContent = data.message || (data.ok ? "连接测试通过" : data.error || "连接测试失败");
    return data;
  }, {refresh: false}));
  $("#saveModelConfig").addEventListener("click", () => run("保存模型配置", async () => {
    const data = await api("/api/model-config", {method: "PUT", body: JSON.stringify(modelPayload())});
    applyModelConfig(data);
    await loadReadiness();
    return data;
  }, {refresh: false}));
  $("#resetModelConfig").addEventListener("click", () => {
    if (!window.confirm("确认恢复沿用部署环境中同步自 bi_center 的模型配置？")) return;
    run("恢复模型配置", async () => { const data = await api("/api/model-config", {method: "DELETE"}); applyModelConfig(data); await loadReadiness(); return data; }, {refresh: false});
  });

  SECTION_KEYS.forEach((key) => $(`#section-${key}`).addEventListener("input", updateReportDirtyState));
  $("#reportEditTitle").addEventListener("input", updateReportDirtyState);
  $("#reportCategorySections").addEventListener("input", updateReportDirtyState);
  $("#closeReportDialog").addEventListener("click", closeReportEditor);
  $("#cancelSections").addEventListener("click", closeReportEditor);
  $("#reportDialog").addEventListener("cancel", (event) => {
    if (!reportIsDirty) return;
    event.preventDefault();
    closeReportEditor();
  });
  $("#saveSections").addEventListener("click", () => run("保存周报正文", async () => {
    if (!activeReportId) throw new Error("未选择周报");
    const payload = currentReportEditPayload();
    const data = await api(`/api/reports/${activeReportId}/sections`, {method: "PUT", body: JSON.stringify(payload)});
    reportIsDirty = false;
    $("#reportDialog").close();
    if (routeFromHash() === "reports" && editReportIdFromHash()) window.history.replaceState(null, "", "#/reports");
    return data;
  }));
  $("#personalEditDialog").addEventListener("input", updatePersonalDirtyState);
  $("#closePersonalEdit").addEventListener("click", closePersonalEditor);
  $("#cancelPersonalEdit").addEventListener("click", closePersonalEditor);
  $("#personalEditDialog").addEventListener("cancel", (event) => {
    if (!personalIsDirty) return;
    event.preventDefault();
    closePersonalEditor();
  });
  $("#savePersonalEdit").addEventListener("click", () => run("保存个人周报", async () => {
    if (!activePersonalReport?.reportId) throw new Error("未选择个人周报");
    const payload = currentPersonalEditPayload();
    const data = await api(`/api/personal-reports/${activePersonalReport.reportId}`, {method: "PUT", body: JSON.stringify(payload)});
    personalIsDirty = false;
    $("#personalEditDialog").close();
    activePersonalReport = data;
    renderPersonalReport(data);
    await loadPersonalContext(data.reportId);
    return data;
  }, {refresh: false}));

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-start-login]")) {
      if (ssoConfigured) beginSsoLogin();
      else {
        $(".auth-menu").open = true;
        $(".maintenance-auth").open = true;
        tokenInput.focus();
      }
      return;
    }
    if (event.target.closest("[data-open-auth]")) {
      $(".auth-menu").open = true;
      tokenInput.focus();
      return;
    }
    if (event.target.closest("[data-personal-edit]")) {
      run("打开个人周报编辑器", openPersonalEditor, {refresh: false});
      return;
    }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "sync-source") run("同步 AI 表", () => api("/api/sync/source", {method: "POST"}));
    if (action === "sync-teambition") run("同步 TB", async () => {
      const data = await api("/api/sync/teambition", {method: "POST"});
      await Promise.all([loadTeambitionDashboard(), loadKeyProjectStatuses()]);
      return data;
    });
    if (action === "sync-directory") run("同步人员目录", () => api("/api/sync/directory", {method: "POST"}));
    if (action === "generate") run("生成周报", () => api("/api/reports/generate", {method: "POST", body: JSON.stringify({reportKind: $("#reportKind").value, useAI: true})}));

    const removeProject = event.target.closest("[data-remove-project]");
    if (removeProject) {
      syncWorkflowConfigFromForm();
      workflowConfig.projectBaseline.splice(toInt(removeProject.dataset.removeProject), 1);
      renderProjectRows();
      return;
    }
    const removeGroup = event.target.closest("[data-remove-group]");
    if (removeGroup) {
      syncWorkflowConfigFromForm();
      const [key, index] = removeGroup.dataset.removeGroup.split(":");
      workflowConfig[key].splice(toInt(index), 1);
      renderGroupRows(key);
      return;
    }
    const removePerson = event.target.closest("[data-remove-person]");
    if (removePerson) {
      const [key, index] = removePerson.dataset.removePerson.split(":");
      workflowConfig[key] = workflowConfig[key] || [];
      workflowConfig[key].splice(toInt(index), 1);
      renderPeopleTargets(key);
      return;
    }
    const eventTarget = event.target.closest("[data-event-target]");
    if (eventTarget) {
      const [type, index] = eventTarget.dataset.eventTarget.split(":");
      addEventTarget(type, toInt(index));
      return;
    }

    const reportButton = event.target.closest("[data-report-action]");
    const personalMember = event.target.closest("[data-personal-user-id]");
    if (personalMember) {
      activePersonalUserId = personalMember.dataset.personalUserId;
      run("查看成员周报", () => loadPersonalReport(undefined, activePersonalUserId), {refresh: false}).then((data) => {
        if (data) $("#personalHero")?.scrollIntoView({behavior: "smooth", block: "start"});
      });
      return;
    }
    if (!reportButton) return;
    const id = reportButton.dataset.reportId;
    const reportAction = reportButton.dataset.reportAction;
    if (reportAction === "browse") { run("浏览周报", () => openPublicReport(id), {refresh: false}); return; }
    if (reportAction === "edit") { run("编辑周报", () => openReport(id), {refresh: false}); return; }
    const routes = {render: ["生成图片", `/api/reports/${id}/render`], preview: ["发送预览", `/api/reports/${id}/preview`], approve: ["审核通过", `/api/reports/${id}/approve`], formal: ["正式发送", `/api/reports/${id}/formal-send`], archive: ["归档", `/api/reports/${id}/archive`], recall: ["撤回消息", `/api/reports/${id}/recall`]};
    const [label, path] = routes[reportAction] || [];
    if (!path) return;
    if (["formal", "archive", "recall"].includes(reportAction) && !window.confirm(`确定要${label}周报 #${id}？${reportAction === "formal" ? "该操作将发送到正式目标。" : ""}`)) return;
    run(label, () => api(path, {method: "POST"}));
  });

  if (!$("#teambitionMonth").value) {
    const today = new Date();
    $("#teambitionMonth").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
  }
  setRoute(routeFromHash());
  if (!window.location.hash) window.history.replaceState(null, "", "#/overview");
  const initializeAccess = async () => {
    try {
      const response = await fetch(resolveUrl("/api/auth/session"), {credentials: "same-origin"});
      const status = await response.json();
      ssoConfigured = Boolean(status.ssoConfigured);
      sessionAuthenticated = Boolean(status.authenticated);
      currentIdentityName = String(status.user?.name || "");
      currentIdentityUserId = String(status.user?.userId || "");
      if (sessionAuthenticated) {
        sessionStorage.removeItem("weeklyReportManualLogout");
        setAccessConnected(currentIdentityName);
        await refreshAll();
        if (routeFromHash() === "reports" && editReportIdFromHash()) await openReport(editReportIdFromHash());
        if (routeFromHash() === "personal-reports") await loadPersonalContext(personalReportIdFromHash());
        return;
      }
      if (ssoConfigured && sessionStorage.getItem("weeklyReportManualLogout") !== "1") {
        beginSsoLogin();
        return;
      }
      if (tokenInput.value) {
        await refreshAll();
        if (routeFromHash() === "reports" && editReportIdFromHash()) await openReport(editReportIdFromHash());
        return;
      }
      setAccessDisconnected(ssoConfigured ? "请使用钉钉登录。" : "钉钉登录尚未配置，可使用运维令牌兜底。", {open: false});
    } catch (error) {
      setAccessDisconnected(`登录状态检查失败：${error.message}`, {open: false});
    }
  };
  initializeAccess();
})();
