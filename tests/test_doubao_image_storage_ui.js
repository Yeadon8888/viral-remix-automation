const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadApp() {
  const root = path.join(__dirname, "..");
  const source = fs.readFileSync(path.join(root, "public", "js", "app.js"), "utf8")
    .split('$("adminToken").value = state.token;')[0];
  const element = {
    addEventListener() {},
    classList: { add() {}, remove() {}, toggle() {} },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    dataset: {},
    disabled: false,
    hidden: false,
    innerHTML: "",
    textContent: "",
    value: "",
  };
  const context = vm.createContext({
    URLSearchParams,
    alert() {},
    console,
    document: {
      getElementById() { return element; },
      querySelectorAll() { return []; },
    },
    fetch: async () => ({ ok: true, json: async () => ({ success: true }) }),
    localStorage: { getItem() { return ""; }, setItem() {} },
    setTimeout,
  });
  vm.runInContext(source, context);
  return context;
}

test("external image URL is shown as waiting for object-storage transfer", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderDoubaoRow({
    record_id: "record-1",
    "行号": 1,
    "商品名称": "Test product",
    "商品图存储链接": "https://img.kalocdn.com/product/cover.png",
    "商品图已入库": false,
    "商品图可转存": true
  })`, context);

  assert.match(html, /外链待转存/);
  assert.match(html, />立即转存<\/button>/);
  assert.doesNotMatch(html, />对象存储<\/a>/);
});

test("managed image remains available for downstream prompt generation", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderDoubaoRow({
    record_id: "record-2",
    "行号": 2,
    "商品名称": "Stored product",
    "商品图存储链接": "https://vc-upload.example.com/product/cover.png",
    "商品图已入库": true,
    "商品图可转存": false,
    "人脸检测状态": "无人脸",
    "人脸检测通过": true,
    "人脸可检测": true,
    "可生成提示词": true
  })`, context);

  assert.match(html, />对象存储<\/a>/);
  const promptButton = html.match(/<button[^>]*data-action="doubao-generate"[^>]*>[^<]*<\/button>/)?.[0] || "";
  assert.match(promptButton, />生成提示词<\/button>/);
  assert.doesNotMatch(promptButton, /\bdisabled\b/);
});

test("synced material locks upstream workflow buttons", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderDoubaoRow({
    record_id: "record-locked",
    "行号": 9,
    "商品名称": "Locked product",
    "商品图存储链接": "https://vc-upload.example.com/product/cover.png",
    "商品图已入库": true,
    "人脸检测状态": "无人脸",
    "人脸检测通过": true,
    "人脸可检测": true,
    "可生成提示词": true,
    "发布前置条件通过": true,
    "可同步兼职": true,
    "可执行人脸检测": false,
    "可执行生成提示词": false,
    "可执行同步兼职": false,
    "流程阶段": "已同步锁定",
    "流程已锁定": true,
    "流程锁定原因": "已同步到兼职表，禁止回退修改上游内容",
    "最终视频生成提示词": "prompt",
    "目标国家": "马来西亚",
    "目标语言": "马来语",
    "兼职同步状态": "已同步"
  })`, context);

  assert.match(html, /已同步锁定/);
  assert.match(html, /已同步到兼职表，禁止回退修改上游内容/);
  const faceButton = html.match(/<button[^>]*data-action="doubao-detect-face"[^>]*>[^<]*<\/button>/)?.[0] || "";
  const promptButton = html.match(/<button[^>]*data-action="doubao-generate"[^>]*>[^<]*<\/button>/)?.[0] || "";
  const syncButton = html.match(/<button[^>]*data-action="doubao-sync"[^>]*>[^<]*<\/button>/)?.[0] || "";
  assert.match(faceButton, /\bdisabled\b/);
  assert.match(promptButton, /\bdisabled\b/);
  assert.match(syncButton, /\bdisabled\b/);
});

test("part-time sync button explains the exact publish prerequisite blocker", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderDoubaoRow({
    record_id: "record-missing-product-id",
    "行号": 3,
    "商品名称": "Missing product ID",
    "商品图存储链接": "https://vc-upload.example.com/product/cover.png",
    "商品图已入库": true,
    "人脸检测状态": "无人脸",
    "人脸检测通过": true,
    "人脸可检测": true,
    "可同步兼职": false,
    "素材阻塞原因": "缺少商品 ID，不能同步到兼职表",
    "目标国家": "马来西亚",
    "目标语言": "马来语"
  })`, context);

  const syncButton = html.match(/<button[^>]*data-action="doubao-sync"[^>]*>[^<]*<\/button>/)?.[0] || "";
  assert.match(syncButton, /\bdisabled\b/);
  assert.match(syncButton, /title="缺少商品 ID，不能同步到兼职表"/);
  assert.match(html, /缺少商品 ID，不能同步到兼职表/);
});

test("Doubao page removes destructive flagged-record actions", () => {
  const root = path.join(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "public", "index.html"), "utf8");

  assert.match(html, /id="batchDoubaoStoreImages"[^>]*>批量转存<\/button>/);
  assert.match(html, /id="batchDoubaoDetectFaces"/);
  assert.doesNotMatch(html, /id="batchDoubaoDeleteFaceRecords"/);
  assert.match(html, /id="batchDoubaoSyncPublish"[^>]*>同步到发布表<\/button>/);
  assert.match(html, /不包含在“按流程批量处理”中/);
});

test("flagged face action offers paid white-background repair instead of deletion", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderDoubaoRow({
    record_id: "record-face",
    "行号": 3,
    "商品名称": "Flagged product",
    "商品图存储链接": "https://vc-upload.example.com/product/face.png",
    "商品图已入库": true,
    "人脸检测状态": "含人脸",
    "可生成白底图": true
  })`, context);

  assert.match(html, /data-action="doubao-generate-white-background"/);
  assert.match(html, />生成白底图<\/button>/);
  assert.doesNotMatch(html, /data-action="doubao-delete-face-record"/);
  assert.doesNotMatch(html, />删除记录<\/button>/);
  const source = fs.readFileSync("public/js/app.js", "utf8");
  assert.match(source, /generateDoubaoWhiteBackground/);
  assert.match(source, /generate-white-background/);
  assert.match(source, /付费图片模型/);
});

test("completed batch with item failures is shown as failed with its reason", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderJob({
    id: "job-1",
    name: "doubao-batch-store-images",
    action: "doubao.batch_store_images",
    status: "done",
    created_at: 1784941200000,
    finished_at: 1784941265000,
    outcome: "partial_failed",
    target: {
      label: "豆包素材表 4932–5250",
      record_count: 3,
      country_code: "PH"
    },
    result: {
      processed: 3,
      failed: 1,
      failures: [{
        record_id: "record-3",
        row_number: 4934,
        source_table: "豆包素材表",
        error: "download failed: HTTP 403"
      }],
      failure_items_total: 1
    }
  })`, context);

  assert.match(html, /批量转存商品图/);
  assert.match(html, /处理对象：豆包素材表 4932–5250/);
  assert.match(html, /菲律宾/);
  assert.match(html, /成功 2 条/);
  assert.match(html, /失败 1 条/);
  assert.match(html, /2026-07-25 09:00:00/);
  assert.match(html, /查看 1 条失败明细/);
  assert.match(html, /豆包素材表第 4934 行/);
  assert.match(html, /record-3/);
  assert.match(html, /源图片拒绝访问（HTTP 403）/);
  assert.match(html, /data-action="retry-job-failures"/);
  assert.match(html, />重试失败 1 条<\/button>/);
  assert.doesNotMatch(html, /class="status done"/);
});

test("Doubao publish action submits selected source rows with line-number context", () => {
  const source = fs.readFileSync("public/js/app.js", "utf8");

  assert.match(source, /async function batchDoubaoSyncPublish/);
  assert.match(source, /operation:\s*"sync_publish"/);
  assert.match(source, /row_numbers:\s*doubaoRowNumberMap\(rows\)/);
});

test("full material workflow never includes publish-table synchronization", () => {
  const source = fs.readFileSync("public/js/app.js", "utf8");
  const workflow = source.match(/async function batchDoubaoWorkflow[\s\S]*?async function batchDoubaoDetectFaces/)?.[0] || "";

  assert.match(workflow, /operation:\s*"process_workflow"/);
  assert.doesNotMatch(workflow, /sync_publish|batchDoubaoSyncPublish/);
});

test("job retry calls the failed-record retry endpoint", () => {
  const source = fs.readFileSync("public/js/app.js", "utf8");
  assert.match(source, /\/api\/jobs\/\$\{jobId\}\/retry-failures/);
});

test("protected sync conflicts are separated from retryable failures", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderJob({
    id: "job-protected",
    name: "doubao-batch-sync-part-time",
    action: "doubao.batch_sync_part_time",
    status: "done",
    outcome: "protected",
    result: {
      processed: 2,
      failed: 0,
      conflicts: 2,
      conflict_items: [
        { record_id: "record-1", status: "conflict", message: "目标兼职任务状态为已完成，已保护现有任务，未覆盖" }
      ],
      conflict_items_total: 2
    }
  })`, context);

  assert.match(html, /保护跳过 2 条/);
  assert.match(html, /查看 2 条保护明细/);
  assert.doesNotMatch(html, /重试失败/);
});

test("full workflow job separates business blockers from retryable failures", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderJob({
    id: "job-workflow",
    name: "doubao-batch-workflow",
    action: "doubao.batch_workflow",
    status: "done",
    result: {
      processed: 3,
      failed: 1,
      blocked: 1,
      failures: [
        { record_id: "record-failed", status: "failed", stage: "图片转存", error: "HTTP 403" }
      ],
      failure_items_total: 1,
      blocked_items: [
        { record_id: "record-blocked", status: "blocked", stage: "人脸审核拦截", message: "商品图包含人脸" }
      ],
      blocked_items_total: 1
    }
  })`, context);

  assert.match(html, /按流程批量处理豆包素材/);
  assert.match(html, /成功 1 条/);
  assert.match(html, /流程拦截 1 条/);
  assert.match(html, /图片转存：源图片拒绝访问/);
  assert.match(html, /人脸审核拦截：商品图包含人脸/);
  assert.match(html, /重试失败 1 条/);
});

test("part-time batch skips synced rows unless force update is enabled", () => {
  const context = loadApp();
  const defaultCount = vm.runInContext(`syncableDoubaoRows([
    {
      record_id: "record-new",
      "可同步兼职": true,
      "最终视频生成提示词": "prompt",
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "未同步"
    },
    {
      record_id: "record-synced",
      "可同步兼职": true,
      "最终视频生成提示词": "prompt",
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "已同步"
    }
  ]).length`, context);
  const forcedCount = vm.runInContext(`syncableDoubaoRows([
    {
      record_id: "record-new",
      "可同步兼职": true,
      "最终视频生成提示词": "prompt",
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "未同步"
    },
    {
      record_id: "record-synced",
      "可同步兼职": true,
      "最终视频生成提示词": "prompt",
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "已同步"
    }
  ], true).length`, context);

  assert.equal(defaultCount, 1);
  assert.equal(forcedCount, 2);
});

test("protected part-time conflicts are excluded and selection counts explain the difference", () => {
  const context = loadApp();
  const summary = vm.runInContext(`doubaoSyncSelectionSummary([
    {
      record_id: "record-ready",
      "可同步兼职": true,
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "未同步"
    },
    {
      record_id: "record-synced",
      "可同步兼职": true,
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "已同步"
    },
    {
      record_id: "record-conflict",
      "可同步兼职": true,
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "同步冲突"
    },
    {
      record_id: "record-other-country",
      "可同步兼职": true,
      "目标国家": "菲律宾",
      "目标语言": "菲律宾语",
      "兼职同步状态": "未同步"
    }
  ], false)`, context);
  const forcedIds = vm.runInContext(`syncableDoubaoRows([
    {
      record_id: "record-synced",
      "可同步兼职": true,
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "已同步"
    },
    {
      record_id: "record-conflict",
      "可同步兼职": true,
      "目标国家": "马来西亚",
      "目标语言": "马来语",
      "兼职同步状态": "同步冲突"
    }
  ], true).map((row) => row.record_id)`, context);

  assert.deepEqual(
    JSON.parse(JSON.stringify(summary)),
    { selected: 4, submitted: 1, synced: 1, conflicts: 1, unavailable: 1 },
  );
  assert.deepEqual([...forcedIds], ["record-synced"]);
});

test("batch jobs expose a real stop action and stopped status", () => {
  const source = fs.readFileSync("public/js/app.js", "utf8");
  assert.match(source, /\/api\/jobs\/\$\{jobId\}\/cancel/);
  assert.match(source, /data-action="cancel-job"/);
  assert.match(source, />\$\{job\.cancel_requested \? "停止中" : "停止任务"\}<\/button>/);
  assert.match(source, />停止中<\/span>/);
  assert.match(source, />已停止<\/span>/);
  assert.match(source, /未处理 \$\{cancelled\} 条/);
});
