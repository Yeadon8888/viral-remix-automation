# 多人并发加固运行说明

## 生产要求

- `REDIS_URL`：Redis 连接地址，用于任务队列、任务状态、精确去重和记录锁。
- `DATABASE_URL`：PostgreSQL 连接地址，用于兼职幂等台账和操作审计。
- `JOB_WORKER_CONCURRENCY`：单实例 Worker 并发数，默认 `2`。
- `JOB_LOCK_TTL_SECONDS`：记录锁租约，默认 `300` 秒；长任务会心跳续租，Worker 中断后由 30 秒周期检查自动回收。
- `ADMIN_USERS_JSON`：可选，Token 到操作者/角色的映射；未配置时兼容 `ADMIN_TOKEN`。

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
