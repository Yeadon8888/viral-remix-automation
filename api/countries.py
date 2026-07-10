from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    prompt_region: str
    language: str


COUNTRIES = (
    Country(code="MY", name="马来西亚", prompt_region="马来", language="Bahasa Malaysia（马来语）"),
    Country(code="PH", name="菲律宾", prompt_region="菲律宾", language="Filipino / Tagalog（菲律宾语）"),
    Country(code="TH", name="泰国", prompt_region="泰国", language="ภาษาไทย（泰语）"),
)

COUNTRY_BY_CODE = {country.code: country for country in COUNTRIES}


def get_country(country_code: str | None) -> Country:
    code = str(country_code or "MY").strip().upper()
    country = COUNTRY_BY_CODE.get(code)
    if not country:
        supported = " / ".join(COUNTRY_BY_CODE)
        raise ValueError(f"不支持的国家：{code or '空'}，当前支持 {supported}")
    return country


def list_countries() -> list[dict[str, str]]:
    return [asdict(country) for country in COUNTRIES]


def doubao_fallback_prompt(country: Country) -> str:
    return (
        f"你是 TikTok {country.name}带货视频 AI 导演。根据商品图生成适合{country.name}市场的 "
        f"Seedance 视频生成提示词；中文描述画面，口播和字幕使用{country.language}，严格保持商品一致性。"
    )
