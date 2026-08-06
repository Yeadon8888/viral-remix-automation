## Parent

#1

## What to build

让操作员显式标记豆包素材的鞋类/服装/其他品类，并对墨西哥鞋类或服装选择对应 10 秒直接 Prompt 方案，确定性生成最终提示词而不调用 Gemini。

## Acceptance criteria

- [ ] 豆包素材可编辑商品品类，未设置按其他处理且不从名称猜测。
- [ ] 鞋类方案只对墨西哥+西班牙语+鞋类可用，服装方案同理；前后端均 fail closed。
- [ ] 直接渲染仅允许声明变量，未知/缺失必需变量会失败且不部分回写。
- [ ] 执行时断言 Gemini 未被调用；标题写商品名，文案/标签保留已有值或留空。
- [ ] 用户重新提供的鞋类/服装权威模板文本经审核后以停用版本导入；不凭记忆重建。

## Blocked by

- #2
- External input: authoritative Mexico shoes and clothing template text must be re-supplied.
