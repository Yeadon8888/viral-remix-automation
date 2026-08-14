from api.config import settings
from api.feishu import FeishuClient
from api.services.prompt_configs import PromptConfigService


MARKETS = (
    ("墨西哥", "西班牙语", "墨西哥", "Español de México（墨西哥西班牙语）"),
    ("马来", "马来语", "马来西亚", "Bahasa Melayu（马来语）"),
    ("马来-英语", "英语", "马来西亚", "Malaysian English（马来西亚英语）"),
    ("泰国", "泰语", "泰国", "泰语（自然地道的泰国本土口语）"),
)
SCHEMES = {"pain_physical_proof": "T2 痛点与物理反馈", "multi_scene_proof": "T3 多场景价值证明"}
VERSION = "2026-08-06-localized-v1"


def localized_content(source: str, country: str, language: str) -> str:
    header = f"""【本地化最高优先级规则】
目标市场：{country}
口播语言：{language}
以下菲律宾原始模板只提供 T2/T3 的结构、节奏、物理反馈与多场景证明方法。
执行时必须把其中所有菲律宾、Filipino、Tagalog、Taglish、菲律宾城市/住宅/天气/消费心理示例，完整替换为{country}对应的真实生活语境；不得在最终结果中残留菲律宾或 Filipino/Tagalog/Taglish 表述。
所有口播必须使用自然地道的{language}，不得混入源模板的菲律宾语示例。若本规则与源模板冲突，以本规则为准。

【结构源模板开始】
"""
    return f"{header}{source.strip()}\n【结构源模板结束】"


def main() -> None:
    service = PromptConfigService(settings, FeishuClient(settings))
    rows = service.list_configs()
    source_by_scheme = {
        key: next(
            row for row in rows
            if row.get("方案Key") == key and row.get("目标地区") == "菲律宾"
        )
        for key in SCHEMES
    }
    for region, language_name, country, spoken_language in MARKETS:
        for scheme_key, scheme_name in SCHEMES.items():
            existing = next((
                row for row in rows
                if row.get("方案Key") == scheme_key
                and row.get("目标地区") == region
                and row.get("目标语言") == language_name
                and row.get("版本") == VERSION
            ), None)
            if existing:
                print(f"SKIP {region}.{scheme_key}: {existing.get('record_id')}")
                continue
            source = source_by_scheme[scheme_key]
            created = service.create({
                "Prompt名称": f"{country}-{scheme_name}",
                "模板Key": "doubao_seedance_video_prompt",
                "方案Key": scheme_key,
                "方案名称": scheme_name,
                "执行模式": "meta_generate",
                "版本": VERSION,
                "是否启用": "启用",
                "目标地区": region,
                "目标语言": language_name,
                "适用品类": "通用",
                "视频时长": str(source.get("视频时长") or "15"),
                "模板内容": localized_content(str(source.get("模板内容") or ""), country, spoken_language),
                "输出协议": str(source.get("输出协议") or "json"),
                "变量说明": str(source.get("变量说明") or ""),
                "备注": f"由菲律宾 {scheme_key} 结构派生；本地化到{country}，最高优先级规则禁止残留菲律宾语言和场景",
            })
            rows.append(created)
            print(f"CREATED {region}.{scheme_key}: {created.get('record_id')}")


if __name__ == "__main__":
    main()
