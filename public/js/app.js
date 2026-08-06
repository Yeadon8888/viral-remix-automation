const state = {
  token: localStorage.getItem("viral-remix-admin-token") || "",
  materials: [],
  tasks: [],
  doubaoItems: [],
  partTimeItems: [],
  prompts: [],
  doubaoPromptOptions: [],
  countries: [],
  jobs: [],
  pendingJobs: new Set(),
  submittedJobIds: new Set(),
  stalePages: new Set(),
  collapsedMaterialDateGroups: new Set(),
  seenMaterialDateGroups: new Set(),
  collapsedTaskDateGroups: new Set(),
  seenTaskDateGroups: new Set(),
  editingPromptId: "",
  creatingPrompt: false,
  promptDraft: null,
  selectedMaterialId: localStorage.getItem("viral-remix-selected-material") || "",
  selectedMaterialIds: new Set(),
  selectedTaskIds: new Set(),
  selectedDoubaoIds: new Set(),
  selectedPartTimeIds: new Set(),
  doubaoCountryCode: localStorage.getItem("viral-remix-doubao-country") || "MY",
  doubaoSortOrder: localStorage.getItem("viral-remix-doubao-sort") || "latest",
  partTimeSortOrder: localStorage.getItem("viral-remix-part-time-sort") || "latest",
  doubaoDateRange: normalizeDateRange(localStorage.getItem("viral-remix-doubao-date-range")),
  partTimeDateRange: normalizeDateRange(localStorage.getItem("viral-remix-part-time-date-range")),
  doubaoTotal: 0,
  doubaoLoadVersion: 0,
  doubaoLoadedRange: null,
  partTimeLoadVersion: 0,
  doubaoRowIndex: {},
  promptCountryFilter: "",
  activePage: localStorage.getItem("viral-remix-active-page") || "materials",
  loadedPages: new Set(),
  pageLoadPromises: new Map(),
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
  partTimePage: 1,
  partTimePageSize: 20,
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

function normalizeDateRange(value) {
  return ["0", "1", "2", "7"].includes(String(value || "")) ? String(value) : "2";
}

function headers() {
  return state.token
    ? { "X-Admin-Token": state.token, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" };
}

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  const contentType = String(res.headers?.get?.("content-type") || "").toLowerCase();
  if (contentType && !contentType.includes("application/json")) {
    const status = Number(res.status) || 0;
    throw new Error(status ? `服务暂时不可用（HTTP ${status}），请稍后重试` : "服务暂时不可用，请稍后重试");
  }
  let data;
  try {
    data = await res.json();
  } catch (_error) {
    throw new Error("服务返回格式异常，请稍后重试");
  }
  if (!res.ok || data.success === false) throw new Error(data.message || data.error || "请求失败");
  const submittedIds = Array.isArray(data.job_ids) && data.job_ids.length
    ? data.job_ids
    : (data.job_id ? [data.job_id] : []);
  submittedIds.forEach((jobId) => state.submittedJobIds.add(String(jobId)));
  return data;
}

async function downloadApi(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  if (!res.ok) {
    const contentType = String(res.headers.get("content-type") || "");
    if (contentType.includes("application/json")) {
      const data = await res.json();
      throw new Error(data.message || data.error || "导出失败");
    }
    throw new Error((await res.text()) || "导出失败");
  }
  const disposition = String(res.headers.get("content-disposition") || "");
  const utf8Name = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plainName = disposition.match(/filename="?([^";]+)"?/i);
  const filename = utf8Name
    ? decodeURIComponent(utf8Name[1])
    : (plainName ? plainName[1] : "批量视频队列.zip");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  return filename;
}

function notify(message, type = "success") {
  const region = $("toastRegion");
  if (!region || !document.createElement) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = String(message || "");
  region.appendChild(toast);
  setTimeout(() => toast.remove(), 4200);
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
  if (key === "part-time") return { pageKey: "partTimePage", sizeKey: "partTimePageSize" };
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

function selectedPartTimeRows() {
  return state.partTimeItems.filter((row) => state.selectedPartTimeIds.has(row.record_id));
}

function partTimeRowsInRange(rows, start, end) {
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) return [];
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  return rows.filter((row) => {
    const line = rowNumber(row);
    return line >= from && line <= to && row.record_id && row["可同步发布"];
  });
}

function syncableDoubaoRows(rows = selectedDoubaoRows(), forceUpdate = false) {
  return rows.filter((row) => canExecuteDoubaoSync(row, forceUpdate)
    && promptCountryMatchesSelection(row)
    && String(row["兼职同步状态"] || "").trim() !== "同步冲突"
    && (forceUpdate || String(row["兼职同步状态"] || "").trim() !== "已同步"));
}

function doubaoSyncSelectionSummary(rows = selectedDoubaoRows(), forceUpdate = false) {
  const eligible = rows.filter((row) => canExecuteDoubaoSync(row, forceUpdate) && promptCountryMatchesSelection(row));
  const conflicts = eligible.filter((row) => String(row["兼职同步状态"] || "").trim() === "同步冲突").length;
  const synced = forceUpdate
    ? 0
    : eligible.filter((row) => String(row["兼职同步状态"] || "").trim() === "已同步").length;
  return {
    selected: rows.length,
    submitted: syncableDoubaoRows(rows, forceUpdate).length,
    synced,
    conflicts,
    unavailable: rows.length - eligible.length,
  };
}

function isJobRunning(name) {
  return state.jobs.some((job) => job.name === name && ["queued", "running"].includes(job.status));
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
    ? `已连接：素材 ${data.source_table_id} · 任务 ${data.task_table_id} · 豆包 ${data.doubao_table_id || "-"} · Prompt ${data.prompt_table_id || "-"} · 兼职 ${data.part_time_table_id || "-"} · 模型 ${data.gemini_model || "-"} · 存储 ${data.storage_mode}`
    : `缺少配置：${data.missing.join(", ")}`;
}

async function loadConfig() {
  try {
    renderConfig(await api("/api/config"));
  } catch (err) {
    $("configLine").textContent = String(err);
  }
}

function renderCountrySelect() {
  const select = $("doubaoCountry");
  if (!select) return;
  if (!state.countries.length) {
    select.disabled = true;
    select.innerHTML = `<option value="">加载国家配置…</option>`;
    return;
  }
  select.disabled = false;
  if (!state.countries.some((country) => country.code === state.doubaoCountryCode)) {
    state.doubaoCountryCode = state.countries[0]?.code || "MY";
  }
  select.innerHTML = state.countries
    .map((country) => `<option value="${esc(country.code)}" ${country.code === state.doubaoCountryCode ? "selected" : ""}>${esc(country.name)} · ${esc(country.language)}</option>`)
    .join("");
}

async function loadDoubaoPromptOptions() {
  const data = await api(`/api/doubao-prompt-options?country_code=${encodeURIComponent(state.doubaoCountryCode)}`);
  state.doubaoPromptOptions = data.data || [];
  const select = $("doubaoPromptConfig");
  const previous = select.value;
  select.innerHTML = `<option value="">T1 兼容模式</option>${state.doubaoPromptOptions.map((row) => (
    `<option value="${esc(row.record_id)}">${esc(row["方案名称"] || row["Prompt名称"])} · ${esc(row["版本"] || "未标版本")} · ${esc(row["适用品类"] || "通用")}</option>`
  )).join("")}`;
  if (state.doubaoPromptOptions.some((row) => row.record_id === previous)) select.value = previous;
}

function selectedCountryName() {
  return state.countries.find((country) => country.code === state.doubaoCountryCode)?.name || "马来西亚";
}

function selectedCountryLanguageName() {
  return state.countries.find((country) => country.code === state.doubaoCountryCode)?.language_name || "马来语";
}

function promptCountryMatchesSelection(row) {
  const promptCountry = String(row["目标国家"] || "").trim() || "马来西亚";
  const promptLanguage = String(row["目标语言"] || "").trim() || "马来语";
  return promptCountry === selectedCountryName() && promptLanguage === selectedCountryLanguageName();
}

function promptRegionOptions() {
  const options = [
    { value: "", label: "全部地区" },
    { value: "通用", label: "通用" },
  ];
  state.countries.forEach((country) => {
    if (!options.some((item) => item.value === country.prompt_region)) {
      options.push({
        value: country.prompt_region,
        label: `${country.name} · ${country.language_name}`,
      });
    }
  });
  return options;
}

function renderPromptRegionFilter() {
  const select = $("promptCountryFilter");
  if (!select) return;
  const options = promptRegionOptions();
  if (!options.some((item) => item.value === state.promptCountryFilter)) state.promptCountryFilter = "";
  select.innerHTML = options
    .map((item) => `<option value="${esc(item.value)}" ${item.value === state.promptCountryFilter ? "selected" : ""}>${esc(item.label)}</option>`)
    .join("");
}

async function loadCountries() {
  try {
    const data = await api("/api/countries");
    if (Array.isArray(data.data) && data.data.length) state.countries = data.data;
  } finally {
    renderCountrySelect();
    renderPromptRegionFilter();
    await loadDoubaoPromptOptions();
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
  const page = ["materials", "videos", "doubao", "part-time", "prompts"].includes(state.activePage) ? state.activePage : "materials";
  state.activePage = page;
  $("materialsPage").classList.toggle("active", page === "materials");
  $("videosPage").classList.toggle("active", page === "videos");
  $("doubaoPage").classList.toggle("active", page === "doubao");
  $("partTimePage").classList.toggle("active", page === "part-time");
  $("promptsPage").classList.toggle("active", page === "prompts");
  const remixActive = page === "materials" || page === "videos";
  $("remixSubnav").hidden = !remixActive;
  $("pageRemix").classList.toggle("active", remixActive);
  $("pageMaterials").classList.toggle("active", page === "materials");
  $("pageVideos").classList.toggle("active", page === "videos");
  $("pageDoubao").classList.toggle("active", page === "doubao");
  $("pagePartTime").classList.toggle("active", page === "part-time");
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

function doubaoSelectionLabel(rows) {
  const lineNumbers = rows.map(rowNumber).filter((line) => line > 0);
  if (!lineNumbers.length) return `豆包素材 ${rows.length} 条`;
  const from = Math.min(...lineNumbers);
  const to = Math.max(...lineNumbers);
  return `豆包素材表行号 ${from}–${to}（已选 ${rows.length} 条）`;
}

function doubaoRowNumberMap(rows) {
  return Object.fromEntries(
    rows
      .map((row) => [String(row.record_id || "").trim(), rowNumber(row)])
      .filter(([recordId, line]) => recordId && line > 0),
  );
}

function partTimeSelectionLabel(rows) {
  const lineNumbers = rows.map(rowNumber).filter((line) => line > 0);
  if (!lineNumbers.length) return `兼职成片 ${rows.length} 条`;
  const from = Math.min(...lineNumbers);
  const to = Math.max(...lineNumbers);
  return `兼职表行号 ${from}–${to}（已选 ${rows.length} 条）`;
}

function partTimeRowNumberMap(rows) {
  return Object.fromEntries(
    rows
      .map((row) => [String(row.record_id || "").trim(), rowNumber(row)])
      .filter(([recordId, line]) => recordId && line > 0),
  );
}

function batchJobSummary(data) {
  const jobIds = Array.isArray(data?.job_ids) && data.job_ids.length
    ? data.job_ids
    : (data?.job_id ? [data.job_id] : []);
  return jobIds.length > 1
    ? `${jobIds.length} 个后台任务（${jobIds.join("、")}）`
    : (jobIds[0] || "任务已提交");
}

function selectMaterialRange() {
  const start = Number($("rangeStart").value);
  const end = Number($("rangeEnd").value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) {
    notify("请输入有效的起始行和结束行", "error");
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
    notify("请输入有效的起始行和结束行", "error");
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

function selectPartTimeRange() {
  const start = Number($("partTimeRangeStart").value);
  const end = Number($("partTimeRangeEnd").value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) {
    notify("请输入有效的兼职表起始行和结束行", "error");
    return;
  }
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  const selectedRows = partTimeRowsInRange(state.partTimeItems, start, end);
  state.selectedPartTimeIds = new Set(selectedRows.map((row) => row.record_id));
  renderPartTimeItems();
  if (!selectedRows.length) notify(`兼职表行号 ${from}–${to} 内没有待审核且可同步的成片`, "error");
}

function clearPartTimeSelection() {
  state.selectedPartTimeIds.clear();
  renderPartTimeItems();
}

function currentVisibleDoubaoRows() {
  return pageRows(state.doubaoItems, state.doubaoPage, state.doubaoPageSize);
}

function renderDoubaoSelectionState() {
  const visibleIds = currentVisibleDoubaoRows().map((row) => row.record_id).filter(Boolean);
  const selectedRows = selectedDoubaoRows();
  const selectedCount = selectedRows.length;
  const exportableCount = selectedRows.filter((row) => row["发布前置条件通过"]).length;
  const forceUpdate = Boolean($("forceDoubaoSyncUpdate")?.checked);
  const syncSummary = doubaoSyncSelectionSummary(selectedRows, forceUpdate);
  const visibleSelectedCount = visibleIds.filter((id) => state.selectedDoubaoIds.has(id)).length;
  $("selectedDoubaoCount").textContent = [
    `已选 ${selectedCount} 条`,
    `可导出 ${exportableCount} 条`,
    `本次提交 ${syncSummary.submitted} 条`,
    syncSummary.synced ? `跳过已同步 ${syncSummary.synced} 条` : "",
    syncSummary.conflicts ? `保护冲突 ${syncSummary.conflicts} 条` : "",
    syncSummary.unavailable ? `前置检查未通过 ${syncSummary.unavailable} 条` : "",
  ].filter(Boolean).join(" · ");
  $("selectAllDoubao").checked = visibleIds.length > 0 && visibleSelectedCount === visibleIds.length;
  $("selectAllDoubao").indeterminate = visibleSelectedCount > 0 && visibleSelectedCount < visibleIds.length;
}

async function selectDoubaoRange() {
  const start = Number($("doubaoRangeStart").value);
  const end = Number($("doubaoRangeEnd").value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) {
    notify("请输入有效的起始行和结束行", "error");
    return;
  }
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  if (to - from + 1 > 1000) {
    notify("单次最多按行号选择 1000 条，请缩小范围", "error");
    return;
  }
  await loadDoubaoRangeItems(from, to, { select: true });
}

function clearDoubaoSelection() {
  state.selectedDoubaoIds.clear();
  renderDoubaoItems();
}

function currentVisiblePartTimeRows() {
  return pageRows(sortedPartTimeItems(), state.partTimePage, state.partTimePageSize);
}

function sortedPartTimeItems() {
  const direction = state.partTimeSortOrder === "oldest" ? 1 : -1;
  return [...state.partTimeItems].sort((left, right) => {
    const leftLine = rowNumber(left);
    const rightLine = rowNumber(right);
    if (!leftLine && !rightLine) return String(left.record_id || "").localeCompare(String(right.record_id || ""));
    if (!leftLine) return 1;
    if (!rightLine) return -1;
    return direction * (leftLine - rightLine)
      || String(left.record_id || "").localeCompare(String(right.record_id || ""));
  });
}

function renderPartTimeSelectionState() {
  const visibleIds = currentVisiblePartTimeRows()
    .filter((row) => row["可同步发布"])
    .map((row) => row.record_id);
  const selectedCount = selectedPartTimeRows().length;
  const batchCount = Math.ceil(selectedCount / 200);
  const visibleSelectedCount = visibleIds.filter((id) => state.selectedPartTimeIds.has(id)).length;
  $("selectedPartTimeCount").textContent = selectedCount
    ? `已选 ${selectedCount} 条 · 将拆分为 ${batchCount} 个后台任务`
    : "已选 0 条";
  $("selectAllPartTime").checked = visibleIds.length > 0 && visibleSelectedCount === visibleIds.length;
  $("selectAllPartTime").indeterminate = visibleSelectedCount > 0 && visibleSelectedCount < visibleIds.length;
}

function renderPartTimeItems() {
  const rows = state.partTimeItems;
  $("metricPartTimeRows").textContent = rows.length;
  $("metricPartTimePending").textContent = rows.filter((row) => row["可同步发布"]).length;
  $("metricPartTimePublished").textContent = rows.filter((row) => row["已同步发布"]).length;
  $("metricPartTimeBlocked").textContent = rows.filter((row) => row["发布阻断原因"]).length;
  if (!rows.length) {
    $("partTimeItems").innerHTML = `<div class="selected-box">暂无外部插件已完成的成片</div>`;
    renderPartTimeSelectionState();
    return;
  }
  state.partTimePage = clampPage(state.partTimePage, rows.length, state.partTimePageSize);
  const body = currentVisiblePartTimeRows().map((row) => {
    const canSync = Boolean(row["可同步发布"]);
    const checked = state.selectedPartTimeIds.has(row.record_id) ? "checked" : "";
    const blocker = row["发布阻断原因"]
      ? `<div class="inline-error"><span>${esc(row["发布阻断原因"])}</span></div>`
      : "";
    return `<tr>
      <td class="select-col"><input class="part-time-check" type="checkbox" aria-label="选择兼职成片 ${esc(row.record_id)}" data-part-time-select-id="${esc(row.record_id)}" ${checked} ${canSync ? "" : "disabled"}></td>
      <td class="row-no">${esc(row["行号"] || "")}</td>
      <td><strong>${esc(row["文本"] || "未填写任务标题")}</strong><div class="muted">兼职：${esc(row.record_id)}</div><div class="muted">素材：${esc(row["来源记录ID"] || "")}</div></td>
      <td>${status(row["任务状态"] || "未完成")}<div class="muted">${esc(row["审核"] || "未标记")}</div></td>
      <td>${status(row["目标国家"] || "未记录")}<div class="muted">${esc(row["目标语言"] || "未记录语言")}</div></td>
      <td>${row["视频地址"] ? `<a href="${esc(row["视频地址"])}" target="_blank">查看成片</a>` : `<span class="muted">缺少视频</span>`}</td>
      <td>${status(row["发布状态"] || "待审核")}${blocker}</td>
    </tr>`;
  }).join("");
  $("partTimeItems").innerHTML = `${renderPagination("part-time", rows.length)}<table><thead><tr>
    <th class="select-col">选</th><th class="row-no">行</th><th>成片任务</th><th>任务 / 审核</th><th>国家 / 语言</th><th>视频</th><th>发布状态</th>
  </tr></thead><tbody>${body}</tbody></table>`;
  document.querySelectorAll("[data-part-time-select-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedPartTimeIds.add(checkbox.dataset.partTimeSelectId);
      else state.selectedPartTimeIds.delete(checkbox.dataset.partTimeSelectId);
      renderPartTimeSelectionState();
    });
  });
  bindPagination($("partTimeItems"), "part-time", renderPartTimeItems);
  renderPartTimeSelectionState();
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
    <td class="row-no">${esc(row["行号"] || "…")}</td>
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
  $("metricDoubaoRows").textContent = state.doubaoTotal || state.doubaoItems.length;
  $("metricDoubaoImages").textContent = state.doubaoItems.filter((row) => hasDoubaoImage(row)).length;
  $("metricDoubaoStored").textContent = state.doubaoItems.filter((row) => hasDoubaoStoredImage(row)).length;
  $("metricDoubaoFaceClear").textContent = state.doubaoItems.filter((row) => row["人脸检测状态"] === "无人脸").length;
  $("metricDoubaoFaces").textContent = state.doubaoItems.filter((row) => ["含人脸", "疑似人脸"].includes(row["人脸检测状态"])).length;
  $("metricDoubaoPrompts").textContent = state.doubaoItems.filter((row) => String(row["最终视频生成提示词"] || "").trim()).length;
  $("metricDoubaoSynced").textContent = state.doubaoItems.filter((row) => String(row["兼职同步状态"] || "").trim() === "已同步").length;
}

function hasDoubaoImage(row) {
  return Boolean(row["商品图已入库"])
    || Boolean(row["商品图可转存"])
    || Number(row["商品图数量"] || 0) > 0
    || String(row["商品图链接"] || "").startsWith("http");
}

function hasDoubaoStoredImage(row) {
  return Boolean(row["商品图已入库"]);
}

function canTransferDoubaoImage(row) {
  if (row["可执行图片转存"] !== undefined) return Boolean(row["可执行图片转存"]);
  return Boolean(row["商品图可转存"]);
}

function canExecuteDoubaoFaceDetection(row) {
  if (row["可执行人脸检测"] !== undefined) return Boolean(row["可执行人脸检测"]);
  return Boolean(row["人脸可检测"]);
}

function canExecuteDoubaoPrompt(row) {
  if (row["可执行生成提示词"] !== undefined) return Boolean(row["可执行生成提示词"]);
  return Boolean(row["可生成提示词"]);
}

function canExecuteDoubaoSync(row, forceUpdate = false) {
  if (forceUpdate) return Boolean(row["发布前置条件通过"] ?? row["可同步兼职"]);
  if (row["可执行同步兼职"] !== undefined) return Boolean(row["可执行同步兼职"]);
  return Boolean(row["可同步兼职"]);
}

function renderDoubaoRow(row, extraClass = "") {
  const prompt = String(row["最终视频生成提示词"] || "").trim();
  const promptName = `doubao-generate-prompt:${row.record_id}:${state.doubaoCountryCode}`;
  const storeName = `doubao-store-image:${row.record_id}`;
  const syncName = `doubao-sync-part-time:${row.record_id}:${state.doubaoCountryCode}`;
  const checked = state.selectedDoubaoIds.has(row.record_id) ? "checked" : "";
  const promptPending = state.pendingJobs.has(promptName);
  const storePending = state.pendingJobs.has(storeName);
  const promptRunning = isJobBusy(promptName);
  const storeRunning = isJobBusy(storeName);
  const syncPending = state.pendingJobs.has(syncName);
  const syncRunning = isJobBusy(syncName);
  const syncState = String(row["兼职同步状态"] || "未同步").trim();
  const faceState = String(row["人脸检测状态"] || "待检测").trim();
  const faceBusy = isJobBusy("doubao-batch-detect-faces");
  const whiteBackgroundName = `doubao-generate-white-background:${row.record_id}`;
  const whiteBackgroundBusy = isJobBusy(whiteBackgroundName) || state.pendingJobs.has(whiteBackgroundName);
  const whiteBackgroundStatus = String(row["白底图处理状态"] || "").trim();
  const canGenerateWhiteBackground = Boolean(row["可生成白底图"]);
  const whiteBackgroundBlocker = String(row["白底图阻断原因"] || "").trim();
  const countryMatches = promptCountryMatchesSelection(row);
  const sourceBlocker = String(row["素材阻塞原因"] || "").trim();
  const flowStage = String(row["流程阶段"] || "待处理").trim();
  const flowLockReason = String(row["流程锁定原因"] || "").trim();
  const canDetectFace = canExecuteDoubaoFaceDetection(row);
  const canGeneratePrompt = canExecuteDoubaoPrompt(row);
  const canSyncPartTime = canExecuteDoubaoSync(row);
  const syncBlocker = !countryMatches
    ? `请先按${selectedCountryName()}·${selectedCountryLanguageName()}重新生成提示词`
    : !canSyncPartTime
      ? flowLockReason || sourceBlocker || "未满足发布前置条件"
      : "";
  const storedImageUrl = String(row["商品图存储链接"] || "").trim();
  const storageCell = hasDoubaoStoredImage(row)
    ? `<a href="${esc(storedImageUrl)}" target="_blank">对象存储</a>`
    : storedImageUrl.startsWith("http")
      ? `${status("外链待转存")}<div><a href="${esc(storedImageUrl)}" target="_blank">打开外链</a></div>`
      : canTransferDoubaoImage(row) ? status("待转存") : `<span class="muted">缺少可转存图片</span>`;
  const storeLabel = storePending ? "提交中" : storeRunning ? "转存中" : hasDoubaoStoredImage(row) ? "已入库" : "立即转存";
  return `<tr class="${esc(extraClass)}">
    <td class="select-col"><input class="doubao-check" type="checkbox" aria-label="选择第 ${esc(row["行号"] || "")} 行" data-doubao-select-id="${esc(row.record_id)}" ${checked}></td>
    <td class="row-no">${esc(row["行号"] || "")}</td>
    <td>
      <strong>${esc(row["商品名称"] || "未填写商品名称")}</strong>
      <div class="muted">商品 ID：${esc(row["商品ID"] || "未填写")}</div>
      <div class="muted">${esc(row.record_id || "")}</div>
      ${row["商品链接"] ? `<a href="${esc(row["商品链接"])}" target="_blank">商品链接</a>` : ""}
    </td>
    <td>
      ${status(row["目标国家"] || "未选择")}
      <div class="muted">${esc(row["目标语言"] || "未记录语言")}</div>
      <div class="muted">${esc(row["Prompt配置版本"] || "未记录版本")}</div>
    </td>
    <td>
      ${hasDoubaoImage(row) ? status(row["图片来源"] === "链接" ? "图片链接" : `已上传${row["商品图数量"]}张`) : status("缺少商品图")}
      <div class="muted">${esc(row["商品图文件名"] || "")}</div>
      ${row["商品图链接"] ? `<a href="${esc(row["商品图链接"])}" target="_blank">打开图片链接</a>` : ""}
    </td>
    <td>
      ${storageCell}
      <button class="mini ghost${storePending ? " is-pending" : ""}" data-action="doubao-store" data-id="${esc(row.record_id)}" ${storeRunning || !canTransferDoubaoImage(row) ? "disabled" : ""}>${storeLabel}</button>
    </td>
    <td>
      ${status(faceState)}
      ${row["人脸检测说明"] ? `<div class="muted" title="${esc(row["人脸检测说明"])}">${esc(compactErrorMessage(row["人脸检测说明"]))}</div>` : ""}
      ${whiteBackgroundStatus ? `<div class="muted">白底图：${esc(whiteBackgroundStatus)}</div>` : ""}
      ${row["白底图处理错误"] ? `<div class="muted">${esc(compactErrorMessage(row["白底图处理错误"]))}</div>` : ""}
      <div class="step-cell">
        <button class="mini" data-action="doubao-detect-face" data-id="${esc(row.record_id)}" title="${esc(flowLockReason || "检测当前对象存储图片")}" ${faceBusy || !canDetectFace ? "disabled" : ""}>${faceState === "无人脸" ? "重新检测" : "检测人脸"}</button>
        <button class="mini secondary" data-action="doubao-generate-white-background" data-id="${esc(row.record_id)}" title="${esc(whiteBackgroundBlocker || "付费生成白底商品图并自动复检")}" ${whiteBackgroundBusy || !canGenerateWhiteBackground ? "disabled" : ""}>${whiteBackgroundBusy ? "处理中" : "生成白底图"}</button>
      </div>
    </td>
    <td>${prompt ? renderPromptDetail("查看Prompt", prompt) : `<span class="muted">未生成</span>`}</td>
    <td class="publish-cell">
      <strong>${esc(row["标题"] || "")}</strong>
      <div class="muted">${esc(row["标签"] || "")}</div>
      ${row["文案"] ? `<details class="prompt-preview"><summary>文案</summary><p>${esc(row["文案"])}</p></details>` : ""}
    </td>
    <td>
      ${status(syncState)}
      ${row["兼职同步错误"] ? renderInlineError(row["兼职同步错误"], "") : ""}
      ${!row["兼职同步错误"] && syncBlocker ? `<div class="muted">${esc(syncBlocker)}</div>` : ""}
    </td>
    <td><div class="step-cell">
      <span class="flow-stage">${esc(flowStage)}</span>
      ${flowLockReason ? `<small class="flow-lock-reason">${esc(flowLockReason)}</small>` : ""}
      <button class="mini${promptPending ? " is-pending" : ""}" data-action="doubao-generate" data-id="${esc(row.record_id)}" title="${esc(flowLockReason || "按当前国家语言生成提示词")}" ${promptRunning || !canGeneratePrompt ? "disabled" : ""}>${promptPending ? "提交中" : promptRunning ? "进行中" : prompt ? "重新生成" : "生成提示词"}</button>
      <button class="mini sync-button${syncPending ? " is-pending" : ""}" data-action="doubao-sync" data-id="${esc(row.record_id)}" title="${esc(syncBlocker || "同步当前国家语言任务")}" ${syncRunning || !canSyncPartTime || !countryMatches ? "disabled" : ""}>${syncPending ? "提交中" : syncRunning ? "同步中" : countryMatches ? "同步兼职表" : "先重生成"}</button>
    </div></td>
  </tr>`;
}

function renderDoubaoItems() {
  renderDoubaoMetrics();
  if (!state.doubaoItems.length) {
    $("doubaoItems").innerHTML = `<div class="selected-box">暂无豆包视频素材记录</div>`;
    renderDoubaoSelectionState();
    return;
  }
  state.doubaoPage = clampPage(state.doubaoPage, state.doubaoItems.length, state.doubaoPageSize);
  const visibleRows = pageRows(state.doubaoItems, state.doubaoPage, state.doubaoPageSize);
  const body = visibleRows.map((row) => renderDoubaoRow(row)).join("");
  $("doubaoItems").innerHTML = `${renderPagination("doubao", state.doubaoItems.length)}<table><thead><tr>
    <th class="select-col">选</th><th class="row-no">行</th><th>商品</th><th>国家 / 语言 / 版本</th><th>图片</th><th>对象存储</th><th>人脸检测</th><th>最终视频生成提示词</th><th>发布内容</th><th>兼职同步</th><th>操作</th>
  </tr></thead><tbody>${body}</tbody></table>`;

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
  document.querySelectorAll("[data-action='doubao-detect-face']").forEach((button) => {
    button.addEventListener("click", () => batchDoubaoDetectFaces(button, [button.dataset.id]));
  });
  document.querySelectorAll("[data-action='doubao-generate-white-background']").forEach((button) => {
    button.addEventListener("click", () => generateDoubaoWhiteBackground(button.dataset.id, button));
  });
  document.querySelectorAll("[data-action='doubao-generate']").forEach((button) => {
    button.addEventListener("click", () => generateDoubaoPrompt(button.dataset.id, button));
  });
  document.querySelectorAll("[data-action='doubao-sync']").forEach((button) => {
    button.addEventListener("click", () => syncDoubaoPartTime(button.dataset.id, button));
  });
  bindPagination($("doubaoItems"), "doubao", renderDoubaoItems);
  bindPromptDetails($("doubaoItems"));
  renderDoubaoSelectionState();
}

function renderPrompts() {
  renderPromptEditor();
  const filteredRows = state.promptCountryFilter
    ? state.prompts.filter((row) => String(row["目标地区"] || "") === state.promptCountryFilter)
    : state.prompts;
  const rows = [...filteredRows].sort((left, right) => (
    String(left["目标地区"] || "").localeCompare(String(right["目标地区"] || ""), "zh-CN")
    || String(left["Prompt名称"] || "").localeCompare(String(right["Prompt名称"] || ""), "zh-CN")
    || String(left["版本"] || "").localeCompare(String(right["版本"] || ""), "zh-CN")
  ));
  if (!rows.length) {
    $("prompts").innerHTML = `<div class="selected-box">暂无 Prompt 配置</div>`;
    return;
  }
  $("prompts").innerHTML = `<table><thead><tr>
    <th>Prompt</th><th>方案</th><th>模式</th><th>版本</th><th>状态</th><th>市场 / 品类</th><th>模板内容</th><th>操作</th>
  </tr></thead><tbody>${rows.map((row) => {
    const content = String(row["模板内容"] || "").trim();
    return `<tr>
      <td><strong>${esc(row["Prompt名称"] || "")}</strong><div class="muted">${esc(row.record_id || "")}</div></td>
      <td><strong>${esc(row["方案名称"] || "T1 当前转化模板")}</strong><div class="muted"><code>${esc(row["方案Key"] || "current_conversion")}</code></div></td>
      <td><code>${esc(row["执行模式"] || "meta_generate")}</code></td>
      <td>${esc(row["版本"] || "")}</td>
      <td>${status(row["是否启用"] || "未设置")}</td>
      <td>${esc(row["目标地区"] || "")}<div class="muted">${esc(row["目标语言"] || "通用语言")} · ${esc(row["适用品类"] || "通用")}</div></td>
      <td>${renderPromptDetail("查看模板", content)}<div class="muted">${esc(row["变量说明"] || "")}</div></td>
      <td><button class="mini" data-action="edit-prompt" data-id="${esc(row.record_id)}">编辑</button> <button class="mini secondary" data-action="clone-prompt" data-id="${esc(row.record_id)}">复制新版本</button></td>
    </tr>`;
  }).join("")}</tbody></table>`;

  document.querySelectorAll("[data-action='edit-prompt']").forEach((button) => {
    button.addEventListener("click", () => {
      state.creatingPrompt = false;
      state.editingPromptId = button.dataset.id;
      renderPromptEditor();
      $("promptEditor").scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });
  document.querySelectorAll("[data-action='clone-prompt']").forEach((button) => {
    button.addEventListener("click", () => {
      const source = state.prompts.find((item) => item.record_id === button.dataset.id);
      if (!source) return;
      state.editingPromptId = "";
      state.creatingPrompt = true;
      state.promptDraft = {
        ...source,
        record_id: "",
        revision: "",
        "Prompt名称": `${source["Prompt名称"] || "Prompt"}-新版本`,
        "版本": "",
        "是否启用": "停用",
        "版本已锁定": "",
      };
      renderPromptEditor();
      $("promptEditor").scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });
  bindPromptDetails($("prompts"));
}

function renderPromptEditor() {
  const box = $("promptEditor");
  const defaultRegion = state.promptCountryFilter
    || state.countries.find((country) => country.code === state.doubaoCountryCode)?.prompt_region
    || "通用";
  const defaultCountry = state.countries.find((country) => country.prompt_region === defaultRegion);
  const defaultNameRegion = defaultCountry
    ? `${defaultCountry.name}-${defaultCountry.language_name}`
    : defaultRegion;
  const row = state.creatingPrompt
    ? (state.promptDraft || {
      "Prompt名称": `豆包视频-${defaultNameRegion}-新方案`,
      "Prompt类型": "豆包视频",
      "模板Key": "doubao_seedance_video_prompt",
      "方案Key": "current_conversion",
      "方案名称": "T1 当前转化模板",
      "执行模式": "meta_generate",
      "版本": "",
      "是否启用": "停用",
      "目标地区": defaultRegion,
      "目标语言": defaultCountry?.language_name || "",
      "适用品类": "通用",
      "视频时长": "15",
      "模板内容": "",
      "输出协议": "json",
      "变量说明": "{product_name} 商品名称\n{product_link} 商品链接",
      "备注": "",
    })
    : state.prompts.find((item) => item.record_id === state.editingPromptId);
  if (!row) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  const versionLocked = !state.creatingPrompt && (
    row["版本已锁定"] === "是" || row["是否启用"] === "启用"
  );
  const immutableDisabled = versionLocked ? "disabled" : "";
  box.innerHTML = `<div class="editor-head">
    <div>
      <strong>${state.creatingPrompt ? "新增豆包视频提示词" : "编辑 Prompt"}</strong>
      <span>${esc(row["模板Key"] || "")} · ${esc(row["Prompt类型"] || "")}</span>
    </div>
    <button id="closePromptEditor" class="mini secondary" type="button">收起</button>
  </div>
  <div class="editor-grid">
    <label>Prompt名称<input id="editPromptName" type="text" value="${esc(row["Prompt名称"] || "")}"></label>
    <label>模板 Key<input id="editPromptTemplateKey" type="text" value="${esc(row["模板Key"] || "doubao_seedance_video_prompt")}" ${immutableDisabled}></label>
    <label>方案 Key<input id="editPromptSchemeKey" type="text" value="${esc(row["方案Key"] || "current_conversion")}" ${immutableDisabled}></label>
    <label>方案名称<input id="editPromptSchemeName" type="text" value="${esc(row["方案名称"] || "T1 当前转化模板")}"></label>
    <label>执行模式<select id="editPromptExecutionMode" ${immutableDisabled}>
      ${["meta_generate", "direct_prompt"].map((item) => `<option value="${item}" ${row["执行模式"] === item ? "selected" : ""}>${item}</option>`).join("")}
    </select></label>
    <label>版本<input id="editPromptVersion" type="text" value="${esc(row["版本"] || "")}" ${immutableDisabled}></label>
    <label>状态<select id="editPromptEnabled">
      ${["启用", "停用"].map((item) => `<option value="${item}" ${row["是否启用"] === item ? "selected" : ""}>${item}</option>`).join("")}
    </select></label>
    <label>地区<select id="editPromptRegion" ${immutableDisabled}>
      ${promptRegionOptions().filter((item) => item.value).map((item) => `<option value="${esc(item.value)}" ${row["目标地区"] === item.value ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
    </select></label>
    <label>目标语言<input id="editPromptLanguage" type="text" value="${esc(row["目标语言"] || "")}" ${immutableDisabled}></label>
    <label>适用品类<select id="editPromptCategory" ${immutableDisabled}>
      ${["通用", "鞋类", "服装", "其他"].map((item) => `<option value="${item}" ${row["适用品类"] === item ? "selected" : ""}>${item}</option>`).join("")}
    </select></label>
    <label>视频时长（秒）<input id="editPromptDuration" type="number" min="1" value="${esc(row["视频时长"] || "")}" ${immutableDisabled}></label>
    <label>输出协议<input id="editPromptOutputProtocol" type="text" value="${esc(row["输出协议"] || "json")}" ${immutableDisabled}></label>
  </div>
  <label class="editor-block">模板内容<textarea id="editPromptContent" rows="18" ${immutableDisabled}>${esc(row["模板内容"] || "")}</textarea></label>
  <label class="editor-block">变量说明<textarea id="editPromptVars" rows="3" ${immutableDisabled}>${esc(row["变量说明"] || "")}</textarea></label>
  <label class="editor-block">备注<textarea id="editPromptNote" rows="3">${esc(row["备注"] || "")}</textarea></label>
  ${versionLocked ? `<div class="editor-hint">Prompt 版本已锁定：可更新名称、备注和启停状态；内容变更请使用“复制新版本”。</div>` : ""}
  ${state.creatingPrompt ? `<div class="editor-hint">新模板会先作为草稿保存。如果直接选择“启用”，同一国家当前生效的豆包视频模板会自动停用。</div>` : ""}
  <div class="editor-actions">
    <button id="savePromptEdit" type="button">${state.creatingPrompt ? "新增到飞书" : "保存到飞书"}</button>
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

async function loadPartTimeItems(options = {}) {
  const loadVersion = state.partTimeLoadVersion + 1;
  state.partTimeLoadVersion = loadVersion;
  const path = `/api/part-time-items?days=${encodeURIComponent(state.partTimeDateRange)}`;
  const data = await api(tablePath(path, options));
  if (loadVersion !== state.partTimeLoadVersion) return;
  state.partTimeItems = data.data;
  const syncableIds = new Set(state.partTimeItems.filter((row) => row["可同步发布"]).map((row) => row.record_id));
  state.selectedPartTimeIds = new Set([...state.selectedPartTimeIds].filter((id) => syncableIds.has(id)));
  renderPartTimeItems();
  markTableLoaded("partTime", "兼职成片", state.partTimeItems.length);
  markPageLoaded("part-time");
}

async function loadDoubaoRangeItems(start, end, options = {}) {
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  const loadVersion = state.doubaoLoadVersion + 1;
  state.doubaoLoadVersion = loadVersion;
  state.doubaoLoadedRange = { start: from, end: to };
  state.doubaoItems = [];
  state.doubaoRowIndex = {};
  state.doubaoTotal = 0;
  renderDoubaoSelectionState();
  $("doubaoRefreshState").textContent = `正在读取豆包素材表行号 ${from}–${to}…`;
  $("doubaoItems").innerHTML = `<div class="selected-box" role="status"><strong>正在读取指定行号</strong>只读取 ${from}–${to}，无需等待全表加载。</div>`;
  const params = new URLSearchParams({
    start_row: String(from),
    end_row: String(to),
  });
  if (options.force) params.set("force", "1");
  const data = await api(`/api/doubao-items/range?${params.toString()}`);
  if (loadVersion !== state.doubaoLoadVersion) return;
  const rows = Array.isArray(data.data) ? data.data : [];
  rows.sort((left, right) => {
    const delta = rowNumber(left) - rowNumber(right);
    return state.doubaoSortOrder === "oldest" ? delta : -delta;
  });
  state.doubaoItems = rows;
  state.doubaoRowIndex = Object.fromEntries(rows.map((row) => [row.record_id, rowNumber(row)]));
  state.doubaoTotal = Number(data.total || rows.length);
  const rowIds = new Set(rows.map((row) => row.record_id).filter(Boolean));
  state.selectedDoubaoIds = options.select
    ? rowIds
    : new Set([...state.selectedDoubaoIds].filter((id) => rowIds.has(id)));
  state.doubaoPage = 1;
  renderDoubaoItems();
  markTableLoaded("doubao", `豆包表行号 ${from}–${to}`, rows.length);
  markPageLoaded("doubao");
}

async function loadDoubaoItems(options = {}) {
  if (state.doubaoLoadedRange && !options.full) {
    return loadDoubaoRangeItems(
      state.doubaoLoadedRange.start,
      state.doubaoLoadedRange.end,
      options,
    );
  }
  const loadVersion = state.doubaoLoadVersion + 1;
  state.doubaoLoadVersion = loadVersion;
  state.doubaoLoadedRange = null;
  state.doubaoItems = [];
  state.doubaoRowIndex = {};
  state.doubaoTotal = 0;
  renderDoubaoSelectionState();
  $("doubaoRefreshState").textContent = "正在加载全部豆包素材与飞书真实行号…";
  $("doubaoItems").innerHTML = `<div class="selected-box" role="status"><strong>正在加载全部豆包素材</strong>数据与真实行号全部加载完成后统一显示，请稍候。</div>`;
  const rows = [];
  const seenPageTokens = new Set();
  let pageToken = "";
  let total = 0;
  const rowIndexPromise = api(tablePath("/api/doubao-items/row-index", options)).then(
    (data) => ({ data }),
    (error) => ({ error }),
  );
  while (true) {
    const params = new URLSearchParams({ page_size: "100", offset: String(rows.length) });
    const finiteDateRange = state.doubaoDateRange !== "0";
    params.set("order", finiteDateRange ? "latest" : state.doubaoSortOrder);
    params.set("days", state.doubaoDateRange);
    if (pageToken) params.set("page_token", pageToken);
    if (options.force) params.set("force", "1");
    const data = await api(`/api/doubao-items/page?${params.toString()}`);
    if (loadVersion !== state.doubaoLoadVersion) return;
    rows.push(...data.data);
    total = Number(data.total || total || 0);
    $("doubaoRefreshState").textContent = `正在加载全部豆包素材与飞书真实行号…已读取 ${rows.length}${total ? ` / ${total}` : ""} 条`;
    if (!data.has_more) break;
    const nextPageToken = String(data.page_token || "");
    if (!nextPageToken || seenPageTokens.has(nextPageToken)) {
      throw new Error("飞书分页游标异常，豆包表读取未完成");
    }
    seenPageTokens.add(nextPageToken);
    pageToken = nextPageToken;
  }
  const rowIndexResult = await rowIndexPromise;
  if (rowIndexResult.error) throw rowIndexResult.error;
  if (loadVersion !== state.doubaoLoadVersion) return;
  state.doubaoRowIndex = rowIndexResult.data.data || {};
  rows.forEach((row) => {
    row["行号"] = state.doubaoRowIndex[row.record_id] || "";
  });
  rows.sort((left, right) => {
    const delta = rowNumber(left) - rowNumber(right);
    return state.doubaoSortOrder === "oldest" ? delta : -delta;
  });
  state.doubaoItems = rows;
  state.doubaoTotal = state.doubaoDateRange === "0"
    ? Math.max(Number(rowIndexResult.data.total || 0), total, rows.length)
    : rows.length;
  const existingIds = new Set(rows.map((row) => row.record_id));
  state.selectedDoubaoIds = new Set([...state.selectedDoubaoIds].filter((id) => existingIds.has(id)));
  renderDoubaoItems();
  markTableLoaded("doubao", "豆包表", rows.length);
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
  state.creatingPrompt = false;
  state.promptDraft = null;
  renderPromptEditor();
}

async function savePromptEdit() {
  const isCreating = state.creatingPrompt;
  if (!isCreating && !state.editingPromptId) return;
  const row = state.prompts.find((item) => item.record_id === state.editingPromptId);
  if (!isCreating && !row?.revision) {
    notify("Prompt 版本已过期，请刷新后重新编辑", "error");
    await loadPrompts({ force: true });
    return;
  }
  const button = $("savePromptEdit");
  const fields = {
    "Prompt名称": $("editPromptName").value.trim(),
    "模板Key": $("editPromptTemplateKey").value.trim(),
    "方案Key": $("editPromptSchemeKey").value.trim(),
    "方案名称": $("editPromptSchemeName").value.trim(),
    "执行模式": $("editPromptExecutionMode").value,
    "版本": $("editPromptVersion").value.trim(),
    "是否启用": $("editPromptEnabled").value,
    "目标地区": $("editPromptRegion").value,
    "目标语言": $("editPromptLanguage").value.trim(),
    "适用品类": $("editPromptCategory").value,
    "视频时长": $("editPromptDuration").value.trim(),
    "模板内容": $("editPromptContent").value.trim(),
    "输出协议": $("editPromptOutputProtocol").value.trim(),
    "变量说明": $("editPromptVars").value.trim(),
    "备注": $("editPromptNote").value.trim(),
  };
  const missing = ["Prompt名称", "版本", "目标地区", "模板内容"].find((field) => !fields[field]);
  if (missing) {
    notify(`${missing}不能为空`, "error");
    return;
  }
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "保存中";
  try {
    await api(isCreating ? "/api/prompts" : `/api/prompts/${state.editingPromptId}`, {
      method: isCreating ? "POST" : "PATCH",
      body: JSON.stringify(isCreating ? fields : { "revision": row.revision, ...fields }),
    });
    notify(isCreating ? "提示词已新增到飞书" : "Prompt 已保存");
    state.creatingPrompt = false;
    state.promptDraft = null;
    state.editingPromptId = "";
    await loadPrompts({ force: true });
  } catch (error) {
    notify(error.message || "Prompt 保存失败", "error");
    if (!isCreating) await loadPrompts({ force: true });
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

const JOB_ACTION_LABELS = {
  "doubao.store_image": "转存商品图到对象存储",
  "doubao.batch_store_images": "批量转存商品图",
  "doubao.detect_face": "检测商品图人脸",
  "doubao.batch_detect_faces": "批量检测商品图人脸",
  "doubao.delete_face_record": "删除含人脸素材记录",
  "doubao.batch_delete_face_records": "批量删除含人脸素材记录",
  "doubao.generate_prompt": "生成豆包视频提示词",
  "doubao.batch_generate_prompts": "批量生成豆包视频提示词",
  "doubao.sync_part_time": "同步到兼职任务表",
  "doubao.batch_sync_part_time": "批量同步到兼职任务表",
  "doubao.batch_workflow": "按流程批量处理豆包素材",
  "publish.sync_doubao": "同步豆包视频到发布表",
  "publish.sync_part_time": "审核兼职成片并同步发布表",
  "material.prepare": "处理视频复刻素材",
  "material.batch_prepare": "批量处理视频复刻素材",
  "material.analyze_video": "拆解爆款视频",
  "material.store_image": "转存商品主图",
  "material.generate_images": "生成商品组图",
  "material.create_tasks": "创建视频任务",
  "material.batch_step": "批量处理视频复刻步骤",
  "material.retry_failed_step": "重试失败素材步骤",
  "task.generate_video": "生成带货视频",
  "task.batch_generate_video": "批量生成带货视频",
  "task.sync_reference_image": "同步视频参考图",
  "task.batch_sync_reference_images": "批量同步视频参考图",
  "task.store_manual_video": "转存人工视频",
  "task.batch_store_manual_videos": "批量转存人工视频",
  "publish.sync": "同步视频到发布表",
};

function jobLabel(job) {
  return JOB_ACTION_LABELS[job.action] || String(job.name || "后台处理任务");
}

function jobTime(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "";
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(new Date(timestamp));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

function jobErrorMessage(value) {
  const raw = String(value || "").trim();
  if (!raw) return "未返回具体失败原因";
  if (/RecordIdNotFound/i.test(raw)) return "飞书记录不存在（可能已删除或重建）";
  if (/Data not ready/i.test(raw)) return "上游数据尚未准备好，请稍后重试";
  if (/Gemini HTTP 502|502 Bad Gateway/i.test(raw)) return "AI 服务临时异常（HTTP 502）";
  if (/HTTP 403|download failed/i.test(raw)) return "源图片拒绝访问（HTTP 403）";
  return compactErrorMessage(raw);
}

function jobOutcome(job, processed, failed, conflicts = 0, blocked = 0) {
  if (job.cancel_requested && ["queued", "running"].includes(job.status)) {
    return `<span class="status running">停止中</span>`;
  }
  if (job.status === "cancelled") return `<span class="status cancelled">已停止</span>`;
  if (job.status === "queued") return `<span class="status">排队中</span>`;
  if (job.status === "running") return `<span class="status running">处理中</span>`;
  if (job.status === "failed") return `<span class="status failed">任务失败</span>`;
  if (failed > 0 && processed > 0 && failed >= processed) {
    return `<span class="status failed">全部失败 ${failed}/${processed}</span>`;
  }
  if (failed > 0) return `<span class="status failed">部分失败 ${failed}/${processed || failed}</span>`;
  if (conflicts > 0) return `<span class="status protected">完成，保护跳过 ${conflicts}</span>`;
  if (blocked > 0) return `<span class="status protected">完成，流程拦截 ${blocked}</span>`;
  return `<span class="status done">已完成</span>`;
}

function jobFailureItems(job) {
  const result = job.result || {};
  const items = Array.isArray(result.failures)
    ? result.failures
    : Array.isArray(result.results)
      ? result.results.filter((item) => item?.error || item?.status === "failed")
      : [];
  if (job.error && !items.length) return [{ error: job.error }];
  return items;
}

function jobConflictItems(job) {
  const result = job.result || {};
  return Array.isArray(result.conflict_items) ? result.conflict_items : [];
}

function jobBlockedItems(job) {
  const result = job.result || {};
  return Array.isArray(result.blocked_items) ? result.blocked_items : [];
}

function jobItemLocation(item, index) {
  const recordId = item?.record_id ? String(item.record_id) : `第 ${index + 1} 条`;
  const row = Number(item?.row_number || 0);
  const sourceTable = String(item?.source_table || "").trim();
  if (row > 0 && sourceTable) return `${sourceTable}第 ${row} 行（${recordId}）`;
  return recordId;
}

const jobCountryLabels = {
  PH: "菲律宾",
  MY: "马来西亚",
  MY_EN: "马来西亚",
  MX: "墨西哥",
  TH: "泰国",
  ID: "印度尼西亚",
};

function renderJob(job) {
  const result = job.result || {};
  const failed = Number(result.failed || 0);
  const conflicts = Number(result.conflicts || 0);
  const blocked = Number(result.blocked || 0);
  const processed = Number(result.processed || 0);
  const cancelled = Number(result.cancelled || 0);
  const succeeded = Math.max(0, processed - failed - conflicts - blocked);
  const failureItems = jobFailureItems(job);
  const conflictItems = jobConflictItems(job);
  const blockedItems = jobBlockedItems(job);
  const failureTotal = Number(result.failure_items_total || failureItems.length || (job.error ? 1 : 0));
  const conflictTotal = Number(result.conflict_items_total || conflictItems.length);
  const blockedTotal = Number(result.blocked_items_total || blockedItems.length);
  const createdAt = jobTime(job.created_at);
  const finishedAt = jobTime(job.finished_at);
  const target = job.target || {};
  const targetCountry = jobCountryLabels[String(target.country_code || "").toUpperCase()] || target.country_code || "";
  const targetMeta = [
    target.label,
    target.record_count ? `${target.record_count} 条` : "",
    targetCountry,
  ].filter(Boolean);
  const targetLine = targetMeta.length
    ? `<div class="job-target">处理对象：${targetMeta.map(esc).join(" · ")}</div>`
    : "";
  const stats = processed > 0 || cancelled > 0
    ? `<div class="job-stats"><span>处理 ${processed} 条</span><span class="job-success">成功 ${succeeded} 条</span>${blocked ? `<span class="job-protected">流程拦截 ${blocked} 条</span>` : ""}${conflicts ? `<span class="job-protected">保护跳过 ${conflicts} 条</span>` : ""}${failed ? `<span class="job-failed">失败 ${failed} 条</span>` : ""}${cancelled ? `<span class="job-cancelled">未处理 ${cancelled} 条</span>` : ""}</div>`
    : "";
  const cancellableActions = new Set([
    "doubao.batch_store_images",
    "doubao.batch_detect_faces",
    "doubao.batch_delete_face_records",
    "doubao.batch_generate_prompts",
    "doubao.batch_sync_part_time",
    "doubao.batch_workflow",
    "publish.sync_doubao",
    "publish.sync_part_time",
    "material.batch_step",
    "material.batch_prepare",
    "task.batch_generate_video",
    "task.batch_sync_reference_images",
    "task.batch_store_manual_videos",
  ]);
  const canCancel = cancellableActions.has(String(job.action || ""))
    && ["queued", "running"].includes(job.status);
  const cancelControl = canCancel
    ? `<button class="mini job-cancel" type="button" data-action="cancel-job" data-job-id="${esc(job.id)}"${job.cancel_requested ? " disabled" : ""}>${job.cancel_requested ? "停止中" : "停止任务"}</button>`
    : "";
  const retry = failureTotal > 0 && !["queued", "running"].includes(job.status)
    ? `<button class="mini retry-mini job-retry" type="button" data-action="retry-job-failures" data-job-id="${esc(job.id)}">重试失败 ${failureTotal} 条</button>`
    : "";
  const failures = failureItems.length
    ? `<details class="job-failures">
        <summary>查看 ${failureTotal} 条失败明细${failureItems.length < failureTotal ? `（显示前 ${failureItems.length} 条）` : ""}</summary>
        <div class="job-failure-actions">${retry}</div>
        <ol>${failureItems.map((item, index) => {
          const recordId = esc(jobItemLocation(item, index));
          const stage = item?.stage ? `${item.stage}：` : "";
          return `<li><code>${recordId}</code><span>${esc(stage + jobErrorMessage(item?.error || item?.message || job.error))}</span></li>`;
        }).join("")}</ol>
      </details>`
    : "";
  const protectedItems = conflictItems.length
    ? `<details class="job-conflicts">
        <summary>查看 ${conflictTotal} 条保护明细${conflictItems.length < conflictTotal ? `（显示前 ${conflictItems.length} 条）` : ""}</summary>
        <ol>${conflictItems.map((item, index) => {
          const recordId = esc(jobItemLocation(item, index));
          return `<li><code>${recordId}</code><span>${esc(item?.message || "目标记录受保护，未覆盖")}</span></li>`;
        }).join("")}</ol>
      </details>`
    : "";
  const blockedDetails = blockedItems.length
    ? `<details class="job-conflicts">
        <summary>查看 ${blockedTotal} 条流程拦截明细${blockedItems.length < blockedTotal ? `（显示前 ${blockedItems.length} 条）` : ""}</summary>
        <ol>${blockedItems.map((item, index) => {
          const recordId = esc(jobItemLocation(item, index));
          const stage = item?.stage ? `${item.stage}：` : "";
          return `<li><code>${recordId}</code><span>${esc(stage + (item?.message || "当前记录未通过流程前置检查"))}</span></li>`;
        }).join("")}</ol>
      </details>`
    : "";
  return `<article class="job">
    <div class="job-header">
      <div>
        <strong class="job-title">${esc(jobLabel(job))}</strong>
        <div class="job-id">任务 ID · ${esc(job.id)}</div>
      </div>
      <div class="job-header-actions">
        ${jobOutcome(job, processed, failed, conflicts, blocked)}
        ${cancelControl}
      </div>
    </div>
    <div class="job-times">
      ${createdAt ? `<span>创建：${createdAt}</span>` : ""}
      ${finishedAt ? `<span>完成：${finishedAt}</span>` : ""}
    </div>
    ${targetLine}
    ${stats}
    ${failures}
    ${protectedItems}
    ${blockedDetails}
  </article>`;
}

async function retryJobFailures(jobId, button = null) {
  const oldText = button?.textContent || "";
  if (button) {
    button.disabled = true;
    button.textContent = "提交重试中";
  }
  try {
    const data = await api(`/api/jobs/${jobId}/retry-failures`, {
      method: "POST",
      body: "{}",
    });
    state.submittedJobIds.add(data.job_id);
    notify(`已重新提交 ${data.retry_count} 条失败记录：${data.job_id}`);
    await loadJobs();
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
    }
  }
}

async function cancelJob(jobId, button = null) {
  if (!window.confirm("确定停止这个批量任务吗？已完成的记录会保留，未开始的记录不会继续处理。")) return;
  if (button) {
    button.disabled = true;
    button.textContent = "停止中";
  }
  try {
    const data = await api(`/api/jobs/${jobId}/cancel`, {
      method: "POST",
      body: "{}",
    });
    notify(data.message || "已请求停止任务");
    await loadJobs();
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "停止任务";
    }
    throw error;
  }
}

function bindJobActions() {
  document.querySelectorAll("[data-action='retry-job-failures']").forEach((button) => {
    button.addEventListener("click", () => retryJobFailures(button.dataset.jobId, button));
  });
  document.querySelectorAll("[data-action='cancel-job']").forEach((button) => {
    button.addEventListener("click", () => cancelJob(button.dataset.jobId, button));
  });
}

function jobPage(job) {
  const action = String(job.action || "");
  if (action === "publish.sync_part_time" || String(job.name || "").startsWith("part-time-")) return "part-time";
  if (action.startsWith("doubao.") || action === "publish.sync_doubao" || String(job.name || "").startsWith("doubao-")) return "doubao";
  if (action.startsWith("task.") || action === "publish.sync") return "videos";
  if (action.startsWith("material.")) return "materials";
  return "";
}

function renderJobSummary(jobs) {
  const running = jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
  const failed = jobs.filter((job) => job.status === "failed" || Number(job.result?.failed || 0) > 0).length;
  const target = $("jobSummary");
  if (!target) return;
  target.textContent = `最近 ${Math.min(jobs.length, 20)} 条 · 处理中 ${running} · 有失败 ${failed}`;
}

function runPageDataLoader(page, options = {}) {
  if (page === "materials") return loadMaterials(options);
  if (page === "videos") return loadTasks(options);
  if (page === "doubao") return loadDoubaoItems(options);
  if (page === "part-time") return loadPartTimeItems(options);
  if (page === "prompts") return loadPrompts(options);
  return Promise.resolve();
}

function pageLoadKey(page) {
  if (page === "doubao") {
    const range = state.doubaoLoadedRange;
    return `${page}:${state.doubaoDateRange}:${state.doubaoSortOrder}:${range ? `${range.start}-${range.end}` : "all"}`;
  }
  if (page === "part-time") return `${page}:${state.partTimeDateRange}`;
  return page;
}

function loadPageData(page, options = {}) {
  const key = pageLoadKey(page);
  const existing = state.pageLoadPromises.get(key);
  if (existing) return existing;
  const promise = Promise.resolve(runPageDataLoader(page, options)).finally(() => {
    if (state.pageLoadPromises.get(key) === promise) state.pageLoadPromises.delete(key);
  });
  state.pageLoadPromises.set(key, promise);
  return promise;
}

async function refreshStaleActivePage() {
  const page = state.activePage;
  if (!state.stalePages.has(page) || !activePageLoaded(page)) return;
  try {
    await loadPageData(page, { force: true });
    state.stalePages.delete(page);
  } catch (error) {
    notify(`任务已完成，但网页刷新失败：${error.message || "请手动刷新表格"}`, "error");
  }
}

async function loadJobs() {
  const data = await api("/api/jobs?limit=30");
  const previousActiveJobIds = new Set(state.jobs
    .filter((job) => ["queued", "running"].includes(job.status))
    .map((job) => job.id));
  const completedJobs = data.data.filter((job) => !["queued", "running"].includes(job.status)
    && (previousActiveJobIds.has(job.id) || state.submittedJobIds.has(job.id)));
  completedJobs.forEach((job) => {
    state.submittedJobIds.delete(job.id);
    const page = jobPage(job);
    if (page) state.stalePages.add(page);
  });
  const nextActiveNames = data.data
    .filter((job) => job.status === "running")
    .map((job) => `${job.name}:${job.id}`)
    .join("|");
  const nextSnapshot = data.data.map((job) => [
    job.id,
    job.name,
    job.status,
    job.error || "",
    job.finished_at || "",
    job.result?.processed || "",
    job.result?.failed || "",
    job.outcome || "",
    job.target?.label || "",
  ].join(":")).join("|");
  const activeChanged = nextActiveNames !== state.lastActiveJobNames;
  state.jobs = data.data;
  state.lastActiveJobNames = nextActiveNames;
  if (nextSnapshot !== state.lastJobsSnapshot) {
    state.lastJobsSnapshot = nextSnapshot;
    const visibleJobs = data.data.slice(-20).reverse();
    $("jobs").innerHTML = visibleJobs.map(renderJob).join("");
    bindJobActions();
    renderJobSummary(visibleJobs);
  }
  await refreshStaleActivePage();
  if (!activeChanged) return;
  if (state.activePage === "materials" && activePageLoaded("materials")) renderMaterials();
  if (state.activePage === "videos" && activePageLoaded("videos")) renderTasks();
  if (state.activePage === "doubao" && activePageLoaded("doubao")) renderDoubaoItems();
}

async function loadLogs() {
  const data = await api("/api/logs?limit=80");
  const box = $("logs");
  const levelLabels = { info: "信息", success: "成功", error: "失败", warning: "警告" };
  const sourceLabels = { workflow: "业务处理", batch: "批量任务", audit: "操作审计", system: "系统" };
  const nextText = data.data.map((l) => `[${l.timestamp}] [${levelLabels[l.level] || l.level}] [${sourceLabels[l.source] || l.source}] ${l.message}`).join("\n");
  if (nextText === state.lastLogsSnapshot) return;
  const shouldStick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  state.lastLogsSnapshot = nextText;
  box.textContent = nextText;
  if (shouldStick) box.scrollTop = box.scrollHeight;
}

async function prepareMaterial(id) {
  const data = await api(`/api/materials/${id}/prepare`, { method: "POST", body: "{}" });
  notify(`已启动素材处理：${data.job_id}`);
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
    notify(`已启动${labels[step] || step}：${data.job_id}`);
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
    notify(`已启动失败步骤重试：${data.job_id}`);
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
    notify("请先勾选要处理的素材", "error");
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
    notify(`${labels[step] || step}已启动：${data.job_id}，将按顺序处理 ${ids.length} 条素材`);
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
    notify(`已启动视频生成：${data.job_id}`);
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
    notify(`已启动参考图入表：${data.job_id}`);
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
    notify(`已启动人工视频入库：${data.job_id}`);
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
  const name = `doubao-generate-prompt:${id}:${state.doubaoCountryCode}`;
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
    const data = await api(`/api/doubao-items/${id}/generate-prompt`, {
      method: "POST",
      body: JSON.stringify({
        country_code: state.doubaoCountryCode,
        prompt_config_id: $("doubaoPromptConfig")?.value || "",
      }),
    });
    notify(`已启动豆包视频提示词生成：${data.job_id}`);
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

async function generateDoubaoWhiteBackground(id, button = null) {
  const name = `doubao-generate-white-background:${id}`;
  if (isJobBusy(name) || state.pendingJobs.has(name)) return;
  if (!window.confirm("生成白底图会调用付费图片模型，成功后将直接替换当前商品图存储链接并自动复检人脸。是否继续？")) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  renderDoubaoItems();
  try {
    const data = await api(`/api/doubao-items/${id}/generate-white-background`, {
      method: "POST",
      body: "{}",
    });
    notify(`已启动付费白底图任务：${data.job_id}`);
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

async function syncDoubaoPartTime(id, button = null) {
  const name = `doubao-sync-part-time:${id}:${state.doubaoCountryCode}`;
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
    const data = await api(`/api/doubao-items/${id}/sync-part-time`, {
      method: "POST",
      body: JSON.stringify({ country_code: state.doubaoCountryCode }),
    });
    notify(`已启动兼职表同步：${data.job_id}`);
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
    notify(`已启动商品图转存：${data.job_id}`);
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
    notify("请先勾选要转存商品图的豆包记录", "error");
    return;
  }
  const rows = selectedRows.filter((row) => canTransferDoubaoImage(row));
  if (!rows.length) {
    notify("已勾选记录里没有待转存的商品图", "error");
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
    const data = await api("/api/doubao-items/batch-operation", {
      method: "POST",
      body: JSON.stringify({
        operation: "store_images",
        record_ids: rows.map((row) => row.record_id),
        row_numbers: doubaoRowNumberMap(rows),
        selection_label: doubaoSelectionLabel(rows),
      }),
    });
    notify(`已启动 ${rows.length} 条商品图批量转存：${batchJobSummary(data)}`);
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

async function batchDoubaoWorkflow(button = null) {
  const name = "doubao-batch-workflow";
  if (isJobBusy(name)) return;
  const rows = selectedDoubaoRows();
  if (!rows.length) {
    notify("请先勾选要按流程处理的豆包记录", "error");
    return;
  }
  const forceUpdate = Boolean($("forceDoubaoSyncUpdate")?.checked);
  const requireFaceDetection = Boolean($("requireDoubaoFaceDetection")?.checked);
  const repairFaces = requireFaceDetection && Boolean($("repairDoubaoFaces")?.checked);
  if (repairFaces && !window.confirm(`将对检测为含人脸或疑似人脸的记录调用付费白底图模型。当前选择 ${rows.length} 条，是否继续？`)) return;
  const oldText = button ? button.textContent : "";
  setPendingJob(name, true);
  if (button) {
    button.disabled = true;
    button.textContent = "提交中";
    button.classList.add("is-pending");
  }
  try {
    const data = await api("/api/doubao-items/batch-operation", {
      method: "POST",
      body: JSON.stringify({
        operation: "process_workflow",
        record_ids: rows.map((row) => row.record_id),
        row_numbers: doubaoRowNumberMap(rows),
        country_code: state.doubaoCountryCode,
        force_update: forceUpdate,
        detect_faces: requireFaceDetection,
        repair_faces: repairFaces,
        prompt_config_id: $("doubaoPromptConfig")?.value || "",
        selection_label: doubaoSelectionLabel(rows),
      }),
    });
    notify(`已启动 ${rows.length} 条按流程批量处理：${batchJobSummary(data)}，后台会从每条记录的当前步骤继续`);
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

async function batchDoubaoDetectFaces(button = null, explicitIds = null) {
  const name = "doubao-batch-detect-faces";
  if (isJobBusy(name)) return;
  const sourceRows = explicitIds
    ? state.doubaoItems.filter((row) => explicitIds.includes(row.record_id))
    : selectedDoubaoRows();
  if (!sourceRows.length) {
    notify("请先勾选要检测人脸的豆包记录", "error");
    return;
  }
  const externalCount = sourceRows.filter((row) => !hasDoubaoStoredImage(row)).length;
  const rows = sourceRows.filter((row) => canExecuteDoubaoFaceDetection(row));
  if (!rows.length) {
    notify(externalCount
      ? "所选图片尚未进入自有对象存储，请先执行步骤 1“批量转存”"
      : "所选记录里没有可检测的商品图", "error");
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
    const data = await api("/api/doubao-items/batch-operation", {
      method: "POST",
      body: JSON.stringify({
        operation: "detect_faces",
        record_ids: rows.map((row) => row.record_id),
        row_numbers: doubaoRowNumberMap(rows),
        selection_label: doubaoSelectionLabel(rows),
      }),
    });
    notify(`已启动 ${rows.length} 条人脸检测：${batchJobSummary(data)}`);
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

async function batchDoubaoPrompts(button = null) {
  const name = `doubao-batch-prompts:${state.doubaoCountryCode}`;
  if (isJobBusy(name)) return;
  const selectedRows = selectedDoubaoRows();
  if (!selectedRows.length) {
    notify("请先勾选要生成提示词的豆包记录", "error");
    return;
  }
  const rows = selectedRows.filter((row) => canExecuteDoubaoPrompt(row) && !isJobBusy(`doubao-generate-prompt:${row.record_id}:${state.doubaoCountryCode}`));
  if (!rows.length) {
    notify("没有可生成提示词的记录，请先完成步骤 1 转存和步骤 2 人脸检测", "error");
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
    const data = await api("/api/doubao-items/batch-operation", {
      method: "POST",
      body: JSON.stringify({
        operation: "generate_prompts",
        record_ids: rows.map((row) => row.record_id),
        row_numbers: doubaoRowNumberMap(rows),
        country_code: state.doubaoCountryCode,
        prompt_config_id: $("doubaoPromptConfig")?.value || "",
        selection_label: doubaoSelectionLabel(rows),
      }),
    });
    notify(`已启动 ${rows.length} 条豆包提示词生成：${batchJobSummary(data)}`);
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

async function batchDoubaoSyncPartTime(button = null) {
  const name = `doubao-batch-sync-part-time:${state.doubaoCountryCode}`;
  if (isJobBusy(name)) return;
  const selectedRows = selectedDoubaoRows();
  const promptRows = selectedRows.filter((row) => String(row["最终视频生成提示词"] || "").trim());
  const forceUpdate = Boolean($("forceDoubaoSyncUpdate")?.checked);
  const syncSummary = doubaoSyncSelectionSummary(selectedRows, forceUpdate);
  const rows = syncableDoubaoRows(selectedRows, forceUpdate);
  if (!rows.length) {
    const hasSyncedRows = syncableDoubaoRows(selectedRows, true).length > 0;
    const message = syncSummary.conflicts && syncSummary.conflicts === selectedRows.length
      ? `所选 ${syncSummary.conflicts} 条均为同步冲突：对应兼职任务已接单或完成，为保护现有任务不可覆盖`
      : !forceUpdate && hasSyncedRows
      ? "所选记录均已同步；如需覆盖，请勾选“强制更新已同步”"
      : promptRows.length
      ? `所选提示词不属于${selectedCountryName()}·${selectedCountryLanguageName()}，请先按当前国家语言重新生成`
      : "请先勾选已生成提示词的豆包记录";
    notify(message, "error");
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
    const data = await api("/api/doubao-items/batch-operation", {
      method: "POST",
      body: JSON.stringify({
        operation: "sync_part_time",
        record_ids: rows.map((row) => row.record_id),
        row_numbers: doubaoRowNumberMap(rows),
        country_code: state.doubaoCountryCode,
        force_update: forceUpdate,
        selection_label: doubaoSelectionLabel(rows),
      }),
    });
    const excluded = selectedRows.length - rows.length;
    notify(`已选 ${selectedRows.length} 条，本次提交 ${rows.length} 条${excluded ? `，排除 ${excluded} 条已同步/冲突/前置检查未通过记录` : ""}：${batchJobSummary(data)}`);
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

async function batchDoubaoSyncPublish(button = null) {
  const name = "doubao-batch-sync-publish";
  if (isJobBusy(name)) return;
  const rows = selectedDoubaoRows();
  if (!rows.length) {
    notify("请先勾选要同步到发布表的豆包素材记录", "error");
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
    const data = await api("/api/doubao-items/batch-operation", {
      method: "POST",
      body: JSON.stringify({
        operation: "sync_publish",
        record_ids: rows.map((row) => row.record_id),
        row_numbers: doubaoRowNumberMap(rows),
        selection_label: doubaoSelectionLabel(rows),
        limit: 200,
      }),
    });
    const readiness = data.readiness || {};
    const readySources = Number(readiness.ready_sources || 0);
    const eligibleVideos = Number(readiness.eligible_videos || 0);
    const pendingSources = Number(readiness.pending_sources || 0);
    notify(
      `已选 ${rows.length} 条素材，其中 ${readySources} 条有已完成兼职成片、共 ${eligibleVideos} 个有效视频`
      + `${pendingSources ? `；其余 ${pendingSources} 条会在运行记录中按行号说明原因` : ""}：${batchJobSummary(data)}`,
    );
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

async function batchPartTimeSyncPublish(button = null) {
  const name = "part-time-batch-sync-publish";
  if (isJobBusy(name)) return;
  const rows = selectedPartTimeRows().filter((row) => row["可同步发布"]);
  if (!rows.length) {
    notify("请先勾选待审核的兼职成片", "error");
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
    const data = await api("/api/part-time-items/batch-sync-publish", {
      method: "POST",
      body: JSON.stringify({
        record_ids: rows.map((row) => row.record_id),
        row_numbers: partTimeRowNumberMap(rows),
        selection_label: partTimeSelectionLabel(rows),
        limit: 200,
      }),
    });
    notify(`已完整提交 ${rows.length} 条成片审核发布任务：${batchJobSummary(data)}`);
    await loadJobs();
  } catch (error) {
    notify(error.message || "兼职成片同步发布失败", "error");
  } finally {
    setPendingJob(name, false);
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
      button.classList.remove("is-pending");
    }
  }
}

async function exportDoubaoBatchQueue(button = null) {
  const selectedRows = selectedDoubaoRows();
  const rows = selectedRows.slice(0, 200);
  if (!rows.length) {
    notify("请先勾选已完成转存、人脸检测和提示词生成的豆包素材", "error");
    return;
  }
  const oldText = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "正在打包";
    button.classList.add("is-pending");
  }
  try {
    const filename = await downloadApi("/api/doubao-items/export-batch-queue", {
      method: "POST",
      body: JSON.stringify({ record_ids: rows.map((row) => row.record_id) }),
    });
    const excluded = selectedRows.length - rows.length;
    notify(`已导出 ${rows.length} 条任务：${filename}${excluded ? `；另有 ${excluded} 条不满足条件或超过单批上限` : ""}`);
  } catch (error) {
    notify(error.message || "豆包素材批量队列导出失败", "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
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
    notify("当前选择里没有可同步的视频链接", "error");
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
    notify(`已启动发布表同步：${data.job_id}`);
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
    notify("当前选择里没有需要入表的参考图", "error");
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
    notify(`已启动批量参考图入表：${data.job_id}，将处理 ${rows.length} 条任务`);
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
    notify("当前选择里没有可入库的视频附件或视频链接", "error");
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
    notify(`已启动批量视频入库：${data.job_id}，将处理 ${rows.length} 条任务`);
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
  loadCountries().catch(() => {});
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
$("refreshPartTimeTable").onclick = () => refreshTable("refreshPartTimeTable", () => loadPartTimeItems({ force: true }));
$("refreshPromptsTable").onclick = () => refreshTable("refreshPromptsTable", () => loadPrompts({ force: true }));

async function loadActivePage(options = {}) {
  return loadPageData(state.activePage, options);
}

function switchPage(page) {
  state.activePage = page;
  renderPage();
  const force = state.stalePages.has(page);
  if (force || !activePageLoaded(page)) {
    loadActivePage({ force }).then(() => {
      state.stalePages.delete(page);
    }).catch((error) => notify(error.message || "页面数据刷新失败", "error"));
  }
}

$("pageRemix").onclick = () => switchPage(state.activePage === "videos" ? "videos" : "materials");
$("pageMaterials").onclick = () => switchPage("materials");
$("pageVideos").onclick = () => switchPage("videos");
$("pageDoubao").onclick = () => switchPage("doubao");
$("pagePartTime").onclick = () => switchPage("part-time");
$("pagePrompts").onclick = () => switchPage("prompts");
$("loadDoubao").onclick = () => {
  state.doubaoLoadedRange = null;
  loadDoubaoItems({ force: true, full: true }).catch((error) => notify(error.message, "error"));
};
$("loadPartTime").onclick = () => loadPartTimeItems({ force: true });
$("loadPrompts").onclick = () => loadPrompts({ force: true });
$("addPrompt").onclick = () => {
  state.editingPromptId = "";
  state.creatingPrompt = true;
  state.promptDraft = null;
  renderPromptEditor();
  $("promptEditor").scrollIntoView({ behavior: "smooth", block: "nearest" });
};
$("doubaoCountry").onchange = () => {
  state.doubaoCountryCode = $("doubaoCountry").value;
  localStorage.setItem("viral-remix-doubao-country", state.doubaoCountryCode);
  renderDoubaoItems();
  loadDoubaoPromptOptions().catch((error) => notify(error.message, "error"));
};
$("doubaoSortOrder").value = state.doubaoSortOrder;
$("doubaoSortOrder").onchange = () => {
  state.doubaoSortOrder = $("doubaoSortOrder").value === "oldest" ? "oldest" : "latest";
  localStorage.setItem("viral-remix-doubao-sort", state.doubaoSortOrder);
  state.doubaoPage = 1;
  state.doubaoItems.sort((left, right) => {
    const delta = rowNumber(left) - rowNumber(right);
    return state.doubaoSortOrder === "oldest" ? delta : -delta;
  });
  renderDoubaoItems();
};
$("doubaoDateRange").value = state.doubaoDateRange;
$("doubaoDateRange").onchange = () => {
  state.doubaoDateRange = normalizeDateRange($("doubaoDateRange").value);
  localStorage.setItem("viral-remix-doubao-date-range", state.doubaoDateRange);
  state.doubaoLoadedRange = null;
  state.doubaoPage = 1;
  state.selectedDoubaoIds.clear();
  state.loadedPages.delete("doubao");
  loadPageData("doubao").catch((error) => notify(error.message, "error"));
};
$("partTimeSortOrder").value = state.partTimeSortOrder;
$("partTimeSortOrder").onchange = () => {
  state.partTimeSortOrder = $("partTimeSortOrder").value === "oldest" ? "oldest" : "latest";
  localStorage.setItem("viral-remix-part-time-sort", state.partTimeSortOrder);
  state.partTimePage = 1;
  renderPartTimeItems();
};
$("partTimeDateRange").value = state.partTimeDateRange;
$("partTimeDateRange").onchange = () => {
  state.partTimeDateRange = normalizeDateRange($("partTimeDateRange").value);
  localStorage.setItem("viral-remix-part-time-date-range", state.partTimeDateRange);
  state.partTimePage = 1;
  state.selectedPartTimeIds.clear();
  state.loadedPages.delete("part-time");
  loadPageData("part-time").catch((error) => notify(error.message, "error"));
};
$("forceDoubaoSyncUpdate").onchange = renderDoubaoSelectionState;
$("requireDoubaoFaceDetection").onchange = () => {
  const repair = $("repairDoubaoFaces");
  repair.disabled = !$("requireDoubaoFaceDetection").checked;
  if (repair.disabled) repair.checked = false;
};
$("promptCountryFilter").onchange = () => {
  state.promptCountryFilter = $("promptCountryFilter").value;
  renderPrompts();
};
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
$("selectAllPartTime").onchange = () => {
  const ids = currentVisiblePartTimeRows()
    .filter((row) => row["可同步发布"])
    .map((row) => row.record_id);
  if ($("selectAllPartTime").checked) ids.forEach((id) => state.selectedPartTimeIds.add(id));
  else ids.forEach((id) => state.selectedPartTimeIds.delete(id));
  renderPartTimeItems();
};
$("selectPartTimeRange").onclick = selectPartTimeRange;
$("clearPartTimeSelection").onclick = clearPartTimeSelection;
["partTimeRangeStart", "partTimeRangeEnd"].forEach((id) => {
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter") selectPartTimeRange();
  });
});
$("selectDoubaoRange").onclick = () => selectDoubaoRange().catch((error) => notify(error.message, "error"));
$("clearDoubaoSelection").onclick = clearDoubaoSelection;
["doubaoRangeStart", "doubaoRangeEnd"].forEach((id) => {
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter") selectDoubaoRange().catch((error) => notify(error.message, "error"));
  });
});
$("batchAnalyze").onclick = () => runBatchMaterialStep("analyze-video", $("batchAnalyze"));
$("batchStoreImage").onclick = () => runBatchMaterialStep("store-image", $("batchStoreImage"));
$("batchGenerateImages").onclick = () => runBatchMaterialStep("generate-images", $("batchGenerateImages"));
$("batchCreateTasks").onclick = () => runBatchMaterialStep("create-tasks", $("batchCreateTasks"));
$("batchDoubaoStoreImages").onclick = () => batchDoubaoStoreImages($("batchDoubaoStoreImages"));
$("batchDoubaoWorkflow").onclick = () => batchDoubaoWorkflow($("batchDoubaoWorkflow"));
$("batchDoubaoDetectFaces").onclick = () => batchDoubaoDetectFaces($("batchDoubaoDetectFaces"));
$("batchDoubaoPrompts").onclick = () => batchDoubaoPrompts($("batchDoubaoPrompts"));
$("exportDoubaoBatchQueue").onclick = () => exportDoubaoBatchQueue($("exportDoubaoBatchQueue"));
$("batchDoubaoSyncPartTime").onclick = () => batchDoubaoSyncPartTime($("batchDoubaoSyncPartTime"));
$("batchDoubaoSyncPublish").onclick = () => batchDoubaoSyncPublish($("batchDoubaoSyncPublish"));
$("batchPartTimeSyncPublish").onclick = () => batchPartTimeSyncPublish($("batchPartTimeSyncPublish"));

$("batchVideos").onclick = async () => {
  const name = "batch-generate-videos";
  if (isJobBusy(name)) return;
  const button = $("batchVideos");
  const oldText = button.textContent;
  const selectedRows = selectedVisibleTaskRows();
  const sourceRows = selectedRows.length ? selectedRows : tasksForSelected();
  const rows = sourceRows.filter((row) => ["待生成视频", "内容已生成", ""].includes(row["生成状态"] || ""));
  if (!rows.length) {
    notify("当前选择里没有可生成的视频任务", "error");
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
    notify(`已启动批量视频生成：${data.job_id}`);
    await loadJobs();
  } finally {
    setPendingJob(name, false);
    button.disabled = false;
    button.textContent = oldText;
    button.classList.remove("is-pending");
  }
};

renderPage();
renderCountrySelect();
loadConfig();
loadCountries().catch(() => {});
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
