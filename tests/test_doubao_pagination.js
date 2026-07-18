const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadApp(fetchImpl) {
  const appPath = path.join(__dirname, "..", "public", "js", "app.js");
  const source = fs.readFileSync(appPath, "utf8").split('$("adminToken").value = state.token;')[0];
  const elements = {};
  function element(id) {
    if (!elements[id]) {
      elements[id] = {
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
    }
    return elements[id];
  }
  const context = vm.createContext({
    __elements: elements,
    URLSearchParams,
    alert() {},
    console,
    document: {
      getElementById(id) { return element(id); },
      querySelectorAll() { return []; },
    },
    fetch: fetchImpl,
    localStorage: { getItem() { return ""; }, setItem() {} },
    setTimeout,
  });
  vm.runInContext(source, context);
  return context;
}

test("Doubao selection shows selected and currently syncable counts", () => {
  const context = loadApp(async () => ({ ok: true, json: async () => ({ success: true }) }));
  vm.runInContext(`
    state.countries = [{ code: "MX", name: "墨西哥", language_name: "西班牙语" }];
    state.doubaoCountryCode = "MX";
    state.doubaoItems = [
      { record_id: "ready", "最终视频生成提示词": "prompt", "目标国家": "墨西哥", "目标语言": "西班牙语" },
      { record_id: "missing-prompt", "目标国家": "墨西哥", "目标语言": "西班牙语" }
    ];
    state.selectedDoubaoIds = new Set(["ready", "missing-prompt"]);
    renderDoubaoSelectionState();
  `, context);

  assert.equal(
    context.__elements.selectedDoubaoCount.textContent,
    "已选 2 条 · 当前国家可同步 1 条",
  );
});

test("Doubao data refreshes when a background job finishes", async () => {
  const requests = [];
  let jobRequestCount = 0;
  const context = loadApp(async (requestPath) => {
    requests.push(requestPath);
    if (requestPath === "/api/jobs") {
      jobRequestCount += 1;
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: [{
            id: "job-1",
            name: "doubao-batch-prompts:MX",
            status: jobRequestCount === 1 ? "running" : "done",
          }],
        }),
      };
    }
    if (String(requestPath).startsWith("/api/doubao-items/page?")) {
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: [{ record_id: "fresh-record", "最终视频生成提示词": "fresh prompt" }],
          has_more: false,
          page_token: "",
        }),
      };
    }
    throw new Error(`unexpected request: ${requestPath}`);
  });
  vm.runInContext(`
    state.activePage = "doubao";
    state.loadedPages.add("doubao");
  `, context);

  await vm.runInContext("loadJobs()", context);
  await vm.runInContext("loadJobs()", context);

  const refreshRequest = requests.find((requestPath) => String(requestPath).startsWith("/api/doubao-items/page?"));
  assert.ok(refreshRequest, "expected completed Doubao job to refresh the table");
  assert.equal(new URL(refreshRequest, "https://example.test").searchParams.get("force"), "1");
  assert.deepEqual(
    [...vm.runInContext("state.doubaoItems.map((row) => row.record_id)", context)],
    ["fresh-record"],
  );
});

test("loadDoubaoItems follows every cursor and merges all records", async () => {
  const requests = [];
  const pages = [
    {
      success: true,
      data: [{ record_id: "rec-1" }, { record_id: "rec-2" }],
      has_more: true,
      page_token: "cursor with space",
    },
    {
      success: true,
      data: [{ record_id: "rec-3" }],
      has_more: false,
      page_token: "",
    },
  ];
  const context = loadApp(async (requestPath) => {
    requests.push(requestPath);
    const payload = pages[requests.length - 1] || pages.at(-1);
    return { ok: true, json: async () => payload };
  });

  await vm.runInContext("loadDoubaoItems({ force: true })", context);

  const recordIds = vm.runInContext("state.doubaoItems.map((row) => row.record_id)", context);
  assert.deepEqual([...recordIds], ["rec-1", "rec-2", "rec-3"]);
  assert.equal(requests.length, 2);

  const first = new URL(requests[0], "https://example.test");
  assert.equal(first.pathname, "/api/doubao-items/page");
  assert.equal(first.searchParams.get("page_size"), "100");
  assert.equal(first.searchParams.get("offset"), "0");
  assert.equal(first.searchParams.get("force"), "1");

  const second = new URL(requests[1], "https://example.test");
  assert.equal(second.searchParams.get("page_token"), "cursor with space");
  assert.equal(second.searchParams.get("offset"), "2");
  assert.equal(second.searchParams.get("force"), "1");
});
