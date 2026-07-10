const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadApp(fetchImpl) {
  const appPath = path.join(__dirname, "..", "public", "js", "app.js");
  const source = fs.readFileSync(appPath, "utf8").split('$("adminToken").value = state.token;')[0];
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
    fetch: fetchImpl,
    localStorage: { getItem() { return ""; }, setItem() {} },
    setTimeout,
  });
  vm.runInContext(source, context);
  return context;
}

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
