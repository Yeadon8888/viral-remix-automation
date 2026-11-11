# 短视频带货全链路自动化工作后台

以飞书多维表格承载运营协作，以 Python/Flask 执行业务流程的短视频工作台。生产部署在 Railway；Redis 保存任务与检查点，PostgreSQL 保存持久化业务数据，R2/上传网关保存媒体文件。

## 接手前先读：代码与生产基线

截至 **2026-11-11**，近期生产更新采用维护者本地受控上传 Railway 的方式发布；GitHub `main` **尚未完整对齐近期生产能力**。本次文档更新不代表业务代码已同步，也不代表自动部署、CI 或分支保护已经开启。

- 不要把旧 `main` 直接部署到生产，以免覆盖线上新功能。
- 修改线上问题前，请维护者提供与生产版本对应的提交/开发分支；记录提交 SHA 与 `/healthz` 的 release 标识。
- 仓库找不到某个线上模块时，先确认基线，不能据此自行重写一套。
- 本地存在未提交改动时，不要整批提交或覆盖；从维护者确认的基线新建隔离分支。

## 开发者与 Agent 入口

| 你要做什么 | 从哪里开始 |
| --- | --- |
| 首次接手、权限和安全边界 | [AGENTS.md](AGENTS.md) |
| 接任务、建分支、测试、提 PR | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 复现问题、查任务、定位故障 | [问题定位指南](docs/agents/debugging.md) |
| 业务名词和历史决策 | [CONTEXT.md](CONTEXT.md)、[ADR](docs/adr/) |
| Issue 状态与依赖 | [Issue tracker](docs/agents/issue-tracker.md)、[分诊标签](docs/agents/triage-labels.md) |

**协作约定：开发者/Agent 改代码、测试并提 PR；维护者审核、合并并决定上线。** PR 模板与 CODEOWNERS 用于规范交接；强制审核、禁止直推及自动部署需要管理员另外配置，不会因文档存在而自动生效。

## 工作台业务范围

以下是维护者当前管理的线上流程概览，**不是对本仓库旧基线已具备全部功能的承诺**：

1. **爆款复刻**：参考视频/商品素材 → 视频理解与 Prompt → 参考图 → 视频任务 → 成片。
2. **豆包素材**：库存检查 → 图片转存 → 可选人脸检测与白底图修复 → Prompt 与独立发布元数据 → 兼职表。发布同步是后续独立动作。
3. **兼职成片**：审核完成视频 → 按资格、国家、权重/等级及配额匹配 AI 账号 → 发布表。
4. **视频搬运**：明确拥有使用权的视频/Excel → 受限编辑 → R2 成片 → 文案改写 → 仅匹配参与发布的搬运账号 → 发布表“待发布”。不等于直接向 TikTok 发帖，也不承诺平台认可原创。
5. **运营配置与观察**：Prompt/合规层、AI 与 TikHub 渠道、账号健康、权重与经营观察。启用状态以当前版本和配置为准。

AI 与搬运账号池不得混用。模型及渠道以任务快照和已启用配置为准，不从旧 README 的默认值推断当前生产渠道。每次修改流程前先核对相关 ADR 和基线。

## 本地开发

建议使用与 [Dockerfile](Dockerfile) 一致的 Python 主次版本（当前为 3.13）、Node.js 22+、Git。媒体测试还需要 `ffmpeg` 和 `ffprobe`。

```bash
git clone https://github.com/Yeadon8888/viral-remix-automation.git
cd viral-remix-automation
git status --short
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt pytest pytest-subtests
cp .env.example .env
```

先按 CONTRIBUTING 核对干净工作区与维护者确认的提交 SHA，再创建开发分支；不要直接把刚拉到的旧 main 当作生产基线。

**先配置测试资源，再运行服务。** 不要直接照抄示例中的历史表 ID、媒体前缀或渠道；它们不是可安全写入的测试环境。没有测试资源时，先运行 mock 单元测试，不接生产飞书、Redis、数据库或付费模型。

需要由维护者提供的配置类别：

| 类别 | 常见配置 | 开发要求 |
| --- | --- | --- |
| 管理鉴权 | `ADMIN_TOKEN` | 独立测试令牌，不使用生产管理员令牌 |
| 飞书 | `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_APP_TOKEN`、各 `FEISHU_*_TABLE_ID` | 独立测试 Base、表和应用权限 |
| 任务/持久化 | `REDIS_URL`、`DATABASE_URL`、`COORDINATION_REQUIRED` | 隔离实例或维护者批准的隔离配置，不能共用生产任务队列 |
| 媒体存储 | 上传网关配置或 `R2_*` | 测试 bucket/前缀；生命周期规则由存储端确认 |
| 外部模型/查询 | Gemini/AI 渠道、TikHub、图片/视频接口配置 | 默认 mock；真实调用需确认费用、用途和上限 |

可用字段及默认值见 [api/config.py](api/config.py) 与 [.env.example](.env.example)；较新版本可能增加网页端加密配置，以对应基线代码为准。不要在文档、PR、日志、命令行参数或截图中放 Key、Cookie、数据库密码。

配置审核后运行：

```bash
python -m flask --app app --no-debug run --host 127.0.0.1 --port 38888 --no-debugger --no-reload
```

访问 `http://127.0.0.1:38888`。该命令仅绑定本机且关闭调试器/自动重载；旧基线的 `python app.py` 会监听 0.0.0.0 并开启 debug，不作为默认协作启动方式。

## 测试

```bash
python -m pytest -q
node --test tests/*.js
```

先运行相关用例，再运行全量回归。缺少 FFmpeg 导致的跳过必须在 PR 中说明；不得把“测试跳过”写成媒体流程已验证。不把真实飞书写入、模型付费或上线当作单元测试步骤。

## 诊断入口

| 入口 | 用途 |
| --- | --- |
| `GET /healthz` | Worker、Redis/PostgreSQL 健康及版本提供的 release；不是任务完成证明 |
| `GET /api/health` | 业务配置检查 |
| `GET /api/jobs?limit=100` | 服务器任务列表；不以浏览器缓存判断后台有无任务 |
| `GET /api/jobs/<job_id>` | 单任务状态、结果与可用进度 |
| `GET /api/logs` | 诊断日志；分享前必须脱敏 |

旧基线可能没有 release 字段；此时记“未知”，请维护者提供部署 ID 与提交 SHA 的对应关系，不把缺字段本身当成服务故障。

生产是否提供某字段或路由，必须以对应发布版本为准。配置了管理鉴权时，`/api/*` 需要授权；不要公开令牌来“方便排查”。停止、失败重试、同步、配置保存等 POST 操作会改变业务状态，不属于只读诊断。

完整排查步骤、常见错误和模块地图见[问题定位指南](docs/agents/debugging.md)。

## 发布

**当前：** 维护者受控发布。贡献者不能直接运行 Railway 上线、重启、变量修改、飞书迁移或任务重试。

**目标流程（待单独配置）：** PR → 自动测试 → 维护者审核 → 合并 `main` → Railway 等待 CI 成功后部署 → 健康检查与业务验收。配置前须先完成生产代码基线对齐；不得直接把旧 `main` 接成生产源。

涉及功能、算法或修复的生产发布，必须按 [AGENTS.md](AGENTS.md) 完成飞书“110自动化迭代记录”的发布前/验收后留痕。纯文档 PR 不会触发业务部署，本 PR 不开启自动部署或调整权限。
