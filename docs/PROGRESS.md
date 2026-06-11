# 爆款复刻自动化进度记录

更新时间：2026-06-11

## 当前定位

这是一个围绕飞书多维表格搭建的爆款短视频复刻工作台，用于把一条爆款视频和一个商品素材包拆成可追踪、可人工调试、可批量生成的视频任务。

核心链路：

1. 读取飞书素材表。
2. 解析 TikTok 爆款视频链接并下载视频。
3. 使用 Gemini 视频理解生成最终视频生成 Prompt、发布标题、正文和关键词。
4. 将商品主图转存到对象存储。
5. 使用图片模型生成商品组图，并转存到对象存储。
6. 按一张参考图对应一条视频任务创建飞书任务记录。
7. 使用 CPA 视频生成接口提交图生视频任务，轮询进度并把成品视频转存到对象存储。

## 飞书多维表

Base：

- `H3RybjMFqabStts0zFUcWrz9nvb`

核心表：

- 素材处理表：`tblDEJZwbA7iEuL1`，`v3-墨西哥-爆款短视频带货项目数据`
- 视频生成任务表：`tblJSkNvzYaku9Kg`，`爆款复刻视频生成任务`
- Prompt 配置表：`tblvFcsSoHnTP5Te`，`爆款复刻Prompt配置`
- 豆包视频制作表：`tblvHL2tMBrksvpH`，`豆包视频制作`

素材表新增的 Prompt 调试字段：

- `视频拆解提示词模板`
- `视频拆解实际提示词`
- `商品组图提示词模板`
- `商品组图实际提示词`
- `Prompt配置版本`

## Prompt 配置设计

采用三层策略：

1. 全局模板：保存在 `爆款复刻Prompt配置` 表，支持版本、启停和地区覆盖。
2. 单条覆盖：素材表里的 `视频拆解提示词模板`、`商品组图提示词模板` 只影响当前素材。
3. 运行快照：每次实际发给模型的 Prompt 写回 `视频拆解实际提示词`、`商品组图实际提示词`。

已初始化模板 key：

- `video_analysis`
- `image_lifestyle`
- `image_model`
- `image_detail`
- `image_flatlay`
- `image_outdoor`
- `image_studio`
- `doubao_seedance_video_prompt`

模板变量：

- `{product_name}`：商品名称
- `{region}`：目标地区
- `{lang}`：目标地区语言
- `{custom_prompt}`：人工补充要求
- `{style_label}`：商品组图模板中文名

豆包视频模板使用商品图作为多模态输入，回填 `最终视频生成提示词`、`标题`、`文案`、`标签`。商品图支持三层输入：`商品图存储链接` 优先；没有入库链接时读取 `商品图` 附件；没有附件时读取 `商品图链接` OSS/R2/CDN 图片 URL。

## 已完成能力

- 素材处理和视频生成拆成两个页面。
- 支持素材批量勾选处理。
- 支持商品组图模板选择。
- 视频生成任务支持进度轮询并写回飞书进度字段。
- CPA 返回 `video.url` 时可直接下载并转存，避免 100% 后无视频链接。
- 发布标题、文案和关键词在视频理解阶段生成，并关联写入视频任务。
- Prompt 配置表和工具里的 Prompt 配置页已上线。
- 豆包视频页已上线：读取 `豆包视频制作` 表的 `商品图` 附件或 `商品图链接`，可先转存到 `商品图存储链接`，再使用 Gemini 生成 Seedance 视频提示词、标题、文案和标签并回填。
- 后端已加同名任务互斥，前端运行中按钮会禁用，避免误触重复执行。

## 线上部署

- Railway 服务：`viral-remix-automation`
- 线上地址：https://viral-remix-automation-production.up.railway.app
- 存储方式：上传网关，对象前缀 `vidclaw-assets/viral-remix-7d/`
- 当前视频理解模型：`gemini-3.1-flash-lite`
- 当前 CPA 视频模型：`grok-imagine-video-1.5-preview`

## 注意事项

- 不要把真实 API Key、Admin Token、飞书 App Secret 写入仓库。
- `.env`、`.venv`、缓存目录和临时目录已被 `.gitignore` 忽略。
- 调 Prompt 时优先改 `爆款复刻Prompt配置` 表；只做单条实验时再用素材表覆盖字段。
