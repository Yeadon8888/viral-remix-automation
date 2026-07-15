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
  assert.match(html, /id="batchDoubaoSyncPublish"/);
  assert.match(js, /\/api\/doubao-items\/batch-generate-prompts/);
  assert.match(js, /\/api\/doubao-items\/batch-sync-part-time/);
  assert.match(js, /\/api\/doubao-items\/batch-sync-publish/);
  assert.match(js, /data\.readiness\.eligible_videos/);
  assert.match(js, /notify\(error\.message, "error"\)/);
  assert.match(js, /country_code/);
  assert.match(js, /"revision": row\.revision/);
  assert.doesNotMatch(js, /\{ value: "墨西哥", label: "墨西哥" \}/);
  assert.doesNotMatch(html, /连接本地Worker|更新本地Worker|打开 127\.0\.0\.1:5177/);
});

test("main navigation exposes three product areas", () => {
  const root = path.join(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "public", "index.html"), "utf8");

  assert.match(html, /短视频带货全链路自动化工作后台/);
  assert.match(html, /data-page="remix"[\s\S]*?<strong>视频复刻<\/strong>/);
  assert.match(html, /data-page="doubao"[\s\S]*?<strong>豆包视频素材表<\/strong>/);
  assert.match(html, /data-page="prompts"[\s\S]*?<strong>Prompt配置<\/strong>/);
  assert.doesNotMatch(html, /视频拆解 -&gt;|视频拆解 -> 主图入库/);
});
