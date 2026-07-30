# 短视频带货全链路自动化工作后台

围绕飞书多维表格搭建的短视频运营后台。前台分为「视频复刻」「豆包视频」「兼职成片」「Prompt配置」四个工作区；Dola 本地工作台已从前台退役，后端兼容接口暂时保留。

## 数据流

1. 从素材输入表读取商品名称、爆款视频链接、商品图片、商品组图提示词、地区。
2. 用 TikHub 解析 TikTok 分享链接并下载原视频。
3. 用 yunwu 渠道 Gemini 模型理解爆款视频，只输出最终视频生成 Prompt；Prompt 中文为主，口播和视频内文案使用目标地区语言。
4. 默认截取爆款视频第一帧，和商品主图一起作为 NewToken `gpt-image-2` 系列模型的参考图，生成 1 张商品组图并转存 R2；也可以切换为其它单一模板。
5. 在「爆款复刻视频生成任务」表创建 1 条视频任务，写入视频生成 Prompt 和参考图链接。
6. 手动触发任务表视频生成时，调用 CPA `/v1/videos`，下载结果视频并转存 R2，再写回任务表。
7. 可把已完成的视频任务同步到发布表「视频生成列表」。
8. 豆包视频支持 MY（马来语）/ MY_EN（马来英语）/ PH / TH / ID（印尼语）/ MX（墨西哥西班牙语）国家语言配置；按国家与语言选择 Seedance Prompt，并把选中记录幂等同步到「数据表-兼职」。目标任务进入「接单中」或「已完成」后，系统不会覆盖兼职人员的工作数据。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

默认访问 `http://localhost:38888`。

## Railway 环境变量

必填：

```bash
ADMIN_TOKEN=
REDIS_URL=
DATABASE_URL=
COORDINATION_REQUIRED=1
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_APP_TOKEN=H3RybjMFqabStts0zFUcWrz9nvb
FEISHU_SOURCE_TABLE_ID=tblDEJZwbA7iEuL1
FEISHU_TASK_TABLE_ID=tblJSkNvzYaku9Kg
FEISHU_PROMPT_TABLE_ID=tblvFcsSoHnTP5Te
FEISHU_DOUBAO_TABLE_ID=tblvHL2tMBrksvpH
FEISHU_PART_TIME_TABLE_ID=tblhFSNlmKkeJFVK
FEISHU_PUBLISH_TABLE_ID=tbl0D4WlwbkxtrZx
TIKHUB_API_KEY=
GEMINI_API_KEY=
NEWTOKEN_IMAGE_API_KEY=
CPA_API_KEY=
UPLOAD_API_URL=
UPLOAD_API_KEY=
```

常用可选项：

```bash
GEMINI_BASE_URL=https://yunwu.ai
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_REQUEST_CONCURRENCY=6
GEMINI_MIN_INTERVAL_SECONDS=0.25
BATCH_FACE_CONCURRENCY=6
NEWTOKEN_IMAGE_BASE_URL=https://api.newtoken.club
NEWTOKEN_IMAGE_MODEL=gpt-image-2-1K
IMAGE_SCENE_STYLES=viral_first_frame,lifestyle,model,detail,flatlay,outdoor,studio
CPA_BASE_URL=https://cliproxyapi-production-2dde.up.railway.app
CPA_VIDEO_MODEL=grok-imagine-video-1.5-preview
CPA_SECONDS=10
CPA_SIZE=720x1280
CPA_RESOLUTION=720p
CPA_PRESET=normal
UPLOAD_PREFIX=vidclaw-assets
R2_PREFIX=viral-remix-7d
```

推荐复用 vidclaw 的上传网关：`UPLOAD_API_URL / UPLOAD_API_KEY / UPLOAD_PREFIX`。如果没有上传网关，也可以改用直连 R2 的 `R2_ENDPOINT / R2_ACCESS_KEY / R2_SECRET_KEY / R2_BUCKET / R2_PUBLIC_URL`。`R2_PREFIX` 用来把本工具上传的图片和视频放在独立前缀下。7 天自动清理需要在 Cloudflare R2 bucket 上配置生命周期规则，例如删除 `vidclaw-assets/viral-remix-7d/` 前缀下超过 7 天的对象。

## API

- `GET /api/health`：检查缺失环境变量。
- `GET /api/countries`：读取豆包视频支持的国家语言配置；`code` 是稳定配置码，例如 `MY`、`MY_EN`、`ID`、`MX`。
- `GET /api/materials`：读取素材输入表。
- `POST /api/materials/<record_id>/analyze-video`：理解爆款视频并生成中文视频生成 Prompt。
- `POST /api/materials/<record_id>/store-image`：商品主图转存对象存储。
- `POST /api/materials/<record_id>/generate-images`：生成并转存商品组图。
- `POST /api/materials/<record_id>/create-tasks`：基于 Prompt 和商品组图创建视频任务。
- `POST /api/materials/<record_id>/prepare`：兼容旧的一键串联处理。
- `POST /api/materials/batch-prepare`：批量处理素材。
- `GET /api/tasks`：读取生成任务表。
- `POST /api/tasks/<record_id>/generate-video`：单条生成视频。
- `POST /api/tasks/batch-generate-video`：批量生成视频。
- `POST /api/tasks/sync-publish`：把已完成任务的标题、文案、标签、商品标题和视频链接同步到发布表。
- `GET /api/doubao-items/page`：按飞书游标读取全部豆包记录，不受 500 条限制。
- `POST /api/doubao-items/batch-operation`：豆包素材统一批量入口，支持 `process_workflow`（按流程自动执行）、`store_images`、`detect_faces`、`delete_face_records`、`generate_prompts`、`sync_part_time`、`sync_publish`。
- `POST /api/doubao-items/<record_id>/generate-prompt`：按 `country_code` 生成单条国家化 Prompt。
- `POST /api/doubao-items/batch-generate-prompts`：用一个后台任务批量生成国家化 Prompt。
- `POST /api/doubao-items/<record_id>/sync-part-time`：幂等同步单条记录到兼职表。
- `POST /api/doubao-items/batch-sync-part-time`：批量同步已选记录，并汇总创建、更新、冲突和失败数。

豆包素材按固定顺序处理：先把附件或外部图片链接转存到自有对象存储，再使用配置的 Gemini 模型检测人脸，只有“无人脸”的当前图片才能生成提示词和同步兼职表。图片链接发生变化后，旧检测结果自动失效；“含人脸”与“疑似人脸”的豆包素材记录可在后台确认后整条删除。发布表同步会携带豆包素材表中的 `商品id`，写入发布表 `商品ID` 字段，并继续按兼职记录 ID / 视频链接幂等更新。

豆包素材表需要以下字段：`商品id`、`后台创建时间`（自动创建时间）、`人脸检测状态`、`人脸检测说明`、`人脸检测时间`、`人脸检测图片链接`、`人脸检测模型`。发布表需要文本字段 `商品ID`。豆包页面默认按 `后台创建时间 DESC` 从飞书服务端读取，全部游标页和真实行号加载完成后统一显示；也可以切换为最早优先。页面显示的“行”来自独立的飞书原始顺序索引（`record_id → 行号`），不使用排序后序号或总数倒推，因此中间删除过记录时仍与多维表格当前行号一致。兼职成片页面支持按真实行号正序或倒序查看。

如果设置了 `ADMIN_TOKEN`，所有 `/api/*` 请求都需要带 `X-Admin-Token` 请求头。
