import unittest


class AuthIdentityTests(unittest.TestCase):
    def resolver(self):
        try:
            from api.auth import resolve_operator
        except (ImportError, ModuleNotFoundError) as exc:
            self.fail(f"缺少多用户身份解析: {exc}")
        return resolve_operator

    def test_configured_token_resolves_named_operator_and_role(self):
        resolve_operator = self.resolver()

        operator = resolve_operator(
            "token-a",
            legacy_admin_token="legacy",
            users_json='{"token-a":{"name":"Alice","role":"operator"}}',
        )

        self.assertEqual(operator.id, "Alice")
        self.assertEqual(operator.role, "operator")

    def test_legacy_admin_token_remains_compatible(self):
        resolve_operator = self.resolver()

        operator = resolve_operator("legacy", legacy_admin_token="legacy", users_json="")

        self.assertEqual(operator.id, "legacy-admin")
        self.assertEqual(operator.role, "admin")

    def test_unknown_token_is_rejected(self):
        resolve_operator = self.resolver()

        self.assertIsNone(resolve_operator("bad", legacy_admin_token="legacy", users_json="{}"))


if __name__ == "__main__":
    unittest.main()

