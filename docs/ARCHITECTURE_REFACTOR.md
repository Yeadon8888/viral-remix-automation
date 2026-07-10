# 1.0 后架构优化方案

更新时间：2026-07-10

## 本次已落地

- `api/jobs/runner.py`：任务运行器已从 `app.py` 拆出，路由层只负责触发任务和查询任务状态。
- `api/services/viral_video_assets.py`：爆款视频直链缓存、TikHub 重新解析、ffmpeg 首帧截取、R2 首帧入库已从 `ViralRemixWorkflow` 拆出。
- `public/css/style.css`：工作台视觉层整理为响应式 B2B 控制台风格，突出视频复刻、豆包视频、Prompt 配置三个业务区。
- `api/countries.py`：MY / MY_EN / PH / TH / ID 的稳定国家语言配置码、飞书地区映射和语言信息集中管理；同一市场可安全拥有多种语言版本。
- `api/services/part_time_sync.py`：豆包记录到兼职表的字段映射、`来源记录 + 国家 + 语言` 幂等键和状态保护独立为领域服务；批量同步只预载一次目标表。
- 前端信息架构收敛为「视频复刻 / 豆包视频 / Prompt配置」三类；素材与视频任务成为视频复刻内部视图，Dola 前台入口退役。

本轮新增的飞书字段只用于国家、Prompt 版本和兼职同步追踪；没有删除旧字段，Dola 后端兼容接口仍保留，避免破坏历史调用方。

## 当前判断

1.0 版本已经跑通了核心闭环：飞书素材输入、视频拆解、商品主图入库、商品组图、视频任务、CPA 视频生成、发布表同步、Prompt 配置调试。

当前主要风险不是功能缺失，而是功能以补丁方式叠加后，边界开始变模糊：

- `ViralRemixWorkflow` 同时承担编排、飞书字段映射、模型调用、存储、容错和业务状态更新。
- 前端 `app.js` 里状态、渲染、事件、接口请求混在一起，继续扩展页面会越来越难维护。
- 飞书字段名散落在代码里，字段变更时容易漏改。
- 后台任务是内存态，适合 1.0 手动触发，但不适合更长任务、恢复、并发和审计。
- Prompt 配置已经独立出来，但“使用哪个 Prompt、快照写哪里、失败如何回退”仍然分散在业务代码中。

## 目标形态

系统应该拆成四层，做到低耦合高内聚：

1. **表格适配层**
   - 只负责飞书表结构、字段读写、字段值转换。
   - 对外返回稳定的领域对象，不让业务层直接关心飞书字段格式。

2. **领域服务层**
   - 只表达业务动作：拆解视频、主图入库、生成组图、创建视频任务、生成视频、同步发布表。
   - 不直接拼飞书字段 payload，不直接处理 HTTP 细节。

3. **外部能力网关层**
   - TikHub、Gemini、NewToken、CPA、R2/Upload Gateway 各自独立。
   - 每个网关统一输出标准结果和标准错误，方便降级、重试和替换渠道。

4. **任务编排层**
   - 管理任务状态、幂等、互斥、重试、日志和进度。
   - 当前可继续用内存队列，后续迁移到 SQLite/Postgres/Railway volume 或 Redis 队列。

## 建议目录结构

```text
api/
  app_routes.py              # Flask 路由，只做请求参数和响应
  config.py
  domain/
    material.py              # Material, VideoTask, PromptConfig 等领域模型
    statuses.py              # 状态枚举与状态流转
  tables/
    base_client.py           # 飞书基础客户端
    material_table.py        # 素材表字段映射
    task_table.py            # 视频任务表字段映射
    prompt_table.py
    doubao_table.py
    publish_table.py
  services/
    material_service.py      # 拆解、主图、组图、任务创建
    video_service.py         # CPA 生成、轮询、转存
    doubao_service.py
    publish_service.py
  gateways/
    tikhub.py
    gemini.py
    newtoken.py
    cpa.py
    storage.py
  jobs/
    runner.py                # run_job、去重、限流、重试
    logs.py
  prompts/
    registry.py              # Prompt 选择、渲染、快照
```

## 核心抽象

### MaterialRepository

负责素材表：

- `list(limit) -> list[Material]`
- `get(record_id) -> Material`
- `update_status(record_id, status, error="")`
- `save_video_analysis(record_id, result)`
- `save_main_image(record_id, url)`
- `save_group_image(record_id, result)`
- `save_viral_video_cache(record_id, url, resolved_at)`

业务层不再直接写 `"商品组图链接"`、`"自动化状态"` 这类字段名。

### ViralVideoAssetService

负责视频资产的临时处理：

- `download_for_analysis(material) -> VideoBytes`
- `ensure_first_frame(material) -> FirstFrameResult`
- 内部逻辑：
  - 优先使用 `爆款视频直链`
  - 失败后重新 TikHub 解析
  - 首帧失败可降级，不阻断组图

### ImageGenerationService

负责商品组图：

- 输入：商品主图、可选爆款首帧、模板 key、Prompt 配置
- 输出：生成 URL、转存 URL、实际 Prompt、失败原因
- 只生成一张还是多张，由策略决定，而不是散落在流程里。

### TaskService

负责视频任务：

- 根据素材创建/更新任务，默认一素材一任务。
- 任务幂等键建议从 `source_record_id + reference_image_url` 升级为 `source_record_id + task_type + variant_key`。
- 后续要恢复裂变时，只改任务策略，不影响素材处理。

## 状态机建议

素材状态建议从自由文本逐步收敛为有限状态：

- `待处理`
- `视频拆解中`
- `视频已拆解`
- `主图入库中`
- `主图已入库`
- `组图生成中`
- `组图已生成`
- `任务创建中`
- `已创建任务`
- `失败`

视频任务状态：

- `待生成视频`
- `视频生成中`
- `已完成`
- `失败`

每个状态更新都由统一函数写入：

- 当前状态
- 错误摘要
- 处理时间
- 关联 job id

## 前端重构方向

前端可以继续保持原生 JS，但建议按模块拆文件：

```text
public/js/
  api.js
  state.js
  render/materials.js
  render/tasks.js
  render/doubao.js
  render/prompts.js
  actions/materialActions.js
  actions/taskActions.js
  main.js
```

收益：

- 表格渲染和接口请求分开。
- 每个页面有自己的 `load/render/bind`。
- 后面加新页面，例如“素材资产库”“发布队列”“任务监控”，不会继续挤进一个大文件。

## 迁移策略

不要一次性重写。建议分四步：

1. **字段映射收口**
   - 先把飞书字段名集中到 `tables/*_table.py`。
   - 现有行为不变，只减少散落字段名。

2. **外部网关标准化**
   - TikHub、Gemini、NewToken、CPA 返回统一结构。
   - 标准化错误类型：可重试、可降级、用户数据缺失、配置错误。

3. **任务编排独立**
   - 把 `run_job` 从 `app.py` 移到 `jobs/runner.py`。
   - 增加 job metadata：关联 record_id、步骤、开始时间、结束时间。

4. **前端模块化**
   - 先拆 API 和页面渲染，不改视觉。
   - 每拆一页就跑线上验证。

## 近期优先级

最应该先做：

1. 飞书字段映射收口。
2. TikHub 视频直链缓存逻辑抽成 `ViralVideoAssetService`。
3. NewToken 图片生成任务做更清晰的超时、重试、继续轮询入口。
4. 前端拆 `api.js` 和四个页面渲染模块。

暂时不急：

- 上数据库队列。
- 做复杂定时任务。
- 大规模 UI 重写。
- 多账号发布自动化深度集成。

## 架构原则

- 表格是业务状态存储，不是临时大文件存储。
- R2 只存需要给模型或人长期访问的图片/视频结果。
- TikHub 直链只缓存，不承诺长期有效。
- 每个步骤都应该可单独重跑。
- 每个外部能力失败都要判断：阻断、降级、还是稍后重试。
- Prompt 是核心变量，必须可配置、可版本化、可回放。
