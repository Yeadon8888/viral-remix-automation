import unittest

from api.countries import country_from_target


class RecordCountrySelectionTests(unittest.TestCase):
    def test_resolves_malaysia_english_separately(self):
        country = country_from_target("马来西亚", "英语", "MY")

        self.assertEqual(country.code, "MY_EN")

    def test_resolves_mixed_country_record(self):
        country = country_from_target("墨西哥", "西班牙语", "PH")

        self.assertEqual(country.code, "MX")

    def test_uses_selected_country_only_when_record_is_blank(self):
        country = country_from_target("", "", "PH")

        self.assertEqual(country.code, "PH")


if __name__ == "__main__":
    unittest.main()
