const state = {
  token: localStorage.getItem("viral-remix-admin-token") || "",
  materials: [],
  tasks: [],
  doubaoItems: [],
  prompts: [],
  jobs: [],
  pendingJobs: new Set(),
  editingPromptId: "",
  selectedMaterialId: localStorage.getItem("viral-remix-selected-material") || "",
  selectedMaterialIds: new Set(),
  selectedTaskIds: new Set(),
  activePage: localStorage.getItem("viral-remix-active-page") || "materials",
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

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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

function percentValue(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(n <= 1 ? Math.round(n * 100) : Math.round(n), 100));
}

function renderProgress(value) {
  const pct = percentValue(value);
  return `<div class="progress-cell">
    <div class="progress-track"><span style="width:${pct}%"></span></div>
    <strong>${pct}%</strong>
  </div>`;
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
  const ids = state.materials.map((row) => row.record_id).filter(Boolean);
  const selectedCount = ids.filter((id) => state.selectedMaterialIds.has(id)).length;
  $("selectedMaterialCount").textContent = `已选 ${selectedCount} 条`;
  $("selectAllMaterials").checked = ids.length > 0 && selectedCount === ids.length;
  $("selectAllMaterials").indeterminate = selectedCount > 0 && selectedCount < ids.length;
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
  const rows = tasksForSelected();
  const ids = rows.map((row) => row.record_id).filter(Boolean);
  const selectedCount = ids.filter((id) => state.selectedTaskIds.has(id)).length;
  $("selectedTaskCount").textContent = `已选 ${selectedCount} 条`;
  $("selectAllTasks").checked = ids.length > 0 && selectedCount === ids.length;
  $("selectAllTasks").indeterminate = selectedCount > 0 && selectedCount < ids.length;
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

function renderPromptDetail(label, text) {
  const body = String(text || "").trim();
  if (!body) return "";
  return `<details class="prompt-preview wide"><summary>${esc(label)}</summary><p>${esc(body)}</p></details>`;
}

function renderScriptStep(row) {
  const prompt = String(row["视频生成Prompt"] || "").trim();
  const title = String(row["生成标题"] || "").trim();
  const caption = String(row["生成文案"] || "").trim();
  const keywords = String(row["关键词"] || "").trim();
  const analysisTemplate = String(row["视频拆解提示词模板"] || "").trim();
  const analysisPrompt = String(row["视频拆解实际提示词"] || "").trim();
  return `<div class="step-cell">
    ${prompt ? status("已生成Prompt") : status("未生成")}
    ${title ? `<div class="publish-chip">${esc(title)}</div>` : ""}
    ${prompt ? `<details class="prompt-preview wide"><summary>视频生成Prompt/发布内容</summary>
      ${title ? `<strong>${esc(title)}</strong>` : ""}
      ${caption ? `<p>${esc(caption)}</p>` : ""}
      ${keywords ? `<p class="muted">${esc(keywords)}</p>` : ""}
      <p>${esc(prompt)}</p>
    </details>` : ""}
    ${renderPromptDetail("视频拆解覆盖模板", analysisTemplate)}
    ${renderPromptDetail("视频拆解实际提示词", analysisPrompt)}
    ${stepActionButton("analyze-video", row.record_id, prompt ? "重新生成" : "生成Prompt")}
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
    ${renderPromptDetail("商品组图覆盖模板", imageTemplate)}
    ${renderPromptDetail("商品组图实际提示词", imagePrompt)}
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

function renderMaterials() {
  renderMetrics();
  if (!state.materials.length) {
    $("materials").innerHTML = `<div class="selected-box">暂无素材</div>`;
    return;
  }

  $("materials").innerHTML = `<table><thead><tr>
    <th class="select-col"></th><th class="row-no">行</th><th>爆款视频</th><th>1 视频Prompt</th><th>2 主图入库</th><th>3 商品组图</th><th>4 视频任务</th><th>状态</th>
  </tr></thead><tbody>${state.materials.map((row) => {
    const videoUrl = row["爆款视频链接"];
    const selected = row.record_id === state.selectedMaterialId ? " selected" : "";
    const checked = state.selectedMaterialIds.has(row.record_id) ? "checked" : "";
    return `<tr class="selectable${selected}" data-material-id="${esc(row.record_id)}">
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
      <td>${status(row["自动化状态"])}</td>
    </tr>`;
  }).join("")}</tbody></table>`;

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
  document.querySelectorAll("[data-action='save-templates']").forEach((button) => {
    button.addEventListener("click", () => saveTemplates(button.dataset.id, button));
  });
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

function renderTasks() {
  const rows = tasksForSelected();
  renderSelectedMaterial();
  if (!rows.length) {
    $("tasks").innerHTML = `<div class="selected-box">暂无视频生成任务</div>`;
    renderTaskSelectionState();
    return;
  }

  $("tasks").innerHTML = `<table><thead><tr>
    <th class="select-col"></th><th class="row-no">行</th><th>任务</th><th>状态</th><th>进度</th><th>发布内容</th><th>参考图</th><th>视频</th><th>操作</th>
  </tr></thead><tbody>${rows.map((row) => {
    const checked = state.selectedTaskIds.has(row.record_id) ? "checked" : "";
    return `<tr>
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
    <td>${row["首帧/参考图链接"] ? renderThumbs([row["首帧/参考图链接"]]) : `<span class="muted">无</span>`}</td>
    <td>${row["生成视频链接"] ? `<a href="${esc(row["生成视频链接"])}" target="_blank">打开视频</a>` : esc(row["失败原因"] || "")}</td>
    <td><button class="mini${state.pendingJobs.has(`generate-video:${row.record_id}`) ? " is-pending" : ""}" data-action="video" data-id="${esc(row.record_id)}" ${isJobBusy(`generate-video:${row.record_id}`) ? "disabled" : ""}>${state.pendingJobs.has(`generate-video:${row.record_id}`) ? "提交中" : isJobBusy(`generate-video:${row.record_id}`) ? "进行中" : "生成视频"}</button></td>
  </tr>`;
  }).join("")}</tbody></table>`;

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

function renderDoubaoItems() {
  renderDoubaoMetrics();
  if (!state.doubaoItems.length) {
    $("doubaoItems").innerHTML = `<div class="selected-box">暂无豆包视频制作记录</div>`;
    return;
  }
  $("doubaoItems").innerHTML = `<table><thead><tr>
    <th>商品</th><th>图片</th><th>对象存储</th><th>最终视频生成提示词</th><th>发布内容</th><th>视频链接</th><th>操作</th>
  </tr></thead><tbody>${state.doubaoItems.map((row) => {
    const prompt = String(row["最终视频生成提示词"] || "").trim();
    const promptName = `doubao-generate-prompt:${row.record_id}`;
    const storeName = `doubao-store-image:${row.record_id}`;
    const promptPending = state.pendingJobs.has(promptName);
    const storePending = state.pendingJobs.has(storeName);
    const promptRunning = isJobBusy(promptName);
    const storeRunning = isJobBusy(storeName);
    return `<tr>
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
      <td>${row["视频生成链接"] ? `<a href="${esc(row["视频生成链接"])}" target="_blank">打开视频</a>` : `<span class="muted">未填写</span>`}</td>
      <td><button class="mini${promptPending ? " is-pending" : ""}" data-action="doubao-generate" data-id="${esc(row.record_id)}" ${promptRunning || !hasDoubaoImage(row) ? "disabled" : ""}>${promptPending ? "提交中" : promptRunning ? "进行中" : prompt ? "重新生成" : "生成提示词"}</button></td>
    </tr>`;
  }).join("")}</tbody></table>`;

  document.querySelectorAll("[data-action='doubao-store']").forEach((button) => {
    button.addEventListener("click", () => storeDoubaoImage(button.dataset.id, button));
  });
  document.querySelectorAll("[data-action='doubao-generate']").forEach((button) => {
    button.addEventListener("click", () => generateDoubaoPrompt(button.dataset.id, button));
  });
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
  state.selectedTaskIds.clear();
  localStorage.setItem("viral-remix-selected-material", state.selectedMaterialId);
  renderMaterials();
  renderTasks();
}

async function loadMaterials() {
  const data = await api("/api/materials?limit=100");
  state.materials = data.data;
  if (state.selectedMaterialId && !state.materials.some((row) => row.record_id === state.selectedMaterialId)) {
    state.selectedMaterialId = "";
  }
  const existingIds = new Set(state.materials.map((row) => row.record_id));
  state.selectedMaterialIds = new Set([...state.selectedMaterialIds].filter((id) => existingIds.has(id)));
  renderMaterials();
  renderTasks();
  markTableLoaded("materials", "素材表", state.materials.length);
}

async function loadTasks() {
  const data = await api("/api/tasks?limit=200");
  state.tasks = data.data;
  const existingIds = new Set(state.tasks.map((row) => row.record_id));
  state.selectedTaskIds = new Set([...state.selectedTaskIds].filter((id) => existingIds.has(id)));
  renderTasks();
  markTableLoaded("tasks", "任务表", state.tasks.length);
}

async function loadDoubaoItems() {
  const data = await api("/api/doubao-items?limit=200");
  state.doubaoItems = data.data;
  renderDoubaoItems();
  markTableLoaded("doubao", "豆包表", state.doubaoItems.length);
}

async function loadPrompts() {
  const data = await api("/api/prompts");
  state.prompts = data.data;
  if (state.editingPromptId && !state.prompts.some((row) => row.record_id === state.editingPromptId)) {
    state.editingPromptId = "";
  }
  renderPrompts();
  markTableLoaded("prompts", "Prompt表", state.prompts.length);
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
  state.jobs = data.data;
  $("jobs").innerHTML = data.data.slice().reverse().map((job) => `<div class="job">
    <div><strong>${esc(job.name)}</strong><div class="muted">${esc(job.id)}</div></div>
    <div>${status(job.status)}</div>
  </div>`).join("");
  renderMaterials();
  renderTasks();
  renderDoubaoItems();
}

async function loadLogs() {
  const data = await api("/api/logs?limit=200");
  const box = $("logs");
  const shouldStick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  box.textContent = data.data.map((l) => `[${l.timestamp}] [${l.level}] [${l.source}] ${l.message}`).join("\n");
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
  const rows = state.doubaoItems.filter((row) => hasDoubaoImage(row) && !hasDoubaoStoredImage(row));
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
  const rows = state.doubaoItems.filter((row) => hasDoubaoImage(row) && !isJobBusy(`doubao-generate-prompt:${row.record_id}`));
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
  loadMaterials().catch(() => {});
  loadTasks().catch(() => {});
};

$("refreshConfig").onclick = loadConfig;
$("loadMaterials").onclick = loadMaterials;
$("loadTasks").onclick = loadTasks;
$("syncPublish").onclick = () => syncPublishTasks($("syncPublish"));
$("loadJobs").onclick = loadJobs;
$("loadLogs").onclick = loadLogs;
$("refreshMaterialsTable").onclick = () => refreshTable("refreshMaterialsTable", loadMaterials);
$("refreshTasksTable").onclick = () => refreshTable("refreshTasksTable", loadTasks);
$("refreshDoubaoTable").onclick = () => refreshTable("refreshDoubaoTable", loadDoubaoItems);
$("refreshPromptsTable").onclick = () => refreshTable("refreshPromptsTable", loadPrompts);
$("pageMaterials").onclick = () => { state.activePage = "materials"; renderPage(); };
$("pageVideos").onclick = () => { state.activePage = "videos"; renderPage(); loadTasks().catch(() => {}); };
$("pageDoubao").onclick = () => { state.activePage = "doubao"; renderPage(); loadDoubaoItems().catch(() => {}); };
$("pagePrompts").onclick = () => { state.activePage = "prompts"; renderPage(); loadPrompts().catch(() => {}); };
$("loadDoubao").onclick = loadDoubaoItems;
$("loadPrompts").onclick = loadPrompts;
$("selectAllMaterials").onchange = () => {
  const ids = state.materials.map((row) => row.record_id).filter(Boolean);
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
  const ids = tasksForSelected().map((row) => row.record_id).filter(Boolean);
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
loadMaterials().catch(() => {});
loadTasks().catch(() => {});
loadDoubaoItems().catch(() => {});
loadPrompts().catch(() => {});
loadJobs().catch(() => {});
loadLogs().catch(() => {});
setInterval(() => {
  loadJobs().catch(() => {});
  loadLogs().catch(() => {});
  loadTasks().catch(() => {});
  loadDoubaoItems().catch(() => {});
}, 8000);
