const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync("public/index.html", "utf8");
const js = fs.readFileSync("public/js/app.js", "utf8");

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

test("part-time completed videos have an independent audit and publish page", () => {
  assert.match(html, /id="pagePartTime"/);
  assert.match(html, /id="partTimePage"/);
  assert.match(html, /兼职成片/);
  assert.match(html, /同步即视为审核通过/);
  assert.match(html, /id="batchPartTimeSyncPublish"/);
  assert.match(html, /兼职表真实行号/);
  assert.match(html, /id="batchDoubaoSyncPublish"/);
});

test("part-time page submits every selected record for server-side chunking", () => {
  const publishHandler = js.match(/async function batchPartTimeSyncPublish[\s\S]*?async function exportDoubaoBatchQueue/)?.[0] || "";

  assert.match(js, /\/api\/part-time-items/);
  assert.doesNotMatch(js, /\/api\/part-time-items\?limit=500/);
  assert.match(js, /publish\.sync_part_time/);
  assert.match(publishHandler, /record_ids:\s*rows\.map\(\(row\) => row\.record_id\)/);
  assert.match(publishHandler, /row_numbers:\s*partTimeRowNumberMap\(rows\)/);
  assert.doesNotMatch(publishHandler, /\.slice\(0,\s*200\)/);
  assert.match(publishHandler, /batchJobSummary\(data\)/);
  assert.match(js, /function batchDoubaoSyncPublish/);
});

test("part-time completed videos only handle review and publish", () => {
  assert.doesNotMatch(html, /id="exportPartTimeBatchQueue"/);
  assert.doesNotMatch(js, /\/api\/part-time-items\/export-batch-queue/);
  assert.doesNotMatch(js, /function exportPartTimeBatchQueue/);
});

test("part-time publish rows can be selected by real Feishu row range", () => {
  assert.match(html, /id="partTimeRangeStart"/);
  assert.match(html, /id="partTimeRangeEnd"/);
  assert.match(html, /id="selectPartTimeRange"/);
  assert.match(html, /id="clearPartTimeSelection"/);

  const context = loadApp();
  const selectedIds = vm.runInContext(`partTimeRowsInRange([
    { record_id: "before", "行号": 4120, "可同步发布": true },
    { record_id: "ready-1", "行号": 4121, "可同步发布": true },
    { record_id: "blocked", "行号": 4300, "可同步发布": false },
    { record_id: "ready-2", "行号": 4655, "可同步发布": true },
    { record_id: "after", "行号": 4656, "可同步发布": true }
  ], 4655, 4121).map((row) => row.record_id)`, context);

  assert.deepEqual([...selectedIds], ["ready-1", "ready-2"]);
});

test("part-time completed videos can be sorted by real Feishu row number", () => {
  assert.match(html, /id="partTimeSortOrder"/);
  assert.match(html, /value="latest">行号倒序/);
  assert.match(html, /value="oldest">行号正序/);

  const context = loadApp();
  const descending = vm.runInContext(`
    state.partTimeSortOrder = "latest";
    state.partTimeItems = [
      { record_id: "row-12", "行号": 12 },
      { record_id: "row-30", "行号": 30 },
      { record_id: "row-21", "行号": 21 }
    ];
    sortedPartTimeItems().map((row) => row.record_id)
  `, context);
  assert.deepEqual([...descending], ["row-30", "row-21", "row-12"]);

  const ascending = vm.runInContext(`
    state.partTimeSortOrder = "oldest";
    sortedPartTimeItems().map((row) => row.record_id)
  `, context);
  assert.deepEqual([...ascending], ["row-12", "row-21", "row-30"]);
});

test("part-time page defaults to the latest two calendar days", () => {
  assert.match(html, /id="partTimeDateRange"/);
  assert.match(html, /value="2" selected>最近 2 天/);
  assert.match(js, /\/api\/part-time-items\?days=/);
});
