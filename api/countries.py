from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    prompt_region: str
    language_name: str
    language: str
    legacy_default: bool = False


COUNTRIES = (
    Country(
        code="MY",
        name="马来西亚",
        prompt_region="马来",
        language_name="马来语",
        language="Bahasa Malaysia（马来语）",
        legacy_default=True,
    ),
    Country(
        code="MY_EN",
        name="马来西亚",
        prompt_region="马来-英语",
        language_name="英语",
        language="English（英语）",
    ),
    Country(
        code="PH",
        name="菲律宾",
        prompt_region="菲律宾",
        language_name="菲律宾语",
        language="Filipino / Tagalog（菲律宾语）",
        legacy_default=True,
    ),
    Country(
        code="TH",
        name="泰国",
        prompt_region="泰国",
        language_name="泰语",
        language="ภาษาไทย（泰语）",
        legacy_default=True,
    ),
    Country(
        code="ID",
        name="印度尼西亚",
        prompt_region="印度尼西亚",
        language_name="印尼语",
        language="Bahasa Indonesia（印度尼西亚语）",
    ),
    Country(
        code="MX",
        name="墨西哥",
        prompt_region="墨西哥",
        language_name="西班牙语",
        language="Español de México（墨西哥西班牙语）",
    ),
)

COUNTRY_BY_CODE = {country.code: country for country in COUNTRIES}


def get_country(country_code: str | None) -> Country:
    code = str(country_code or "MY").strip().upper()
    country = COUNTRY_BY_CODE.get(code)
    if not country:
        supported = " / ".join(COUNTRY_BY_CODE)
        raise ValueError(f"不支持的国家：{code or '空'}，当前支持 {supported}")
    return country


def country_from_target(
    target_country: str | None,
    target_language: str | None,
    fallback_code: str = "MY",
) -> Country:
    country_name = str(target_country or "").strip()
    language_name = str(target_language or "").strip()
    if not country_name and not language_name:
        return get_country(fallback_code)
    matches = [
        country
        for country in COUNTRIES
        if (not country_name or country.name == country_name)
        and (not language_name or country.language_name == language_name)
    ]
    if len(matches) == 1:
        return matches[0]
    if country_name == "马来西亚" and not language_name:
        return get_country(fallback_code) if get_country(fallback_code).name == country_name else get_country("MY")
    raise ValueError(f"记录中的目标国家/语言无法识别：{country_name or '未填写'} / {language_name or '未填写'}")


def list_countries() -> list[dict[str, str | bool]]:
    return [asdict(country) for country in COUNTRIES]


def prompt_regions() -> set[str]:
    return {country.prompt_region for country in COUNTRIES}


def doubao_fallback_prompt(country: Country) -> str:
    return (
        f"你是 TikTok {country.name}带货视频 AI 导演。根据商品图生成适合{country.name}市场的 "
        f"Seedance 视频生成提示词；中文描述画面，口播和字幕使用{country.language}，严格保持商品一致性。"
    )
