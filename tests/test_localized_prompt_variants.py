import unittest

from scripts.create_localized_prompt_variants import MARKETS, localized_content


class LocalizedPromptVariantTests(unittest.TestCase):
    def test_thailand_market_is_configured_for_thai_speech(self):
        self.assertIn(
            ("泰国", "泰语", "泰国", "泰语（自然地道的泰国本土口语）"),
            MARKETS,
        )

    def test_thailand_variant_prioritizes_localization_over_source_template(self):
        content = localized_content(
            "Philippines / Filipino / Tagalog / Taglish source structure",
            "泰国",
            "泰语（自然地道的泰国本土口语）",
        )

        required_rules = (
            "目标市场：泰国",
            "口播语言：泰语（自然地道的泰国本土口语）",
            "完整替换为泰国对应的真实生活语境",
            "不得在最终结果中残留菲律宾或 Filipino/Tagalog/Taglish 表述",
            "若本规则与源模板冲突，以本规则为准",
            "【结构源模板开始】",
            "【结构源模板结束】",
        )
        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, content)


if __name__ == "__main__":
    unittest.main()
