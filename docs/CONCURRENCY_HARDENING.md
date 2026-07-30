# 多人并发加固运行说明

## 生产要求

- `REDIS_URL`：Redis 连接地址，用于任务队列、任务状态、精确去重和记录锁。
- `DATABASE_URL`：PostgreSQL 连接地址，用于兼职幂等台账和操作审计。
- `JOB_WORKER_CONCURRENCY`：单实例 Worker 并发数，默认 `2`。
- `JOB_LOCK_TTL_SECONDS`：记录锁租约，默认 `300` 秒；长任务会心跳续租，Worker 中断后由 30 秒周期检查自动回收。
- `ADMIN_USERS_JSON`：可选，Token 到操作者/角色的映射；未配置时兼容 `ADMIN_TOKEN`。
- Web 运行时：保持 `1` 个 Gunicorn 进程并使用 `gthread`、`4` 个有界线程。这样只会
  初始化一套应用内 Redis worker，同时飞书慢分页不会阻塞健康检查和任务入队。

生产环境在 Redis 或 PostgreSQL 缺失时不得静默使用内存协调器。内存后端仅用于本地开发和自动化测试。

## 非功能目标

- 同一业务记录同一时刻最多一个写任务。
- 完全相同的重复点击返回同一个 Job；不同选择始终创建不同 Job。
- Worker 并发有上限，进程重启后排队任务可恢复。
- 兼职同步唯一键不会生成两条活动映射。
- Prompt 冲突返回 409，不覆盖他人的新版本。
- 所有写请求记录操作者、动作、资源和结果。

## 部署与回滚

部署前执行完整单元测试、并发测试、Redis/PostgreSQL 集成测试和浏览器冒烟测试。生产发布后检查 `/healthz`、队列深度、运行中任务数和最近失败任务。

回滚使用 Railway 上一个成功部署；不要删除 Redis/PostgreSQL，以便保留审计和排队状态。

### 发布门禁

每次修改按以下顺序验证，任何一步失败都不发布：

1. `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
2. `node --test tests/*.js`
3. `git diff --check`
4. `.venv/bin/python -m compileall -q api app.py`
5. 在 Railway 容器内运行 Redis/PostgreSQL 集成测试：
   `INTEGRATION_REDIS_URL="$REDIS_URL" INTEGRATION_DATABASE_URL="$DATABASE_URL" python -m unittest tests.test_production_backends -v`
6. 发布后确认 `/healthz` 返回 `200`，且 `coordination.alive_workers` 等于
   `coordination.expected_workers`、`coordination.recovery_worker_alive` 为 `true`。
7. 选择一条已经入库的豆包素材再次提交图片转存，确认任务完成、失败数为 `0`，
   且结果为幂等跳过；再确认队列可以继续接收下一条任务。

### Redis 队列故障处置

出现“任务一直排队、不能接新任务”时：

1. 查看 `/healthz`。Redis `ping` 正常但 `alive_workers < expected_workers` 时，
   说明连接仍可用但消费线程已经退出。
2. 查看 Railway 日志中是否有 `Timeout reading from socket`、worker 异常计数和最近异常时间。
3. 先确认运行中的任务和队列深度；需要恢复时重启服务，让 Redis 中的排队任务继续消费。
4. 持续观察旧任务直至结束，再发布修复，避免中断正在执行的图片下载或飞书写入。
5. 对 `RecordIdNotFound` 不做盲目重试。它表示任务携带的是已删除或重建前的飞书
   `record_id`，应从当前表格重新选择有效记录后创建新任务。

worker 对 Redis 阻塞读取、任务执行和 processing 清理阶段的瞬时异常都会记录并继续循环；
健康检查同时检查 Redis/PostgreSQL 和实际存活 worker，不能只以 Redis `ping` 作为队列健康依据。

### 下一阶段架构

当前 Web 与 Worker 仍在同一 Railway 服务中。业务量继续增长后，应拆成独立 Web 服务和
独立 Worker 服务，共用 Redis/PostgreSQL：Web 只负责校验、入队和查询状态，Worker 可独立扩容、
滚动重启和限流。拆分前仍以有界并发为准，图片转存、人脸检测和提示词任务可以同时排队执行，
但各自并发不得无限增加。
