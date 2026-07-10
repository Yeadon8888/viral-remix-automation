const state = {
  token: localStorage.getItem("viral-remix-admin-token") || "",
  materials: [],
  tasks: [],
  doubaoItems: [],
  prompts: [],
  jobs: [],
  pendingJobs: new Set(),
  collapsedMaterialDateGroups: new Set(),
  seenMaterialDateGroups: new Set(),
  collapsedTaskDateGroups: new Set(),
  seenTaskDateGroups: new Set(),
  collapsedDoubaoDateGroups: new Set(),
  seenDoubaoDateGroups: new Set(),
  editingPromptId: "",
  selectedMaterialId: localStorage.getItem("viral-remix-selected-material") || "",
  selectedMaterialIds: new Set(),
  selectedTaskIds: new Set(),
  selectedDoubaoIds: new Set(),
  activePage: localStorage.getItem("viral-remix-active-page") || "materials",
  loadedPages: new Set(),
  lastActiveJobNames: "",
  pollTick: 0,
  pollBusy: false,
  lastJobsSnapshot: "",
  lastLogsSnapshot: "",
  promptDetails: new Map(),
  materialPage: 1,
  materialPageSize: 20,
  taskPage: 1,
  taskPageSize: 20,
  doubaoPage: 1,
  doubaoPageSize: 20,
  imageStyles: ["viral_first_frame", "lifestyle", "model", "detail", "flatlay", "outdoor", "studio"],
  imageStyleLabels: {
    viral_first_frame: "爆款首帧",
    lifestyle: "生活场景",
    model: "模特展示",
    detail: "细节特写",
    flatlay: "平铺摆拍",
    outdoor: "户外场景",
    studio: "棚拍风格",
  },
};

const $ = (id) => document.getElementById(id);

function headers() {
  return state.token
    ? { "X-Admin-Token": state.token, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" };
}

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  const data = await res.json();
  if (!res.ok || data.success === false) throw new Error(data.message || data.error || "请求失败");
  return data;
}

async function localWorkerApi(path, options = {}) {
  const res = await fetch(`http://127.0.0.1:5177${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.success === false) throw new Error(data.message || data.error || `本地 Worker 请求失败：${res.status}`);
  return data;
}

function tablePath(path, { force = false } = {}) {
  const joiner = path.includes("?") ? "&" : "?";
  return force ? `${path}${joiner}force=1` : path;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function compactErrorMessage(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (/chromium_headless_shell|playwright install|Executable doesn't exist|PLAYWRIGHT_BROWSERS_PATH/i.test(text)) {
    return "本地 Worker 缺少浏览器运行时，请换用最新版离线包后重启 Worker。";
  }
  if (/自动化系统请求失败[:：]?\s*500|HTTP\s*500/i.test(text)) {
    return "自动化系统请求失败：500，请检查本地 Worker 是否已启动并使用最新版离线包。";
  }
  if (/Failed to fetch|NetworkError|Load failed/i.test(text)) {
    return "网络请求失败，请检查本地 Worker、网络或接口配置。";
  }
  return text.length > 72 ? `${text.slice(0, 72)}...` : text;
}

function renderInlineError(value, fallback = "暂无") {
  const raw = String(value || "").trim();
  if (!raw) return `<span class="muted">${esc(fallback)}</span>`;
  const shortText = compactErrorMessage(raw);
  const hasDetail = raw.length > shortText.length || raw !== shortText;
  return `<div class="inline-error">
    <span title="${esc(shortText)}">${esc(shortText)}</span>
    ${hasDetail ? renderPromptDetail("错误详情", raw, "error-detail") : ""}
  </div>`;
}

function stableId(value) {
  let hash = 5381;
  const text = String(value || "");
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash) ^ text.charCodeAt(i);
  }
  return `p${(hash >>> 0).toString(36)}`;
}

function rememberPromptDetail(id, body) {
  state.promptDetails.set(id, body);
  if (state.promptDetails.size > 500) {
    state.promptDetails.delete(state.promptDetails.keys().next().value);
  }
}

function activePageLoaded(page = state.activePage) {
  return state.loadedPages.has(page);
}

function markPageLoaded(page = state.activePage) {
  state.loadedPages.add(page);
}

function hasRunningJobs() {
  return state.jobs.some((job) => job.status === "running") || state.pendingJobs.size > 0;
}

function refreshTimeLabel() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function markTableLoaded(key, label, count) {
  const target = $(`${key}RefreshState`);
  if (target) target.textContent = `${label}已从飞书刷新 ${count} 条 · ${refreshTimeLabel()}`;
}

async function refreshTable(buttonId, loader) {
  const button = $(buttonId);
  const oldText = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "刷新中";
  }
  try {
    await loader();
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
    }
  }
}

function status(value) {
  const v = esc(value || "未处理");
  return `<span class="status ${v}">${v}</span>`;
}

function renderMaterialStatus(row) {
  const value = String(row["自动化状态"] || "未处理");
  const name = `retry-failed-step:${row.record_id}`;
  const pending = state.pendingJobs.has(name);
  const busy = isJobBusy(name);
  const retry = value === "失败"
    ? `<button class="mini retry-mini${pending ? " is-pending" : ""}" data-action="retry-failed-material" data-id="${esc(row.record_id)}" ${busy ? "disabled" : ""}>${pending ? "提交中" : busy ? "重试中" : "重试"}</button>`
    : "";
  return `<div class="status-cell">${status(value)}${retry}</div>`;
}

function percentValue(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(n <= 1 ? Math.round(n * 100) : Math.round(n), 100));
}

function clampPage(page, total, pageSize) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return Math.max(1, Math.min(Number(page) || 1, totalPages));
}

function pageRows(rows, page, pageSize) {
  const current = clampPage(page, rows.length, pageSize);
  const start = (current - 1) * pageSize;
  return rows.slice(start, start + pageSize);
}

function pagerState(key) {
  if (key === "materials") return { pageKey: "materialPage", sizeKey: "materialPageSize" };
  if (key === "tasks") return { pageKey: "taskPage", sizeKey: "taskPageSize" };
  return { pageKey: "doubaoPage", sizeKey: "doubaoPageSize" };
}

function renderPagination(key, total) {
  const { pageKey, sizeKey } = pagerState(key);
  const size = Number(state[sizeKey] || 20);
  const page = clampPage(state[pageKey], total, size);
  state[pageKey] = page;
  const totalPages = Math.max(1, Math.ceil(total / size));
  const start = total ? (page - 1) * size + 1 : 0;
  const end = Math.min(page * size, total);
  const sizeOptions = [10, 20, 50].map((item) => (
    `<button class="page-size${item === size ? " active" : ""}" type="button" data-page-size="${esc(key)}:${item}">${item}</button>`
  )).join("");
  const pageButtons = [];
  const from = Math.max(1, page - 2);
  const to = Math.min(totalPages, page + 2);
  if (from > 1) pageButtons.push(`<button class="page-jump" type="button" data-page-to="${esc(key)}:1">1</button>`);
  if (from > 2) pageButtons.push(`<span class="page-ellipsis">...</span>`);
  for (let i = from; i <= to; i += 1) {
    pageButtons.push(`<button class="page-jump${i === page ? " active" : ""}" type="button" data-page-to="${esc(key)}:${i}">${i}</button>`);
  }
  if (to < totalPages - 1) pageButtons.push(`<span class="page-ellipsis">...</span>`);
  if (to < totalPages) pageButtons.push(`<button class="page-jump" type="button" data-page-to="${esc(key)}:${totalPages}">${totalPages}</button>`);
  return `<div class="pagination-bar" data-pager="${esc(key)}">
    <div class="page-meta">显示 ${start}-${end} / ${total} 条</div>
    <div class="page-size-group" aria-label="每页条数">${sizeOptions}</div>
    <div class="page-jumps" aria-label="页码">
      <button class="page-jump" type="button" data-page-to="${esc(key)}:${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button>
      ${pageButtons.join("")}
      <button class="page-jump" type="button" data-page-to="${esc(key)}:${page + 1}" ${page >= totalPages ? "disabled" : ""}>下一页</button>
    </div>
  </div>`;
}

function bindPagination(root, key, renderFn) {
  root.querySelectorAll(`[data-page-size^="${key}:"]`).forEach((button) => {
    button.addEventListener("click", () => {
      const size = Number(button.dataset.pageSize.split(":")[1]);
      const { pageKey, sizeKey } = pagerState(key);
      state[sizeKey] = size;
      state[pageKey] = 1;
      renderFn();
    });
  });
  root.querySelectorAll(`[data-page-to^="${key}:"]`).forEach((button) => {
    button.addEventListener("click", () => {
      const page = Number(button.dataset.pageTo.split(":")[1]);
      const { pageKey } = pagerState(key);
      state[pageKey] = page;
      renderFn();
    });
  });
}

function renderProgress(value) {
  const pct = percentValue(value);
  return `<div class="progress-cell">
    <div class="progress-track"><span style="width:${pct}%"></span></div>
    <strong>${pct}%</strong>
  </div>`;
}

function dateGroupFromTimestamp(raw) {
  const time = Number(raw || 0);
  if (!time) return { key: "unknown", label: "未记录日期" };
  const date = new Date(time > 1e12 ? time : time * 1000);
  if (Number.isNaN(date.getTime())) return { key: "unknown", label: "未记录日期" };
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const key = `${y}-${m}-${d}`;
  const label = date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
  return { key, label };
}

function taskDateGroup(row) {
  return dateGroupFromTimestamp(row["生成时间"]);
}

function materialDateGroup(row) {
  return dateGroupFromTimestamp(row["自动化处理时间"]);
}

function doubaoDateGroup(row) {
  const group = dateGroupFromTimestamp(row["Dola完成时间"] || row["更新时间"] || row["创建时间"]);
  return group.key === "unknown" ? { key: "done", label: "已生成记录" } : group;
}

function groupRowsByDate(rows, getGroup) {
  const groups = new Map();
  rows.forEach((row) => {
    const group = getGroup(row);
    if (!groups.has(group.key)) groups.set(group.key, { ...group, rows: [] });
    groups.get(group.key).rows.push(row);
  });
  return [...groups.values()].sort((a, b) => b.key.localeCompare(a.key));
}

function shortUrl(url) {
  const text = String(url || "").trim();
  if (!text) return "未填写爆款视频链接";
  return text.length > 74 ? `${text.slice(0, 42)}...${text.slice(-20)}` : text;
}

function sourceIdsOfTask(task) {
  const ids = Array.isArray(task["关联素材记录IDs"]) ? task["关联素材记录IDs"] : [];
  const fallback = task["来源素材记录ID"];
  return fallback && !ids.includes(fallback) ? [...ids, fallback] : ids;
}

function tasksForSelected() {
  if (!state.selectedMaterialId) return state.tasks;
  return state.tasks.filter((task) => sourceIdsOfTask(task).includes(state.selectedMaterialId));
}

function selectedVisibleTaskRows() {
  const visibleIds = new Set(tasksForSelected().map((row) => row.record_id));
  return state.tasks.filter((row) => visibleIds.has(row.record_id) && state.selectedTaskIds.has(row.record_id));
}

function selectedDoubaoRows() {
  return state.doubaoItems.filter((row) => state.selectedDoubaoIds.has(row.record_id));
}

function isJobRunning(name) {
  return state.jobs.some((job) => job.name === name && job.status === "running");
}

function isJobBusy(name) {
  return state.pendingJobs.has(name) || isJobRunning(name);
}

function setPendingJob(name, pending) {
  if (pending) state.pendingJobs.add(name);
  else state.pendingJobs.delete(name);
}

function renderConfig(data) {
  const ok = Array.isArray(data.missing) && data.missing.length === 0;
  if (Array.isArray(data.image_styles) && data.image_styles.length) state.imageStyles = data.image_styles;
  if (data.image_style_labels && typeof data.image_style_labels === "object") state.imageStyleLabels = data.image_style_labels;
  $("configLine").textContent = ok
    ? `已连接：素材表 ${data.source_table_id} / 任务表 ${data.task_table_id} / 发布表 ${data.publish_table_id || "-"} / 豆包表 ${data.doubao_table_id || "-"} / Prompt表 ${data.prompt_table_id || "-"} / 视频理解 ${data.gemini_model || "-"} / 存储 ${data.storage_mode}`
    : `缺少配置：${data.missing.join(", ")}`;
}

async function loadConfig() {
  try {
    renderConfig(await api("/api/config"));
  } catch (err) {
    $("configLine").textContent = String(err);
  }
}

function renderMetrics() {
  $("metricMaterials").textContent = state.materials.length;
  $("metricMainImages").textContent = state.materials.filter((row) => String(row["商品主图存储链接"] || "").startsWith("http")).length;
  $("metricScripts").textContent = state.materials.filter((row) => row.has_script).length;
  $("metricImages").textContent = state.materials.filter((row) => Number(row["商品组图数量"] || 0) > 0).length;
  $("metricTasks").textContent = state.materials.reduce((sum, row) => sum + Number(row["任务数量"] || 0), 0);
}

function renderPage() {
  const page = ["materials", "videos", "doubao", "prompts"].includes(state.activePage) ? state.activePage : "materials";
  state.activePage = page;
  $("materialsPage").classList.toggle("active", page === "materials");
  $("videosPage").classList.toggle("active", page === "videos");
  $("doubaoPage").classList.toggle("active", page === "doubao");
  $("promptsPage").classList.toggle("active", page === "prompts");
  $("pageMaterials").classList.toggle("active", page === "materials");
  $("pageVideos").classList.toggle("active", page === "videos");
  $("pageDoubao").classList.toggle("active", page === "doubao");
  $("pagePrompts").classList.toggle("active", page === "prompts");
  localStorage.setItem("viral-remix-active-page", page);
}

function renderSelectionState() {
  const visibleIds = currentVisibleMaterialRows().map((row) => row.record_id).filter(Boolean);
  const selectedCount = state.materials.filter((row) => state.selectedMaterialIds.has(row.record_id)).length;
  const visibleSelectedCount = visibleIds.filter((id) => state.selectedMaterialIds.has(id)).length;
  $("selectedMaterialCount").textContent = `已选 ${selectedCount} 条`;
  $("selectAllMaterials").checked = visibleIds.length > 0 && visibleSelectedCount === visibleIds.length;
  $("selectAllMaterials").indeterminate = visibleSelectedCount > 0 && visibleSelectedCount < visibleIds.length;
}

function rowNumber(row) {
  const value = Number(row["行号"]);
  return Number.isFinite(value) ? value : 0;
}

function selectMaterialRange() {
  const start = Number($("rangeStart").value);
  const end = Number($("rangeEnd").value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) {
    alert("请输入有效的起始行和结束行");
    return;
  }
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  const selectedRows = state.materials.filter((row) => {
    const line = rowNumber(row);
    return line >= from && line <= to && row.record_id;
  });
  state.selectedMaterialIds = new Set(selectedRows.map((row) => row.record_id));
  renderMaterials();
}

function clearMaterialSelection() {
  state.selectedMaterialIds.clear();
  renderMaterials();
}

function renderTaskSelectionState() {
  const visibleIds = currentVisibleTaskRows().map((row) => row.record_id).filter(Boolean);
  const selectedCount = tasksForSelected().filter((row) => state.selectedTaskIds.has(row.record_id)).length;
  const visibleSelectedCount = visibleIds.filter((id) => state.selectedTaskIds.has(id)).length;
  $("selectedTaskCount").textContent = `已选 ${selectedCount} 条`;
  $("selectAllTasks").checked = visibleIds.length > 0 && visibleSelectedCount === visibleIds.length;
  $("selectAllTasks").indeterminate = visibleSelectedCount > 0 && visibleSelectedCount < visibleIds.length;
}

function selectTaskRange() {
  const start = Number($("taskRangeStart").value);
  const end = Number($("taskRangeEnd").value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) {
    alert("请输入有效的起始行和结束行");
    return;
  }
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  const selectedRows = tasksForSelected().filter((row) => {
    const line = rowNumber(row);
    return line >= from && line <= to && row.record_id;
  });
  state.selectedTaskIds = new Set(selectedRows.map((row) => row.record_id));
  renderTasks();
}

function clearTaskSelection() {
  state.selectedTaskIds.clear();
  renderTasks();
}

function currentVisibleDoubaoRows() {
  const activeRows = state.doubaoItems.filter((row) => !isCompletedDoubao(row));
  return pageRows(activeRows, state.doubaoPage, state.doubaoPageSize);
}

function renderDoubaoSelectionState() {
  const visibleIds = currentVisibleDoubaoRows().map((row) => row.record_id).filter(Boolean);
  const selectedCount = state.doubaoItems.filter((row) => state.selectedDoubaoIds.has(row.record_id)).length;
  const visibleSelectedCount = visibleIds.filter((id) => state.selectedDoubaoIds.has(id)).length;
  $("selectedDoubaoCount").textContent = `已选 ${selectedCount} 条`;
  $("selectAllDoubao").checked = visibleIds.length > 0 && visibleSelectedCount === visibleIds.length;
  $("selectAllDoubao").indeterminate = visibleSelectedCount > 0 && visibleSelectedCount < visibleIds.length;
}

function selectDoubaoRange() {
  const start = Number($("doubaoRangeStart").value);
  const end = Number($("doubaoRangeEnd").value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) {
    alert("请输入有效的起始行和结束行");
    return;
  }
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  const selectedRows = state.doubaoItems.filter((row) => {
    const line = rowNumber(row);
    return line >= from && line <= to && row.record_id;
  });
  state.selectedDoubaoIds = new Set(selectedRows.map((row) => row.record_id));
  renderDoubaoItems();
}

function clearDoubaoSelection() {
  state.selectedDoubaoIds.clear();
  renderDoubaoItems();
}

function renderThumbs(urls) {
  const list = Array.isArray(urls) ? urls : [];
  if (!list.length) return `<span class="muted">未生成</span>`;
  const imgs = list.slice(0, 3).map((url) => `<img src="${esc(url)}" alt="">`).join("");
  return `<div class="thumbs">${imgs}<span class="thumb-count">${list.length} 张</span></div>`;
}

function renderMainImage(row) {
  const url = String(row["商品主图存储链接"] || "").trim();
  if (!url.startsWith("http")) return `<span class="muted">待转存</span>`;
  return `<div class="asset-cell">${renderThumbs([url])}<a href="${esc(url)}" target="_blank">对象存储</a></div>`;
}

function selectedTemplateValues(row) {
  const values = row["商品组图模板值"];
  if (Array.isArray(values) && values.length) return [values[0]];
  return state.imageStyles.length ? [state.imageStyles[0]] : [];
}

function renderTemplateControl(row) {
  const values = selectedTemplateValues(row);
  const labels = values.map((style) => state.imageStyleLabels[style] || style);
  const summary = labels.length > 2 ? `${labels.slice(0, 2).join("、")} +${labels.length - 2}` : labels.join("、");
  return `<details class="template-menu" data-template-id="${esc(row.record_id)}">
    <summary>${esc(summary || "默认模板")}</summary>
    <div class="template-popover">
      ${state.imageStyles.map((style) => {
        const label = state.imageStyleLabels[style] || style;
        const checked = values.includes(style) ? "checked" : "";
        return `<label class="template-option">
          <input type="radio" name="template-${esc(row.record_id)}" value="${esc(style)}" ${checked}>
          <span>${esc(label)}</span>
        </label>`;
      }).join("")}
      <button class="mini" data-action="save-templates" data-id="${esc(row.record_id)}">保存模板</button>
    </div>
  </details>`;
}

function stepActionButton(action, id, label, extraClass = "") {
  const name = `${action}:${id}`;
  const pending = state.pendingJobs.has(name);
  const busy = isJobBusy(name);
  return `<button class="mini ${extraClass}${pending ? " is-pending" : ""}" data-step="${esc(action)}" data-id="${esc(id)}" ${busy ? "disabled" : ""}>${pending ? "提交中" : busy ? "进行中" : esc(label)}</button>`;
}

function renderPromptDetail(label, text, extraClass = "") {
  const body = String(text || "").trim();
  if (!body) return "";
  const id = stableId(`${label}:${body}`);
  rememberPromptDetail(id, body);
  return `<details class="prompt-preview wide ${esc(extraClass)}" data-prompt-detail="${id}"><summary>${esc(label)}</summary><p class="muted">展开后加载内容</p></details>`;
}

function renderRemotePromptDetail(label, url, hasContent) {
  if (!hasContent) return "";
  const id = stableId(`${label}:${url}`);
  return `<details class="prompt-preview wide" data-prompt-detail="${id}" data-prompt-url="${esc(url)}"><summary>${esc(label)}</summary><p class="muted">展开后加载内容</p></details>`;
}

function bindPromptDetails(root = document) {
  root.querySelectorAll("[data-prompt-detail]").forEach((detail) => {
    if (detail.dataset.bound === "1") return;
    detail.dataset.bound = "1";
    detail.addEventListener("toggle", async () => {
      if (!detail.open || detail.dataset.loaded === "1") return;
      const target = detail.querySelector("p");
      try {
        const url = detail.dataset.promptUrl || "";
        if (url && !state.promptDetails.has(detail.dataset.promptDetail)) {
          if (target) target.textContent = "加载中...";
          const data = await api(url);
          rememberPromptDetail(detail.dataset.promptDetail, data.data?.text || "");
        }
        const text = state.promptDetails.get(detail.dataset.promptDetail) || "";
        if (target) {
          target.classList.remove("muted");
          target.textContent = text || "暂无内容";
        }
        detail.dataset.loaded = "1";
      } catch (err) {
        if (target) target.textContent = String(err);
      }
    });
  });
}

function materialDetailUrl(row, field) {
  return `/api/materials/${encodeURIComponent(row.record_id)}/details?field=${encodeURIComponent(field)}`;
}

function renderScriptStep(row) {
  const prompt = String(row["视频生成Prompt"] || "").trim();
  const title = String(row["生成标题"] || "").trim();
  const caption = String(row["生成文案"] || "").trim();
  const keywords = String(row["关键词"] || "").trim();
  const analysisTemplate = String(row["视频拆解提示词模板"] || "").trim();
  const analysisPrompt = String(row["视频拆解实际提示词"] || "").trim();
  const hasPromptDetail = Boolean(prompt || row.has_publish_detail || row.has_prompt);
  return `<div class="step-cell">
    ${hasPromptDetail ? status("已生成Prompt") : status("未生成")}
    ${title ? `<div class="publish-chip">${esc(title)}</div>` : ""}
    ${prompt ? renderPromptDetail("视频生成Prompt/发布内容", [title, caption, keywords, prompt].filter(Boolean).join("\n\n")) : renderRemotePromptDetail("视频生成Prompt/发布内容", materialDetailUrl(row, "publish"), hasPromptDetail)}
    ${analysisTemplate ? renderPromptDetail("视频拆解覆盖模板", analysisTemplate) : renderRemotePromptDetail("视频拆解覆盖模板", materialDetailUrl(row, "video_template"), row.has_video_template_detail)}
    ${analysisPrompt ? renderPromptDetail("视频拆解实际提示词", analysisPrompt) : renderRemotePromptDetail("视频拆解实际提示词", materialDetailUrl(row, "video_actual"), row.has_video_actual_detail)}
    ${stepActionButton("analyze-video", row.record_id, hasPromptDetail ? "重新生成" : "生成Prompt")}
  </div>`;
}

function renderImageStep(row) {
  const hasMain = String(row["商品主图存储链接"] || "").startsWith("http");
  return `<div class="step-cell">
    ${renderMainImage(row)}
    ${stepActionButton("store-image", row.record_id, hasMain ? "重新入库" : "主图入库")}
  </div>`;
}

function renderGroupStep(row) {
  const hasGroup = Number(row["商品组图数量"] || 0) > 0;
  const imageTemplate = String(row["商品组图提示词模板"] || "").trim();
  const imagePrompt = String(row["商品组图实际提示词"] || "").trim();
  return `<div class="step-cell group-step">
    ${renderThumbs(row["商品组图URLs"])}
    ${renderTemplateControl(row)}
    ${imageTemplate ? renderPromptDetail("商品组图覆盖模板", imageTemplate) : renderRemotePromptDetail("商品组图覆盖模板", materialDetailUrl(row, "image_template"), row.has_image_template_detail)}
    ${imagePrompt ? renderPromptDetail("商品组图实际提示词", imagePrompt) : renderRemotePromptDetail("商品组图实际提示词", materialDetailUrl(row, "image_actual"), row.has_image_actual_detail)}
    ${stepActionButton("generate-images", row.record_id, hasGroup ? "重新生成" : "生成组图")}
  </div>`;
}

function renderTaskStep(row) {
  const taskTotal = Number(row["任务数量"] || 0);
  const taskDone = Number(row["已完成视频数量"] || 0);
  return `<div class="step-cell">
    <strong>${taskTotal}</strong>
    <div class="muted">${taskDone} 已完成</div>
    ${stepActionButton("create-tasks", row.record_id, taskTotal ? "继续建任务" : "创建任务")}
  </div>`;
}

function renderMaterialRow(row, extraClass = "", hidden = false) {
  const videoUrl = row["爆款视频链接"];
  const selected = row.record_id === state.selectedMaterialId ? " selected" : "";
  const checked = state.selectedMaterialIds.has(row.record_id) ? "checked" : "";
  return `<tr class="selectable${selected} ${esc(extraClass)}" data-material-id="${esc(row.record_id)}" ${hidden ? "hidden" : ""}>
    <td class="select-col"><input class="material-check" type="checkbox" data-select-id="${esc(row.record_id)}" ${checked}></td>
    <td class="row-no">${esc(row["行号"] || "")}</td>
    <td class="video-cell">
      ${videoUrl ? `<a class="video-link" href="${esc(videoUrl)}" target="_blank">${esc(shortUrl(videoUrl))}</a>` : `<strong>${esc(shortUrl(videoUrl))}</strong>`}
      <div class="subline">${esc(row["商品名称"] || "未填写商品名称")}</div>
      <div class="muted">${esc(row.record_id)}</div>
    </td>
    <td>${renderScriptStep(row)}</td>
    <td>${renderImageStep(row)}</td>
    <td>${renderGroupStep(row)}</td>
    <td>${renderTaskStep(row)}</td>
    <td>${renderMaterialStatus(row)}</td>
  </tr>`;
}

function renderProcessedMaterialGroups(rows) {
  return groupRowsByDate(rows, materialDateGroup).map((group) => {
    const collapsed = state.collapsedMaterialDateGroups.has(group.key);
    const groupRows = collapsed ? "" : group.rows.map((row) => renderMaterialRow(row, "task-history-row")).join("");
    return `<tr class="task-date-row">
      <td colspan="8">
        <button class="date-toggle" type="button" data-material-date-toggle="${esc(group.key)}">
          <span>${esc(group.label)}</span>
          <strong>${group.rows.length} 条已处理</strong>
          <em>${collapsed ? "展开" : "收起"}</em>
        </button>
      </td>
    </tr>${groupRows}`;
  }).join("");
}

function isProcessedMaterial(row) {
  const automationStatus = String(row["自动化状态"] || "");
  const taskTotal = Number(row["任务数量"] || 0);
  const runningStatuses = new Set(["视频拆解中", "主图入库中", "组图生成中", "任务创建中"]);
  return !runningStatuses.has(automationStatus) && (automationStatus === "已创建任务" || taskTotal > 0);
}

function currentVisibleMaterialRows() {
  const activeRows = state.materials.filter((row) => !isProcessedMaterial(row));
  return pageRows(activeRows, state.materialPage, state.materialPageSize);
}

function currentVisibleTaskRows() {
  const activeRows = tasksForSelected().filter((row) => row["生成状态"] !== "已完成");
  return pageRows(activeRows, state.taskPage, state.taskPageSize);
}

function renderMaterials() {
  renderMetrics();
  if (!state.materials.length) {
    $("materials").innerHTML = `<div class="selected-box">暂无素材</div>`;
    return;
  }
  const activeRows = state.materials.filter((row) => !isProcessedMaterial(row));
  const processedRows = state.materials.filter((row) => isProcessedMaterial(row));
  state.materialPage = clampPage(state.materialPage, activeRows.length, state.materialPageSize);
  const visibleActiveRows = pageRows(activeRows, state.materialPage, state.materialPageSize);
  processedRows.forEach((row) => {
    const group = materialDateGroup(row);
    if (!state.seenMaterialDateGroups.has(group.key)) {
      state.seenMaterialDateGroups.add(group.key);
      state.collapsedMaterialDateGroups.add(group.key);
    }
  });
  const body = [
    renderProcessedMaterialGroups(processedRows),
    ...visibleActiveRows.map((row) => renderMaterialRow(row)),
  ].join("");

  $("materials").innerHTML = `${renderPagination("materials", activeRows.length)}<table><thead><tr>
    <th class="select-col"></th><th class="row-no">行</th><th>爆款视频</th><th>1 视频Prompt</th><th>2 主图入库</th><th>3 商品组图</th><th>4 视频任务</th><th>状态</th>
  </tr></thead><tbody>${body}</tbody></table>`;

  document.querySelectorAll("[data-material-date-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.materialDateToggle;
      if (state.collapsedMaterialDateGroups.has(key)) state.collapsedMaterialDateGroups.delete(key);
      else state.collapsedMaterialDateGroups.add(key);
      renderMaterials();
    });
  });
  document.querySelectorAll("[data-material-id]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, label, summary, .template-menu")) return;
      selectMaterial(row.dataset.materialId);
    });
  });
  document.querySelectorAll("[data-select-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedMaterialIds.add(checkbox.dataset.selectId);
      else state.selectedMaterialIds.delete(checkbox.dataset.selectId);
      renderSelectionState();
    });
  });
  document.querySelectorAll("[data-step]").forEach((button) => {
    button.addEventListener("click", () => runMaterialStep(button.dataset.id, button.dataset.step, button));
  });
  document.querySelectorAll("[data-action='retry-failed-material']").forEach((button) => {
    button.addEventListener("click", () => retryFailedMaterial(button.dataset.id, button));
  });
  document.querySelectorAll("[data-action='save-templates']").forEach((button) => {
    button.addEventListener("click", () => saveTemplates(button.dataset.id, button));
  });
  bindPagination($("materials"), "materials", renderMaterials);
  bindPromptDetails($("materials"));
  renderSelectionState();
}

function renderSelectedMaterial() {
  const row = state.materials.find((item) => item.record_id === state.selectedMaterialId);
  if (!row) {
    $("selectedMaterial").innerHTML = `未选择素材`;
    return;
  }
  $("selectedMaterial").innerHTML = `<strong>${esc(shortUrl(row["爆款视频链接"]))}</strong>
    <span>${esc(row["商品名称"] || "未填写商品名称")} · ${esc(row["地区"] || "墨西哥")} · ${esc(row["任务数量"] || 0)} 条视频任务</span>
    <button class="mini ghost" id="clearSelection" type="button">查看全部任务</button>`;
  $("clearSelection").onclick = () => selectMaterial("");
}

function renderTaskRow(row, extraClass = "", hidden = false) {
  const checked = state.selectedTaskIds.has(row.record_id) ? "checked" : "";
  const referenceCount = Number(row["首帧附件数"] || 0);
  const videoAttachmentCount = Number(row["生成视频附件数"] || 0);
  const referenceJob = `sync-reference-image:${row.record_id}`;
  const manualVideoJob = `store-manual-video:${row.record_id}`;
  const generateJob = `generate-video:${row.record_id}`;
  const referenceBusy = isJobBusy(referenceJob);
  const manualVideoBusy = isJobBusy(manualVideoJob);
  const generateBusy = isJobBusy(generateJob);
  return `<tr class="${esc(extraClass)}" ${hidden ? "hidden" : ""}>
    <td class="select-col"><input class="task-check" type="checkbox" data-task-select-id="${esc(row.record_id)}" ${checked}></td>
    <td class="row-no">${esc(row["行号"] || "")}</td>
    <td>
      <strong>${esc(row["生成任务名称"])}</strong>
      <div class="muted">#${esc(row["任务序号"] || "-")} · ${esc(row.record_id)}</div>
    </td>
    <td>${status(row["生成状态"])}</td>
    <td>${renderProgress(row["生成进度"])}</td>
    <td class="publish-cell">
      <strong>${esc(row["生成标题"] || "")}</strong>
      <div class="muted">${esc(row["关键词"] || "")}</div>
      ${row["生成文案"] ? `<details class="prompt-preview"><summary>正文</summary><p>${esc(row["生成文案"])}</p></details>` : ""}
    </td>
    <td>
      <div class="asset-cell">
        ${row["首帧/参考图链接"] ? renderThumbs([row["首帧/参考图链接"]]) : `<span class="muted">无</span>`}
        ${referenceCount ? status("已入表") : `<span class="muted">未入表</span>`}
        <button class="mini ghost${state.pendingJobs.has(referenceJob) ? " is-pending" : ""}" data-action="sync-reference" data-id="${esc(row.record_id)}" ${referenceBusy || !row["首帧/参考图链接"] ? "disabled" : ""}>${state.pendingJobs.has(referenceJob) ? "提交中" : referenceBusy ? "入表中" : referenceCount ? "检查入表" : "参考图入表"}</button>
      </div>
    </td>
    <td>
      <div class="asset-cell">
        ${row["生成视频链接"] ? `<a href="${esc(row["生成视频链接"])}" target="_blank">打开视频</a>` : renderInlineError(row["失败原因"], "待人工上传")}
        ${videoAttachmentCount ? status(`视频附件${videoAttachmentCount}`) : ""}
        <button class="mini ghost${state.pendingJobs.has(manualVideoJob) ? " is-pending" : ""}" data-action="manual-video" data-id="${esc(row.record_id)}" ${manualVideoBusy || (!videoAttachmentCount && !row["生成视频链接"]) ? "disabled" : ""}>${state.pendingJobs.has(manualVideoJob) ? "提交中" : manualVideoBusy ? "入库中" : "视频入库"}</button>
      </div>
    </td>
    <td><button class="mini${state.pendingJobs.has(generateJob) ? " is-pending" : ""}" data-action="video" data-id="${esc(row.record_id)}" ${generateBusy ? "disabled" : ""}>${state.pendingJobs.has(generateJob) ? "提交中" : generateBusy ? "进行中" : "生成视频"}</button></td>
  </tr>`;
}

function renderCompletedTaskGroups(rows) {
  return groupRowsByDate(rows, taskDateGroup).map((group) => {
    const collapsed = state.collapsedTaskDateGroups.has(group.key);
    const groupRows = collapsed ? "" : group.rows.map((row) => renderTaskRow(row, "task-history-row")).join("");
    return `<tr class="task-date-row">
      <td colspan="9">
        <button class="date-toggle" type="button" data-task-date-toggle="${esc(group.key)}">
          <span>${esc(group.label)}</span>
          <strong>${group.rows.length} 条已完成</strong>
          <em>${collapsed ? "展开" : "收起"}</em>
        </button>
      </td>
    </tr>${groupRows}`;
  }).join("");
}

function renderTasks() {
  const rows = tasksForSelected();
  renderSelectedMaterial();
  if (!rows.length) {
    $("tasks").innerHTML = `<div class="selected-box">暂无视频生成任务</div>`;
    renderTaskSelectionState();
    return;
  }
  const activeRows = rows.filter((row) => row["生成状态"] !== "已完成");
  const completedRows = rows.filter((row) => row["生成状态"] === "已完成");
  state.taskPage = clampPage(state.taskPage, activeRows.length, state.taskPageSize);
  const visibleActiveRows = pageRows(activeRows, state.taskPage, state.taskPageSize);
  completedRows.forEach((row) => {
    const group = taskDateGroup(row);
    if (!state.seenTaskDateGroups.has(group.key)) {
      state.seenTaskDateGroups.add(group.key);
      state.collapsedTaskDateGroups.add(group.key);
    }
  });
  const body = [
    renderCompletedTaskGroups(completedRows),
    ...visibleActiveRows.map((row) => renderTaskRow(row)),
  ].join("");

  $("tasks").innerHTML = `${renderPagination("tasks", activeRows.length)}<table><thead><tr>
    <th class="select-col"></th><th class="row-no">行</th><th>任务</th><th>状态</th><th>进度</th><th>发布内容</th><th>参考图</th><th>视频</th><th>操作</th>
  </tr></thead><tbody>${body}</tbody></table>`;

  document.querySelectorAll("[data-task-date-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.taskDateToggle;
      if (state.collapsedTaskDateGroups.has(key)) state.collapsedTaskDateGroups.delete(key);
      else state.collapsedTaskDateGroups.add(key);
      renderTasks();
    });
  });
  document.querySelectorAll("[data-task-select-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedTaskIds.add(checkbox.dataset.taskSelectId);
      else state.selectedTaskIds.delete(checkbox.dataset.taskSelectId);
      renderTaskSelectionState();
    });
  });
  document.querySelectorAll("[data-action='video']").forEach((button) => {
    button.addEventListener("click", () => generateVideo(button.dataset.id, button));
  });
  document.querySelectorAll("[data-action='sync-reference']").forEach((button) => {
    button.addEventListener("click", () => syncReferenceImage(button.dataset.id, button));
  });
  document.querySelectorAll("[data-action='manual-video']").forEach((button) => {
    button.addEventListener("click", () => storeManualVideo(button.dataset.id, button));
  });
  bindPagination($("tasks"), "tasks", renderTasks);
  bindPromptDetails($("tasks"));
  renderTaskSelectionState();
}

function renderDoubaoMetrics() {
  $("metricDoubaoRows").textContent = state.doubaoItems.length;
  $("metricDoubaoImages").textContent = state.doubaoItems.filter((row) => hasDoubaoImage(row)).length;
  $("metricDoubaoStored").textContent = state.doubaoItems.filter((row) => hasDoubaoStoredImage(row)).length;
  $("metricDoubaoPrompts").textContent = state.doubaoItems.filter((row) => String(row["最终视频生成提示词"] || "").trim()).length;
}

function hasDoubaoImage(row) {
  return Number(row["商品图数量"] || 0) > 0 || String(row["商品图链接"] || "").startsWith("http");
}

function hasDoubaoStoredImage(row) {
  return String(row["商品图存储链接"] || "").startsWith("http");
}

function doubaoVideoUrl(row) {
  return String(row["视频生成链接"] || row["Dola视频链接"] || "").trim();
}

function isCompletedDoubao(row) {
  return String(row["Dola生成状态"] || "").trim() === "已完成" || doubaoVideoUrl(row).startsWith("http");
}

function renderDoubaoRow(row, extraClass = "") {
  const prompt = String(row["最终视频生成提示词"] || "").trim();
  const promptName = `doubao-generate-prompt:${row.record_id}`;
  const storeName = `doubao-store-image:${row.record_id}`;
  const checked = state.selectedDoubaoIds.has(row.record_id) ? "checked" : "";
  const promptPending = state.pendingJobs.has(promptName);
  const storePending = state.pendingJobs.has(storeName);
  const promptRunning = isJobBusy(promptName);
  const storeRunning = isJobBusy(storeName);
  const videoUrl = doubaoVideoUrl(row);
  return `<tr class="${esc(extraClass)}">
    <td class="select-col"><input class="doubao-check" type="checkbox" aria-label="选择第 ${esc(row["行号"] || "")} 行" data-doubao-select-id="${esc(row.record_id)}" ${checked}></td>
    <td class="row-no">${esc(row["行号"] || "")}</td>
    <td>
      <strong>${esc(row["商品名称"] || "未填写商品名称")}</strong>
      <div class="muted">${esc(row.record_id || "")}</div>
      ${row["商品链接"] ? `<a href="${esc(row["商品链接"])}" target="_blank">商品链接</a>` : ""}
    </td>
    <td>
      ${hasDoubaoImage(row) ? status(row["图片来源"] === "链接" ? "图片链接" : `已上传${row["商品图数量"]}张`) : status("缺少商品图")}
      <div class="muted">${esc(row["商品图文件名"] || "")}</div>
      ${row["商品图链接"] ? `<a href="${esc(row["商品图链接"])}" target="_blank">打开图片链接</a>` : ""}
    </td>
    <td>
      ${hasDoubaoStoredImage(row) ? `<a href="${esc(row["商品图存储链接"])}" target="_blank">对象存储</a>` : `<span class="muted">待入库</span>`}
      <button class="mini ghost${storePending ? " is-pending" : ""}" data-action="doubao-store" data-id="${esc(row.record_id)}" ${storeRunning || !hasDoubaoImage(row) ? "disabled" : ""}>${storePending ? "提交中" : storeRunning ? "入库中" : hasDoubaoStoredImage(row) ? "重新入库" : "图片入库"}</button>
    </td>
    <td>${prompt ? renderPromptDetail("查看Prompt", prompt) : `<span class="muted">未生成</span>`}</td>
    <td class="publish-cell">
      <strong>${esc(row["标题"] || "")}</strong>
      <div class="muted">${esc(row["标签"] || "")}</div>
      ${row["文案"] ? `<details class="prompt-preview"><summary>文案</summary><p>${esc(row["文案"])}</p></details>` : ""}
    </td>
    <td>${videoUrl ? `<a href="${esc(videoUrl)}" target="_blank">打开视频</a>` : `<span class="muted">未填写</span>`}</td>
    <td><button class="mini${promptPending ? " is-pending" : ""}" data-action="doubao-generate" data-id="${esc(row.record_id)}" ${promptRunning || !hasDoubaoImage(row) ? "disabled" : ""}>${promptPending ? "提交中" : promptRunning ? "进行中" : prompt ? "重新生成" : "生成提示词"}</button></td>
  </tr>`;
}

function renderCompletedDoubaoGroups(rows) {
  return groupRowsByDate(rows, doubaoDateGroup).map((group) => {
    const collapsed = state.collapsedDoubaoDateGroups.has(group.key);
    const groupRows = collapsed ? "" : group.rows.map((row) => renderDoubaoRow(row, "task-history-row")).join("");
    return `<tr class="task-date-row">
      <td colspan="9">
        <button class="date-toggle" type="button" data-doubao-date-toggle="${esc(group.key)}">
          <span>${esc(group.label)}</span>
          <strong>${group.rows.length} 条已完成</strong>
          <em>${collapsed ? "展开" : "收起"}</em>
        </button>
      </td>
    </tr>${groupRows}`;
  }).join("");
}

function renderDoubaoItems() {
  renderDoubaoMetrics();
  if (!state.doubaoItems.length) {
    $("doubaoItems").innerHTML = `<div class="selected-box">暂无豆包视频制作记录</div>`;
    renderDoubaoSelectionState();
    return;
  }
  const activeRows = state.doubaoItems.filter((row) => !isCompletedDoubao(row));
  const completedRows = state.doubaoItems.filter((row) => isCompletedDoubao(row));
  state.doubaoPage = clampPage(state.doubaoPage, activeRows.length, state.doubaoPageSize);
  const visibleActiveRows = pageRows(activeRows, state.doubaoPage, state.doubaoPageSize);
  completedRows.forEach((row) => {
    const group = doubaoDateGroup(row);
    if (!state.seenDoubaoDateGroups.has(group.key)) {
      state.seenDoubaoDateGroups.add(group.key);
      state.collapsedDoubaoDateGroups.add(group.key);
    }
  });
  const body = [
    renderCompletedDoubaoGroups(completedRows),
    ...visibleActiveRows.map((row) => renderDoubaoRow(row)),
  ].join("");
  $("doubaoItems").innerHTML = `${renderPagination("doubao", activeRows.length)}<table><thead><tr>
    <th class="select-col">选</th><th class="row-no">行</th><th>商品</th><th>图片</th><th>对象存储</th><th>最终视频生成提示词</th><th>发布内容</th><th>视频链接</th><th>操作</th>
  </tr></thead><tbody>${body}</tbody></table>`;

  document.querySelectorAll("[data-doubao-date-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.doubaoDateToggle;
      if (state.collapsedDoubaoDateGroups.has(key)) state.collapsedDoubaoDateGroups.delete(key);
      else state.collapsedDoubaoDateGroups.add(key);
      renderDoubaoItems();
    });
  });
  document.querySelectorAll("[data-doubao-select-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedDoubaoIds.add(checkbox.dataset.doubaoSelectId);
      else state.selectedDoubaoIds.delete(checkbox.dataset.doubaoSelectId);
      renderDoubaoSelectionState();
    });
  });
  document.querySelectorAll("[data-action='doubao-store']").forEach((button) => {
    button.addEventListener("click", () => storeDoubaoImage(button.dataset.id, button));
  });
  document.querySelectorAll("[data-action='doubao-generate']").forEach((button) => {
    button.addEventListener("click", () => generateDoubaoPrompt(button.dataset.id, button));
  });
  bindPagination($("doubaoItems"), "doubao", renderDoubaoItems);
  bindPromptDetails($("doubaoItems"));
  renderDoubaoSelectionState();
}

function renderPrompts() {
  renderPromptEditor();
  if (!state.prompts.length) {
    $("prompts").innerHTML = `<div class="selected-box">暂无 Prompt 配置</div>`;
    return;
  }
  $("prompts").innerHTML = `<table><thead><tr>
    <th>Prompt</th><th>类型</th><th>Key</th><th>版本</th><th>状态</th><th>地区</th><th>模板内容</th><th>操作</th>
  </tr></thead><tbody>${state.prompts.map((row) => {
    const content = String(row["模板内容"] || "").trim();
    return `<tr>
      <td><strong>${esc(row["Prompt名称"] || "")}</strong><div class="muted">${esc(row.record_id || "")}</div></td>
      <td>${esc(row["Prompt类型"] || "")}</td>
      <td><code>${esc(row["模板Key"] || "")}</code></td>
      <td>${esc(row["版本"] || "")}</td>
      <td>${status(row["是否启用"] || "未设置")}</td>
      <td>${esc(row["目标地区"] || "")}</td>
      <td>${renderPromptDetail("查看模板", content)}<div class="muted">${esc(row["变量说明"] || "")}</div></td>
      <td><button class="mini" data-action="edit-prompt" data-id="${esc(row.record_id)}">编辑</button></td>
    </tr>`;
  }).join("")}</tbody></table>`;

  document.querySelectorAll("[data-action='edit-prompt']").forEach((button) => {
    button.addEventListener("click", () => {
      state.editingPromptId = button.dataset.id;
      renderPromptEditor();
      $("promptEditor").scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });
  bindPromptDetails($("prompts"));
}

function renderPromptEditor() {
  const box = $("promptEditor");
  const row = state.prompts.find((item) => item.record_id === state.editingPromptId);
  if (!row) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = `<div class="editor-head">
    <div>
      <strong>编辑 Prompt</strong>
      <span>${esc(row["模板Key"] || "")} · ${esc(row["Prompt类型"] || "")}</span>
    </div>
    <button id="closePromptEditor" class="mini secondary" type="button">收起</button>
  </div>
  <div class="editor-grid">
    <label>Prompt名称<input id="editPromptName" type="text" value="${esc(row["Prompt名称"] || "")}"></label>
    <label>版本<input id="editPromptVersion" type="text" value="${esc(row["版本"] || "")}"></label>
    <label>状态<select id="editPromptEnabled">
      ${["启用", "停用"].map((item) => `<option value="${item}" ${row["是否启用"] === item ? "selected" : ""}>${item}</option>`).join("")}
    </select></label>
    <label>地区<select id="editPromptRegion">
      ${["通用", "墨西哥", "马来"].map((item) => `<option value="${item}" ${row["目标地区"] === item ? "selected" : ""}>${item}</option>`).join("")}
    </select></label>
  </div>
  <label class="editor-block">模板内容<textarea id="editPromptContent" rows="18">${esc(row["模板内容"] || "")}</textarea></label>
  <label class="editor-block">变量说明<textarea id="editPromptVars" rows="3">${esc(row["变量说明"] || "")}</textarea></label>
  <label class="editor-block">备注<textarea id="editPromptNote" rows="3">${esc(row["备注"] || "")}</textarea></label>
  <div class="editor-actions">
    <button id="savePromptEdit" type="button">保存到飞书</button>
    <button id="cancelPromptEdit" class="secondary" type="button">取消</button>
  </div>`;
  $("closePromptEditor").onclick = closePromptEditor;
  $("cancelPromptEdit").onclick = closePromptEditor;
  $("savePromptEdit").onclick = savePromptEdit;
}

function selectMaterial(id) {
  state.selectedMaterialId = id || "";
  state.taskPage = 1;
  state.selectedTaskIds.clear();
  localStorage.setItem("viral-remix-selected-material", state.selectedMaterialId);
  renderMaterials();
  renderTasks();
}

async function loadMaterials(options = {}) {
  const data = await api(tablePath("/api/materials?limit=500", options));
  state.materials = data.data;
  if (state.selectedMaterialId && !state.materials.some((row) => row.record_id === state.selectedMaterialId)) {
    state.selectedMaterialId = "";
  }
  const existingIds = new Set(state.materials.map((row) => row.record_id));
  state.selectedMaterialIds = new Set([...state.selectedMaterialIds].filter((id) => existingIds.has(id)));
  renderMaterials();
  renderTasks();
  markTableLoaded("materials", "素材表", state.materials.length);
  markPageLoaded("materials");
}

async function loadTasks(options = {}) {
  const data = await api(tablePath("/api/tasks?limit=500", options));
  state.tasks = data.data;
  const existingIds = new Set(state.tasks.map((row) => row.record_id));
  state.selectedTaskIds = new Set([...state.selectedTaskIds].filter((id) => existingIds.has(id)));
  renderTasks();
  markTableLoaded("tasks", "任务表", state.tasks.length);
  markPageLoaded("videos");
}

async function loadDoubaoItems(options = {}) {
  const rows = [];
  const seenPageTokens = new Set();
  let pageToken = "";
  while (true) {
    const params = new URLSearchParams({ page_size: "100", offset: String(rows.length) });
    if (pageToken) params.set("page_token", pageToken);
    if (options.force) params.set("force", "1");
    const data = await api(`/api/doubao-items/page?${params.toString()}`);
    rows.push(...data.data);
    if (!data.has_more) break;
    const nextPageToken = String(data.page_token || "");
    if (!nextPageToken || seenPageTokens.has(nextPageToken)) {
      throw new Error("飞书分页游标异常，豆包表读取未完成");
    }
    seenPageTokens.add(nextPageToken);
    pageToken = nextPageToken;
  }
  state.doubaoItems = rows;
  const existingIds = new Set(state.doubaoItems.map((row) => row.record_id));
  state.selectedDoubaoIds = new Set([...state.selectedDoubaoIds].filter((id) => existingIds.has(id)));
  renderDoubaoItems();
  markTableLoaded("doubao", "豆包表", state.doubaoItems.length);
  markPageLoaded("doubao");
}

async function loadPrompts(options = {}) {
  const data = await api(tablePath("/api/prompts", options));
  state.prompts = data.data;
  if (state.editingPromptId && !state.prompts.some((row) => row.record_id === state.editingPromptId)) {
    state.editingPromptId = "";
  }
  renderPrompts();
  markTableLoaded("prompts", "Prompt表", state.prompts.length);
  markPageLoaded("prompts");
}

function closePromptEditor() {
  state.editingPromptId = "";
  renderPromptEditor();
}

async function savePromptEdit() {
  if (!state.editingPromptId) return;
  const button = $("savePromptEdit");
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "保存中";
  try {
    await api(`/api/prompts/${state.editingPromptId}`, {
      method: "PATCH",
      body: JSON.stringify({
        "Prompt名称": $("editPromptName").value,
        "版本": $("editPromptVersion").value,
        "是否启用": $("editPromptEnabled").value,
        "目标地区": $("editPromptRegion").value,
        "模板内容": $("editPromptContent").value,
        "变量说明": $("editPromptVars").value,
        "备注": $("editPromptNote").value,
      }),
    });
    await loadPrompts();
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function loadJobs() {
  const data = await api("/api/jobs");
  const nextActiveNames = data.data
    .filter((job) => job.status === "running")
    .map((job) => `${job.name}:${job.id}`)
    .join("|");
  const nextSnapshot = data.data.map((job) => `${job.id}:${job.name}:${job.status}:${job.error || ""}`).join("|");
  const activeChanged = nextActiveNames !== state.lastActiveJobNames;
  state.jobs = data.data;
  state.lastActiveJobNames = nextActiveNames;
  if (nextSnapshot !== state.lastJobsSnapshot) {
    state.lastJobsSnapshot = nextSnapshot;
    $("jobs").innerHTML = data.data.slice().reverse().map((job) => `<div class="job">
      <div><strong>${esc(job.name)}</strong><div class="muted">${esc(job.id)}</div></div>
      <div>${status(job.status)}</div>
    </div>`).join("");
  }
  if (!activeChanged) return;
  if (state.activePage === "materials" && activePageLoaded("materials")) renderMaterials();
  if (state.activePage === "videos" && activePageLoaded("videos")) renderTasks();
  if (state.activePage === "doubao" && activePageLoaded("doubao")) renderDoubaoItems();
}

async function loadLogs() {
  const data = await api("/api/logs?limit=120");
  const box = $("logs");
  const nextText = data.data.map((l) => `[${l.timestamp}] [${l.level}] [${l.source}] ${l.message}`).join("\n");
  if (nextText === state.lastLogsSnapshot) return;
  const shouldStick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  state.lastLogsSnapshot = nextText;
  box.textContent = nextText;
  if (shouldStick) box.scrollTop = box.scrollHeight;
}

async function prepareMaterial(id) {
  const data = await api(`/api/materials/${id}/prepare`, { method: "POST", body: "{}" });
  alert(`已启动素材处理：${data.job_id}`);
  await loadJobs();
}

async function runMaterialStep(id, step, button = null) {
  const labels = {
    "analyze-video": "视频拆解",
    "store-image": "主图入库",
    "generate-images": "商品组图",
    "create-tasks": "创建任务",
  };
  const name = `${step}:${id}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderMaterials();
  try {
    const data = await api(`/api/materials/${id}/${step}`, { method: "POST", body: "{}" });
    alert(`已启动${labels[step] || step}：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderMaterials();
  }
}

async function retryFailedMaterial(id, button = null) {
  const name = `retry-failed-step:${id}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderMaterials();
  try {
    const data = await api(`/api/materials/${id}/retry-failed-step`, { method: "POST", body: "{}" });
    alert(`已启动失败步骤重试：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderMaterials();
  }
}

async function runBatchMaterialStep(step, button = null) {
  const ids = [...state.selectedMaterialIds];
  if (!ids.length) {
    alert("请先勾选要处理的素材");
    return;
  }
  const labels = {
    "analyze-video": "批量生成Prompt",
    "store-image": "批量主图入库",
    "generate-images": "批量生成组图",
    "create-tasks": "批量创建任务",
  };
  const name = `batch-${step}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  try {
    const data = await api("/api/materials/batch-step", {
      method: "POST",
      body: JSON.stringify({ step, record_ids: ids, limit: ids.length }),
    });
    alert(`${labels[step] || step}已启动：${data.job_id}，将按顺序处理 ${ids.length} 条素材`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
  }
}

async function generateVideo(id, button = null) {
  const name = `generate-video:${id}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderTasks();
  try {
    const data = await api(`/api/tasks/${id}/generate-video`, { method: "POST", body: "{}" });
    alert(`已启动视频生成：${data.job_id}`);
    await loadJobs();
    await loadTasks();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderTasks();
  }
}

async function syncReferenceImage(id, button = null) {
  const name = `sync-reference-image:${id}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderTasks();
  try {
    const data = await api(`/api/tasks/${id}/sync-reference-image`, { method: "POST", body: "{}" });
    alert(`已启动参考图入表：${data.job_id}`);
    await loadJobs();
    await loadTasks();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderTasks();
  }
}

async function storeManualVideo(id, button = null) {
  const name = `store-manual-video:${id}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderTasks();
  try {
    const data = await api(`/api/tasks/${id}/store-manual-video`, { method: "POST", body: "{}" });
    alert(`已启动人工视频入库：${data.job_id}`);
    await loadJobs();
    await loadTasks();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderTasks();
  }
}

async function generateDoubaoPrompt(id, button = null) {
  const name = `doubao-generate-prompt:${id}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderDoubaoItems();
  try {
    const data = await api(`/api/doubao-items/${id}/generate-prompt`, { method: "POST", body: "{}" });
    alert(`已启动豆包视频提示词生成：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderDoubaoItems();
  }
}

async function storeDoubaoImage(id, button = null) {
  const name = `doubao-store-image:${id}`;
  if (isJobBusy(name)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderDoubaoItems();
  try {
    const data = await api(`/api/doubao-items/${id}/store-image`, { method: "POST", body: "{}" });
    alert(`已启动豆包商品图入库：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderDoubaoItems();
  }
}

async function batchDoubaoStoreImages(button = null) {
  const name = "doubao-batch-store-images";
  if (isJobBusy(name)) return;
  const selectedRows = selectedDoubaoRows();
  if (!selectedRows.length) {
    alert("请先勾选要图片入库的豆包记录");
    return;
  }
  const rows = selectedRows.filter((row) => hasDoubaoImage(row) && !hasDoubaoStoredImage(row));
  if (!rows.length) {
    alert("已勾选记录里没有可入库的商品图");
    return;
  }
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  try {
    const data = await api("/api/doubao-items/batch-store-images", {
      method: "POST",
      body: JSON.stringify({ record_ids: rows.map((row) => row.record_id), limit: 50 }),
    });
    alert(`已启动豆包商品图批量入库：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
  }
}

async function batchDoubaoPrompts(button = null) {
  const name = "doubao-batch-prompts";
  if (isJobBusy(name)) return;
  const selectedRows = selectedDoubaoRows();
  if (!selectedRows.length) {
    alert("请先勾选要生成提示词的豆包记录");
    return;
  }
  const rows = selectedRows.filter((row) => hasDoubaoImage(row) && !isJobBusy(`doubao-generate-prompt:${row.record_id}`));
  if (!rows.length) {
    alert("已勾选记录里没有可生成提示词的商品图");
    return;
  }
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  try {
    const jobIds = [];
    for (const row of rows) {
      const rowJobName = `doubao-generate-prompt:${row.record_id}`;
      setPendingJob(rowJobName, true);
      renderDoubaoItems();
      try {
        const data = await api(`/api/doubao-items/${row.record_id}/generate-prompt`, { method: "POST", body: "{}" });
        jobIds.push(data.job_id);
      } finally {
        setPendingJob(rowJobName, false);
      }
    }
    alert(`已启动豆包视频提示词生成：${jobIds.length} 个任务`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
    renderDoubaoItems();
  }
}

function connectLocalWorker() {
  const url = "http://127.0.0.1:5177/";
  const statusBox = $("workerStatus");
  const withCloud = `${url}?cloud=${encodeURIComponent(window.location.origin)}`;
  window.open(withCloud, "_blank", "noopener");
  if (statusBox) {
    statusBox.textContent = "已尝试打开本机Worker。如果新页面无法访问，请先运行 Dola本地Worker.command。";
  }
}

async function updateLocalWorker(button = null) {
  const statusBox = $("workerStatus");
  const oldText = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "检查中";
    button.classList.add("is-pending");
  }
  if (statusBox) statusBox.textContent = "正在连接本机 Worker 并检查更新...";
  try {
    const status = await localWorkerApi("/api/worker/update-status?force=1");
    if (!status.updateAvailable) {
      if (statusBox) statusBox.textContent = `本机 Worker 已是最新版：v${status.currentVersion || "未知"}`;
      return;
    }
    if (!status.canApply) {
      const reason = status.missing?.url
        ? "更新包下载地址缺失"
        : status.missing?.sha256
          ? "更新包校验值缺失"
          : "当前版本不能自动更新";
      if (statusBox) statusBox.textContent = `发现新版 v${status.version}，但无法一键更新：${reason}`;
      alert(`发现新版 v${status.version}，但无法一键更新：${reason}`);
      return;
    }
    const ok = confirm(`发现本地 Worker 新版 v${status.version}。更新会关闭当前本地 Worker 和已打开的 Dola 环境，确认现在更新吗？`);
    if (!ok) {
      if (statusBox) statusBox.textContent = `发现新版 v${status.version}，已取消更新。`;
      return;
    }
    if (button) button.textContent = "更新中";
    if (statusBox) statusBox.textContent = `正在下载并安装本地 Worker v${status.version}，完成后会自动重启。`;
    const result = await localWorkerApi("/api/worker/apply-update", { method: "POST", body: "{}" });
    if (statusBox) statusBox.textContent = result.message || "本地 Worker 已开始更新，请稍后重新打开 127.0.0.1:5177。";
  } catch (error) {
    const message = String(error.message || error);
    if (/Failed to fetch|NetworkError|Load failed/i.test(message)) {
      if (statusBox) statusBox.textContent = "无法连接本机 Worker。请先启动 Dola本地Worker.command，再点更新。";
    } else if (statusBox) {
      statusBox.textContent = `本地 Worker 更新失败：${message}`;
    }
    alert(statusBox?.textContent || message);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText || "更新本地Worker";
      button.classList.remove("is-pending");
    }
  }
}

async function syncPublishTasks(button = null) {
  const name = "sync-publish-tasks";
  if (isJobBusy(name)) return;
  const selectedRows = selectedVisibleTaskRows();
  const sourceRows = selectedRows.length ? selectedRows : tasksForSelected();
  const rows = sourceRows.filter((row) => String(row["生成视频链接"] || "").startsWith("http"));
  if ((selectedRows.length || state.selectedMaterialId) && !rows.length) {
    alert("当前选择里没有可同步的视频链接");
    return;
  }
  const body = selectedRows.length || state.selectedMaterialId
    ? { record_ids: rows.map((row) => row.record_id), limit: 100 }
    : { limit: 100 };
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  try {
    const data = await api("/api/tasks/sync-publish", { method: "POST", body: JSON.stringify(body) });
    alert(`已启动发布表同步：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
  }
}

async function batchSyncReferenceImages(button = null) {
  const name = "batch-sync-reference-images";
  if (isJobBusy(name)) return;
  const selectedRows = selectedVisibleTaskRows();
  const sourceRows = selectedRows.length ? selectedRows : tasksForSelected();
  const rows = sourceRows.filter((row) => String(row["首帧/参考图链接"] || "").startsWith("http") && !Number(row["首帧附件数"] || 0));
  if (!rows.length) {
    alert("当前选择里没有需要入表的参考图");
    return;
  }
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  try {
    const data = await api("/api/tasks/batch-sync-reference-images", {
      method: "POST",
      body: JSON.stringify({ record_ids: rows.map((row) => row.record_id), limit: rows.length }),
    });
    alert(`已启动批量参考图入表：${data.job_id}，将处理 ${rows.length} 条任务`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
  }
}

async function batchStoreManualVideos(button = null) {
  const name = "batch-store-manual-videos";
  if (isJobBusy(name)) return;
  const selectedRows = selectedVisibleTaskRows();
  const sourceRows = selectedRows.length ? selectedRows : tasksForSelected();
  const rows = sourceRows.filter((row) => Number(row["生成视频附件数"] || 0) || String(row["生成视频链接"] || "").startsWith("http"));
  if (!rows.length) {
    alert("当前选择里没有可入库的视频附件或视频链接");
    return;
  }
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  try {
    const data = await api("/api/tasks/batch-store-manual-videos", {
      method: "POST",
      body: JSON.stringify({ record_ids: rows.map((row) => row.record_id), limit: rows.length }),
    });
    alert(`已启动批量视频入库：${data.job_id}，将处理 ${rows.length} 条任务`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
  }
}

async function saveTemplates(id, button) {
  const menu = [...document.querySelectorAll("[data-template-id]")].find((item) => item.dataset.templateId === id);
  if (!menu) return;
  const checked = menu.querySelector("input[type='radio']:checked");
  const styles = checked ? [checked.value] : [];
  const oldText = button.textContent;
  button.textContent = "保存中";
  button.disabled = true;
  try {
    await api(`/api/materials/${id}/templates`, {
      method: "POST",
      body: JSON.stringify({ styles }),
    });
    await loadMaterials();
  } finally {
    button.textContent = oldText;
    button.disabled = false;
  }
}

$("adminToken").value = state.token;
$("saveToken").onclick = () => {
  state.token = $("adminToken").value.trim();
  localStorage.setItem("viral-remix-admin-token", state.token);
  loadConfig();
  loadActivePage({ force: true }).catch(() => {});
};

$("refreshConfig").onclick = loadConfig;
$("loadMaterials").onclick = () => loadMaterials({ force: true });
$("loadTasks").onclick = () => loadTasks({ force: true });
$("syncPublish").onclick = () => syncPublishTasks($("syncPublish"));
$("batchSyncReferenceImages").onclick = () => batchSyncReferenceImages($("batchSyncReferenceImages"));
$("batchStoreManualVideos").onclick = () => batchStoreManualVideos($("batchStoreManualVideos"));
$("loadJobs").onclick = loadJobs;
$("loadLogs").onclick = loadLogs;
$("refreshMaterialsTable").onclick = () => refreshTable("refreshMaterialsTable", () => loadMaterials({ force: true }));
$("refreshTasksTable").onclick = () => refreshTable("refreshTasksTable", () => loadTasks({ force: true }));
$("refreshDoubaoTable").onclick = () => refreshTable("refreshDoubaoTable", () => loadDoubaoItems({ force: true }));
$("refreshPromptsTable").onclick = () => refreshTable("refreshPromptsTable", () => loadPrompts({ force: true }));

async function loadActivePage(options = {}) {
  if (state.activePage === "materials") return loadMaterials(options);
  if (state.activePage === "videos") return loadTasks(options);
  if (state.activePage === "doubao") return loadDoubaoItems(options);
  if (state.activePage === "prompts") return loadPrompts(options);
}

function switchPage(page) {
  state.activePage = page;
  renderPage();
  if (!activePageLoaded(page)) loadActivePage().catch(() => {});
}

$("pageMaterials").onclick = () => switchPage("materials");
$("pageVideos").onclick = () => switchPage("videos");
$("pageDoubao").onclick = () => switchPage("doubao");
$("pagePrompts").onclick = () => switchPage("prompts");
$("loadDoubao").onclick = () => loadDoubaoItems({ force: true });
$("loadPrompts").onclick = () => loadPrompts({ force: true });
$("connectLocalWorker").onclick = connectLocalWorker;
$("openLocalWorker").onclick = connectLocalWorker;
$("updateLocalWorker").onclick = () => updateLocalWorker($("updateLocalWorker"));
$("selectAllMaterials").onchange = () => {
  const ids = currentVisibleMaterialRows().map((row) => row.record_id).filter(Boolean);
  if ($("selectAllMaterials").checked) ids.forEach((id) => state.selectedMaterialIds.add(id));
  else ids.forEach((id) => state.selectedMaterialIds.delete(id));
  renderMaterials();
};
$("selectRange").onclick = selectMaterialRange;
$("clearMaterialSelection").onclick = clearMaterialSelection;
["rangeStart", "rangeEnd"].forEach((id) => {
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter") selectMaterialRange();
  });
});
$("selectAllTasks").onchange = () => {
  const ids = currentVisibleTaskRows().map((row) => row.record_id).filter(Boolean);
  if ($("selectAllTasks").checked) ids.forEach((id) => state.selectedTaskIds.add(id));
  else ids.forEach((id) => state.selectedTaskIds.delete(id));
  renderTasks();
};
$("selectTaskRange").onclick = selectTaskRange;
$("clearTaskSelection").onclick = clearTaskSelection;
["taskRangeStart", "taskRangeEnd"].forEach((id) => {
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter") selectTaskRange();
  });
});
$("selectAllDoubao").onchange = () => {
  const ids = currentVisibleDoubaoRows().map((row) => row.record_id).filter(Boolean);
  if ($("selectAllDoubao").checked) ids.forEach((id) => state.selectedDoubaoIds.add(id));
  else ids.forEach((id) => state.selectedDoubaoIds.delete(id));
  renderDoubaoItems();
};
$("selectDoubaoRange").onclick = selectDoubaoRange;
$("clearDoubaoSelection").onclick = clearDoubaoSelection;
["doubaoRangeStart", "doubaoRangeEnd"].forEach((id) => {
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter") selectDoubaoRange();
  });
});
$("batchAnalyze").onclick = () => runBatchMaterialStep("analyze-video", $("batchAnalyze"));
$("batchStoreImage").onclick = () => runBatchMaterialStep("store-image", $("batchStoreImage"));
$("batchGenerateImages").onclick = () => runBatchMaterialStep("generate-images", $("batchGenerateImages"));
$("batchCreateTasks").onclick = () => runBatchMaterialStep("create-tasks", $("batchCreateTasks"));
$("batchDoubaoStoreImages").onclick = () => batchDoubaoStoreImages($("batchDoubaoStoreImages"));
$("batchDoubaoPrompts").onclick = () => batchDoubaoPrompts($("batchDoubaoPrompts"));

$("batchVideos").onclick = async () => {
  const name = "batch-generate-videos";
  if (isJobBusy(name)) return;
  const button = $("batchVideos");
  const oldText = button.textContent;
  const selectedRows = selectedVisibleTaskRows();
  const sourceRows = selectedRows.length ? selectedRows : tasksForSelected();
  const rows = sourceRows.filter((row) => ["待生成视频", "内容已生成", ""].includes(row["生成状态"] || ""));
  if (!rows.length) {
    alert("当前选择里没有可生成的视频任务");
    return;
  }
  const body = selectedRows.length
    ? { record_ids: rows.map((row) => row.record_id), limit: rows.length }
    : state.selectedMaterialId
      ? { record_ids: rows.map((row) => row.record_id), limit: 20 }
      : { limit: 3 };
  setPendingJob(name, true);
  button.disabled = true;
  button.textContent = "提交中";
  button.classList.add("is-pending");
  try {
    const data = await api("/api/tasks/batch-generate-video", { method: "POST", body: JSON.stringify(body) });
    alert(`已启动批量视频生成：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    button.disabled = false;
    button.textContent = oldText;
    button.classList.remove("is-pending");
  }
};

renderPage();
loadConfig();
loadActivePage().catch(() => {});
loadJobs().catch(() => {});
loadLogs().catch(() => {});
async function pollRuntime() {
  if (state.pollBusy || document.visibilityState === "hidden") return;
  state.pollBusy = true;
  try {
    state.pollTick += 1;
    await loadJobs();
    if (hasRunningJobs() || state.pollTick % 3 === 0) await loadLogs();
    if (hasRunningJobs() && state.pollTick % 2 === 0) {
      if (state.activePage === "materials" && activePageLoaded("materials")) await loadMaterials();
      if (state.activePage === "videos" && activePageLoaded("videos")) await loadTasks();
      if (state.activePage === "doubao" && activePageLoaded("doubao")) await loadDoubaoItems();
    }
  } finally {
    state.pollBusy = false;
  }
}
setInterval(() => {
  pollRuntime().catch(() => {
    state.pollBusy = false;
  });
}, 10000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") pollRuntime().catch(() => {
    state.pollBusy = false;
  });
});
