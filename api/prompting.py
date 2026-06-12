from typing import Any


DEFAULT_VIDEO_ANALYSIS_TEMPLATE = """
你是一位专业的短视频创作专家、TikTok 电商二创导演和视频生成 Prompt 工程师。

请分析用户提供的爆款视频，并围绕新的商品生成「最终可直接用于视频生成模型的 Prompt」和「可直接发布的标题、正文、标签」。

必须只输出一个 JSON 对象，不要 Markdown，不要代码块，不要分析过程，不要拆镜表。
video_prompt 必须以中文为主；只有观众会听到或看到的口播、旁白、字幕、屏幕文字、CTA，才使用目标地区语言。
publish_title、publish_caption、keywords 必须使用目标地区语言，适合直接发布到 TikTok。

# 商品信息
- 商品名称：{product_name}
- 目标地区：{region}
- 口播/字幕/屏幕文字语言：{lang}
- 用户补充要求：{custom_prompt}

# 最终 Prompt 必须包含
1. 视频比例、时长感、整体风格：9:16 TikTok 带货短视频，只抽象学习参考视频的节奏、镜头语言、转场方式和带货表达，不照搬参考视频的人物、场景、原商品、台词或完整剧情。
2. 商品约束：使用后续传入的参考图片作为唯一商品依据；商品颜色、材质、形状、Logo、包装、细节必须保持一致，不能替换成其他商品。
3. 镜头安排：用中文写清楚开头钩子、展示动作、卖点呈现、使用场景、收尾 CTA，但不要写成表格；整条视频只围绕一个核心卖点和一条清晰动作主线。
4. 地区本地化：场景、人物气质、生活环境符合 {region} 市场。
5. 口播/字幕：凡是观众能听到或看到的文本，必须直接写成 {lang}。
6. 负面约束：避免商品变形、错误 Logo、低清、手部畸形、文字遮挡商品、参考视频原商品喧宾夺主。

# 发布内容要求
1. publish_title：一句短标题，目标地区语言，突出商品核心利益点，不要超过 120 个字符。
2. publish_caption：可直接发布的正文，目标地区语言，包含自然口吻的卖点、使用场景和轻 CTA。
3. keywords：5-10 个目标地区语言标签/关键词，用空格分隔；适合 TikTok hashtag，可以包含 #。

输出 JSON 格式：
{
  "video_prompt": "完整中文视频生成 Prompt，口播/字幕使用 {lang}",
  "publish_title": "目标地区语言标题",
  "publish_caption": "目标地区语言正文",
  "keywords": "#tag1 #tag2 #tag3"
}
""".strip()


DEFAULT_IMAGE_PROMPTS = {
    "viral_first_frame": "请基于两张参考图生成一张新的 {style_label} 商品带货首帧图。商品名称：{product_name}。目标地区：{region}。目标语言：{lang}。用户补充要求：{custom_prompt}。\n\n参考图 A 是商品主图，必须作为最终画面的唯一商品依据；参考图 B 是爆款视频首帧，只用于学习画面结构、镜头语言、景别、自然光、UGC 真实感和开场钩子氛围，禁止直接复刻人物、人脸、服装、背景、原商品、文字、水印或品牌。\n\n生成要求：1. 保留参考图 B 的高层级效果：9:16 竖版手机自拍视频质感、真实生活场景、自然光、近景/半身或手持构图、人物/手部正在展示商品、适合 TikTok 带货视频首帧；2. 最终展示的商品只能来自参考图 A，商品颜色、形状、材质、结构、Logo、纹理、五金、包装等关键细节必须保持一致；3. 如果画面有人物，必须换成另一位符合 {region} 市场审美的普通用户，脸型、五官、发型、表情、年龄感、服装都要明显不同，不能像原视频人物；4. 背景可以保留同类型生活场景，例如阳台、门口、卧室、厨房、车内或街边，但墙面颜色、栏杆样式、窗户位置、植物、家具、道具必须重新设计，避免一眼看出来自同一张首帧；5. 动作可以参考“正在展示/拿起/靠近镜头展示商品”的带货逻辑，但手势、商品角度、身体姿态和画面摆位必须重新设计；6. 保留 40%-60% 的视觉结构参考，人物、背景、服装、道具、原物品至少 70% 差异；7. 画面要真实 UGC，不要棚拍广告感，不要过度美颜；商品必须清晰、完整、醒目；8. 不要出现 TikTok UI、水印、用户名、字幕、侵权品牌标识、奇怪文字、变形手指、错误商品、重复人脸、低清、文字遮挡商品。",
    "lifestyle": "请基于这张商品图生成一张生活场景展示图。要求：1. 将商品自然地融入日常使用场景中；2. 背景有{region}/拉美生活氛围；3. 光影自然柔和；4. 保留商品所有细节、颜色和品牌标识；5. 构图适合作为 TikTok 带货视频参考图。",
    "model": "请基于这张商品图生成一张模特使用/展示图。要求：1. 添加{region}/拉美当地外貌的模特自然展示商品；2. 姿态真实、有社交媒体广告感；3. 完整保留商品材质、颜色、结构和品牌信息；4. 适合作为带货视频首帧或参考图。",
    "detail": "请基于这张商品图生成一张细节特写图。要求：1. 微距突出商品质感和工艺；2. 浅景深；3. 光线突出材质；4. 保留品牌标识和设计元素；5. 适合视频中的卖点特写。",
    "flatlay": "请基于这张商品图生成一张平铺摆拍图。要求：1. 俯拍视角；2. 搭配适合{region} TikTok 电商审美的道具；3. 构图干净；4. 完整保留商品外观；5. 适合首帧封面。",
    "outdoor": "请基于这张商品图生成一张户外场景图。要求：1. 场景像{region}当地街道、咖啡馆、市集或公园；2. 明亮自然光；3. 保留商品所有细节；4. 如果有人物，必须是{region}/拉美当地外貌；5. 适合 TikTok 带货视频参考。",
    "studio": "请基于这张商品图生成一张专业棚拍风格图。要求：1. 专业摄影背景；2. 三点布光突出立体感；3. 高级干净；4. 完整保留商品材质、颜色和品牌标识；5. 适合商品主视觉。",
}


STYLE_LABELS = {
    "viral_first_frame": "爆款首帧",
    "lifestyle": "生活场景",
    "model": "模特展示",
    "detail": "细节特写",
    "flatlay": "平铺摆拍",
    "outdoor": "户外场景",
    "studio": "棚拍风格",
}


def render_template(template: str, variables: dict[str, Any]) -> str:
    text = str(template or "")
    for key, value in variables.items():
        text = text.replace("{" + key + "}", str(value if value not in (None, "") else "无"))
    return text.strip()


def image_prompt_snapshot(items: list[dict[str, str]]) -> str:
    blocks = []
    for item in items:
        style = item.get("style", "")
        label = STYLE_LABELS.get(style, style)
        prompt = item.get("prompt", "").strip()
        if prompt:
            blocks.append(f"[{label}/{style}]\n{prompt}")
    return "\n\n---\n\n".join(blocks)
