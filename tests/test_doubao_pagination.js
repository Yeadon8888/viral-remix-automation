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

test("API reports a readable error when the server returns HTML", async () => {
  const context = loadApp(async () => ({
    ok: false,
    status: 502,
    headers: { get: () => "text/html; charset=utf-8" },
    json: async () => {
      throw new SyntaxError("Unexpected token '<'");
    },
    text: async () => "<!doctype html><title>Bad Gateway</title>",
  }));

  await assert.rejects(
    vm.runInContext('api("/api/test")', context),
    /服务暂时不可用/,
  );
});

test("Doubao selection shows selected and currently syncable counts", () => {
  const context = loadApp(async () => ({ ok: true, json: async () => ({ success: true }) }));
  vm.runInContext(`
    state.countries = [{ code: "MX", name: "墨西哥", language_name: "西班牙语" }];
    state.doubaoCountryCode = "MX";
    state.doubaoItems = [
      { record_id: "ready", "可同步兼职": true, "发布前置条件通过": true, "最终视频生成提示词": "prompt", "目标国家": "墨西哥", "目标语言": "西班牙语" },
      { record_id: "missing-prompt", "目标国家": "墨西哥", "目标语言": "西班牙语" }
    ];
    state.selectedDoubaoIds = new Set(["ready", "missing-prompt"]);
    renderDoubaoSelectionState();
  `, context);

  assert.equal(
    context.__elements.selectedDoubaoCount.textContent,
    "已选 2 条 · 可导出 1 条 · 本次提交 1 条 · 前置检查未通过 1 条",
  );
});

test("Doubao row-range selection loads only the requested rows before the full table is ready", async () => {
  const requests = [];
  const context = loadApp(async (requestPath) => {
    requests.push(String(requestPath));
    if (String(requestPath).startsWith("/api/doubao-items/range?")) {
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: [
            { record_id: "row-6453", "行号": 6453 },
            { record_id: "row-6552", "行号": 6552 },
          ],
          total: 7038,
        }),
      };
    }
    throw new Error(`unexpected request: ${requestPath}`);
  });
  vm.runInContext(`
    $("doubaoRangeStart").value = "6453";
    $("doubaoRangeEnd").value = "6552";
  `, context);

  await vm.runInContext("selectDoubaoRange()", context);

  const rangeRequest = requests.find((requestPath) => requestPath.startsWith("/api/doubao-items/range?"));
  assert.ok(rangeRequest, "expected row-range selection to use the bounded range endpoint");
  const url = new URL(rangeRequest, "https://example.test");
  assert.equal(url.searchParams.get("start_row"), "6453");
  assert.equal(url.searchParams.get("end_row"), "6552");
  assert.equal(url.searchParams.get("force"), null);
  assert.deepEqual(
    [...vm.runInContext("state.selectedDoubaoIds", context)].sort(),
    ["row-6453", "row-6552"],
  );
  assert.equal(vm.runInContext("state.doubaoItems.length", context), 2);
});

test("Doubao data refreshes when a background job finishes", async () => {
  const requests = [];
  let jobRequestCount = 0;
  const context = loadApp(async (requestPath) => {
    requests.push(requestPath);
    if (String(requestPath).startsWith("/api/jobs")) {
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
    if (String(requestPath).startsWith("/api/doubao-items/row-index")) {
      return {
        ok: true,
        json: async () => ({ success: true, data: { "fresh-record": 2472 }, total: 2472 }),
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

test("a submitted Doubao job refreshes the table even when it finishes before the first poll", async () => {
  const requests = [];
  const context = loadApp(async (requestPath) => {
    requests.push(requestPath);
    if (requestPath === "/api/doubao-items/record-fast/store-image") {
      return {
        ok: true,
        json: async () => ({ success: true, job_id: "job-fast" }),
      };
    }
    if (String(requestPath).startsWith("/api/jobs")) {
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: [{
            id: "job-fast",
            name: "doubao-store-image:record-fast",
            action: "doubao.store_image",
            status: "done",
          }],
        }),
      };
    }
    if (String(requestPath).startsWith("/api/doubao-items/page?")) {
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: [{ record_id: "record-fast", "商品图已入库": true }],
          has_more: false,
          page_token: "",
        }),
      };
    }
    if (String(requestPath).startsWith("/api/doubao-items/row-index")) {
      return {
        ok: true,
        json: async () => ({ success: true, data: { "record-fast": 1 }, total: 1 }),
      };
    }
    throw new Error(`unexpected request: ${requestPath}`);
  });
  vm.runInContext(`
    state.activePage = "doubao";
    state.loadedPages.add("doubao");
  `, context);

  await vm.runInContext(`api("/api/doubao-items/record-fast/store-image", {
    method: "POST",
    body: "{}"
  })`, context);
  await vm.runInContext("loadJobs()", context);

  const refreshRequest = requests.find((requestPath) => String(requestPath).startsWith("/api/doubao-items/page?"));
  assert.ok(refreshRequest, "expected the fast completed job to refresh Doubao data");
  assert.equal(new URL(refreshRequest, "https://example.test").searchParams.get("force"), "1");
});

test("loadDoubaoItems follows every cursor and merges all records", async () => {
  const requests = [];
  const pages = [
    {
      success: true,
      data: [{ record_id: "rec-1" }, { record_id: "rec-2" }],
      has_more: true,
      page_token: "cursor with space",
      total: 3,
    },
    {
      success: true,
      data: [{ record_id: "rec-3" }],
      has_more: false,
      page_token: "",
      total: 3,
    },
  ];
  let pageRequestCount = 0;
  const context = loadApp(async (requestPath) => {
    requests.push(requestPath);
    if (String(requestPath).startsWith("/api/doubao-items/row-index")) {
      return {
        ok: true,
        json: async () => ({ success: true, data: { "rec-1": 101, "rec-2": 205, "rec-3": 309 }, total: 3 }),
      };
    }
    const payload = pages[pageRequestCount] || pages.at(-1);
    pageRequestCount += 1;
    return { ok: true, json: async () => payload };
  });

  await vm.runInContext("loadDoubaoItems({ force: true })", context);

  const recordIds = vm.runInContext("state.doubaoItems.map((row) => row.record_id)", context);
  assert.deepEqual([...recordIds], ["rec-3", "rec-2", "rec-1"]);
  const pageRequests = requests.filter((requestPath) => String(requestPath).startsWith("/api/doubao-items/page?"));
  assert.equal(pageRequests.length, 2);
  assert.deepEqual(
    [...vm.runInContext("state.doubaoItems.map((row) => row['行号'])", context)],
    [309, 205, 101],
  );

  const first = new URL(pageRequests[0], "https://example.test");
  assert.equal(first.pathname, "/api/doubao-items/page");
  assert.equal(first.searchParams.get("page_size"), "100");
  assert.equal(first.searchParams.get("offset"), "0");
  assert.equal(first.searchParams.get("force"), "1");
  assert.equal(first.searchParams.get("order"), "latest");
  assert.equal(first.searchParams.get("days"), "2");

  const second = new URL(pageRequests[1], "https://example.test");
  assert.equal(second.searchParams.get("page_token"), "cursor with space");
  assert.equal(second.searchParams.get("offset"), "2");
  assert.equal(second.searchParams.get("force"), "1");
  assert.equal(second.searchParams.get("order"), "latest");
  assert.equal(second.searchParams.get("days"), "2");
});

test("Doubao records render only after every cursor and row index finish", async () => {
  let releaseSecondPage;
  let pageCallCount = 0;
  const context = loadApp(async (requestPath) => {
    if (String(requestPath).startsWith("/api/doubao-items/row-index")) {
      return {
        ok: true,
        json: async () => ({ success: true, data: { "newest-record": 2829, "older-record": 2828 }, total: 2829 }),
      };
    }
    pageCallCount += 1;
    if (pageCallCount === 1) {
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: [{ record_id: "newest-record" }],
          has_more: true,
          page_token: "next-page",
          total: 2,
        }),
      };
    }
    return new Promise((resolve) => {
      releaseSecondPage = () => resolve({
        ok: true,
        json: async () => ({
          success: true,
          data: [{ record_id: "older-record" }],
          has_more: false,
          page_token: "",
          total: 2,
        }),
      });
    });
  });

  const loading = vm.runInContext("loadDoubaoItems()", context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(
    [...vm.runInContext("state.doubaoItems.map((row) => row.record_id)", context)],
    [],
  );
  assert.match(context.__elements.doubaoRefreshState.textContent, /正在加载全部/);
  assert.match(context.__elements.doubaoItems.innerHTML, /全部加载完成后统一显示/);

  releaseSecondPage();
  await loading;
  assert.deepEqual(
    [...vm.runInContext("state.doubaoItems.map((row) => row.record_id)", context)],
    ["newest-record", "older-record"],
  );
  assert.deepEqual(
    [...vm.runInContext("state.doubaoItems.map((row) => row['行号'])", context)],
    [2829, 2828],
  );
  assert.match(context.__elements.doubaoRefreshState.textContent, /已从飞书刷新 2 条/);
});

test("Doubao page exposes row-number order control independently from its date filter", () => {
  const root = path.join(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "public", "index.html"), "utf8");

  assert.match(html, /id="doubaoSortOrder"/);
  assert.match(html, /value="latest">行号倒序/);
  assert.match(html, /value="oldest">行号正序/);
  assert.match(html, /id="doubaoDateRange"/);
  assert.match(html, /value="2" selected>最近 2 天/);
});

test("Doubao date-filtered records are displayed in real row-number order", async () => {
  const context = loadApp(async (requestPath) => {
    if (String(requestPath).startsWith("/api/doubao-items/row-index")) {
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: { "row-5480": 5480, "row-5474": 5474, "row-5481": 5481, "row-5479": 5479 },
          total: 5481,
        }),
      };
    }
    return {
      ok: true,
      json: async () => ({
        success: true,
        data: [
          { record_id: "row-5480" },
          { record_id: "row-5474" },
          { record_id: "row-5481" },
          { record_id: "row-5479" },
        ],
        has_more: false,
        page_token: "",
      }),
    };
  });

  await vm.runInContext("loadDoubaoItems()", context);
  assert.deepEqual(
    [...vm.runInContext("state.doubaoItems.map((row) => row['行号'])", context)],
    [5481, 5480, 5479, 5474],
  );

  vm.runInContext("state.doubaoSortOrder = 'oldest'", context);
  await vm.runInContext("loadDoubaoItems()", context);
  assert.deepEqual(
    [...vm.runInContext("state.doubaoItems.map((row) => row['行号'])", context)],
    [5474, 5479, 5480, 5481],
  );
});

test("concurrent page loads reuse the same in-flight Doubao request", async () => {
  let releasePage;
  let pageRequests = 0;
  const context = loadApp(async (requestPath) => {
    if (String(requestPath).startsWith("/api/doubao-items/row-index")) {
      return {
        ok: true,
        json: async () => ({ success: true, data: { "rec-1": 1 }, total: 1 }),
      };
    }
    if (String(requestPath).startsWith("/api/part-time-items")) {
      return { ok: true, json: async () => ({ success: true, data: [] }) };
    }
    pageRequests += 1;
    return new Promise((resolve) => {
      releasePage = () => resolve({
        ok: true,
        json: async () => ({
          success: true,
          data: [{ record_id: "rec-1" }],
          has_more: false,
          page_token: "",
        }),
      });
    });
  });

  const first = vm.runInContext('loadPageData("doubao")', context);
  const second = vm.runInContext('loadPageData("doubao")', context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(pageRequests, 1);
  releasePage();
  await Promise.all([first, second]);
});

test("returning to an already loaded page reuses its in-memory data", async () => {
  let pageRequests = 0;
  const context = loadApp(async (requestPath) => {
    if (String(requestPath).startsWith("/api/doubao-items/row-index")) {
      return {
        ok: true,
        json: async () => ({ success: true, data: { "rec-1": 1 }, total: 1 }),
      };
    }
    if (String(requestPath).startsWith("/api/part-time-items")) {
      return { ok: true, json: async () => ({ success: true, data: [] }) };
    }
    pageRequests += 1;
    return {
      ok: true,
      json: async () => ({
        success: true,
        data: [{ record_id: "rec-1" }],
        has_more: false,
        page_token: "",
      }),
    };
  });

  await vm.runInContext('loadPageData("doubao")', context);
  await vm.runInContext(`
    state.activePage = "part-time";
    state.activePage = "doubao";
    activePageLoaded("doubao") ? Promise.resolve() : loadPageData("doubao")
  `, context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(pageRequests, 1);
});
