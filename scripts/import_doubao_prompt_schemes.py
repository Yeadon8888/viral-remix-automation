import argparse
from pathlib import Path

from api.config import settings
from api.feishu import FeishuClient
from api.services.prompt_configs import DOUBAO_PROMPT_KEY, PromptConfigService


SCHEMES = (
    ("shoes", "mx_shoes_10s", "墨西哥鞋类 10 秒", "墨西哥", "西班牙语", "鞋类", "direct_prompt", "10"),
    ("clothing", "mx_clothing_10s", "墨西哥服装 10 秒", "墨西哥", "西班牙语", "服装", "direct_prompt", "10"),
    ("t2", "pain_physical_proof", "T2 痛点与物理反馈", "菲律宾", "菲律宾语", "通用", "meta_generate", "15"),
    ("t3", "multi_scene_proof", "T3 多场景价值证明", "菲律宾", "菲律宾语", "通用", "meta_generate", "15"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import reviewed Doubao Prompt source files as disabled drafts")
    for key, *_ in SCHEMES:
        parser.add_argument(f"--{key}-file", type=Path)
    parser.add_argument("--version", default="2026-08-06-v1")
    args = parser.parse_args()

    service = PromptConfigService(settings, FeishuClient(settings))
    existing = service.list_configs()
    for key, scheme_key, name, region, language, category, mode, duration in SCHEMES:
        source = getattr(args, f"{key}_file")
        if not source:
            continue
        content = source.read_text(encoding="utf-8").strip()
        if not content:
            raise RuntimeError(f"{source} 为空")
        duplicate = next((
            row for row in existing
            if row.get("方案Key") == scheme_key and row.get("版本") == args.version
        ), None)
        if duplicate:
            print(f"SKIP {scheme_key}: {duplicate.get('record_id')}")
            continue
        created = service.create({
            "Prompt名称": name,
            "模板Key": DOUBAO_PROMPT_KEY,
            "方案Key": scheme_key,
            "方案名称": name,
            "执行模式": mode,
            "版本": args.version,
            "是否启用": "停用",
            "目标地区": region,
            "目标语言": language,
            "适用品类": category,
            "视频时长": duration,
            "模板内容": content,
            "输出协议": "direct_text" if mode == "direct_prompt" else "json",
            "变量说明": "可选变量：{product_name}、{product_link}",
            "备注": f"从权威源文件导入：{source.name}；启用前需人工审核",
        })
        print(f"CREATED {scheme_key}: {created.get('record_id')}")


if __name__ == "__main__":
    main()
