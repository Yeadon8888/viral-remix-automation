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
    "商品图可转存": false
  })`, context);

  assert.match(html, />对象存储<\/a>/);
  const promptButton = html.match(/<button[^>]*data-action="doubao-generate"[^>]*>[^<]*<\/button>/)?.[0] || "";
  assert.match(promptButton, />生成提示词<\/button>/);
  assert.doesNotMatch(promptButton, /\bdisabled\b/);
});

test("Doubao page names the batch action as object-storage transfer", () => {
  const root = path.join(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "public", "index.html"), "utf8");

  assert.match(html, /id="batchDoubaoStoreImages"[^>]*>批量转存到对象存储<\/button>/);
});

test("completed batch with item failures is shown as failed with its reason", () => {
  const context = loadApp();
  const html = vm.runInContext(`renderJob({
    id: "job-1",
    name: "doubao-batch-store-images",
    status: "done",
    result: {
      processed: 3,
      failed: 3,
      results: [{ error: "download failed: HTTP 403" }]
    }
  })`, context);

  assert.match(html, />转存失败 3\/3</);
  assert.match(html, /download failed: HTTP 403/);
  assert.doesNotMatch(html, /class="status done"/);
});
