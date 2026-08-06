const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("Doubao UI uses one country selector and backend batch endpoints", () => {
  const root = path.join(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "public", "index.html"), "utf8");
  const js = fs.readFileSync(path.join(root, "public", "js", "app.js"), "utf8");

  assert.match(html, /id="doubaoCountry"/);
  assert.match(html, /id="batchDoubaoSyncPartTime"/);
  assert.match(html, /id="exportDoubaoBatchQueue"[^>]*>导出批量队列 ZIP<\/button>/);
  assert.match(html, /id="batchDoubaoWorkflow"[^>]*>按流程批量处理<\/button>/);
  assert.match(html, /id="requireDoubaoFaceDetection"/);
  assert.match(html, /id="repairDoubaoFaces"[^>]*disabled/);
  assert.match(html, /id="batchDoubaoSyncPublish"/);
  assert.match(js, /\/api\/doubao-items\/batch-operation/);
  assert.match(js, /\/api\/doubao-items\/export-batch-queue/);
  assert.match(js, /async function exportDoubaoBatchQueue/);
  assert.match(js, /operation: "process_workflow"/);
  assert.match(js, /detect_faces: requireFaceDetection/);
  assert.match(js, /repair_faces: repairFaces/);
  assert.match(js, /function batchJobSummary\(data\)/);
  assert.match(js, /Array\.isArray\(data\.job_ids\)/);
  assert.match(js, /jobIds\.length > 1/);
  assert.match(js, /operation: "detect_faces"/);
  assert.match(js, /operation: "generate_prompts"/);
  assert.match(js, /operation: "sync_part_time"/);
  assert.match(js, /operation: "sync_publish"/);
  assert.match(js, /notify\(error\.message, "error"\)/);
  assert.match(js, /country_code/);
  assert.match(js, /"revision": row\.revision/);
  assert.doesNotMatch(js, /\{ value: "墨西哥", label: "墨西哥" \}/);
  assert.doesNotMatch(html, /连接本地Worker|更新本地Worker|打开 127\.0\.0\.1:5177/);
});

test("main navigation separates material processing from part-time review", () => {
  const root = path.join(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "public", "index.html"), "utf8");
  const js = fs.readFileSync(path.join(root, "public", "js", "app.js"), "utf8");

  assert.match(html, /短视频带货全链路自动化工作后台/);
  assert.match(html, /data-page="remix"[\s\S]*?<strong>视频复刻<\/strong>/);
  assert.match(html, /data-page="doubao"[\s\S]*?<strong>豆包视频素材表<\/strong>/);
  assert.match(html, /data-page="part-time"[\s\S]*?<strong>兼职成片<\/strong>/);
  assert.match(html, /data-page="prompts"[\s\S]*?<strong>Prompt配置<\/strong>/);
  assert.match(html, /id="addPrompt"[\s\S]*?>新增提示词<\/button>/);
  assert.match(js, /\/api\/prompts" :[\s\S]*?method: isCreating \? "POST"/);
  assert.match(js, /editPromptSchemeKey/);
  assert.match(js, /editPromptExecutionMode/);
  assert.match(js, /data-action="clone-prompt"/);
  assert.match(js, /Prompt 版本已锁定/);
  assert.doesNotMatch(html, /视频拆解 -&gt;|视频拆解 -> 主图入库/);
});
