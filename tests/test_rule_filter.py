"""Self-checks for the shared rule-filter engine (smart playlists).
Pure — no Mongo, no network."""

import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("MONGO_URI", "")

from main.utils import rule_filter  # noqa: E402


class RuleValidationTests(unittest.TestCase):
    def test_valid_tree_passes(self):
        tree = {"all": [
            {"field": "genre", "op": "eq", "value": "Rock"},
            {"any": [
                {"field": "year", "op": "gte", "value": 2000},
                {"field": "playCount", "op": "gt", "value": 5},
            ]},
        ]}
        self.assertIs(rule_filter.validate_rules(tree, {"genre", "year", "playCount"}), tree)

    def test_unknown_field_rejected(self):
        with self.assertRaises(rule_filter.RuleValidationError):
            rule_filter.validate_rules(
                {"all": [{"field": "hacker", "op": "eq", "value": 1}]},
                {"genre"},
            )

    def test_unknown_operator_rejected(self):
        with self.assertRaises(rule_filter.RuleValidationError):
            rule_filter.validate_rules(
                {"all": [{"field": "genre", "op": "dropTable", "value": 1}]},
                {"genre"},
            )

    def test_numeric_op_needs_numeric_value(self):
        with self.assertRaises(rule_filter.RuleValidationError):
            rule_filter.validate_rules(
                {"all": [{"field": "year", "op": "gt", "value": "recently"}]},
                {"year"},
            )

    def test_depth_limit(self):
        deep = {"field": "genre", "op": "eq", "value": "x"}
        for _ in range(5):
            deep = {"all": [deep]}
        with self.assertRaises(rule_filter.RuleValidationError):
            rule_filter.validate_rules(deep, {"genre"})

    def test_too_many_leaves(self):
        tree = {"all": [
            {"field": "genre", "op": "eq", "value": str(i)}
            for i in range(20)
        ]}
        with self.assertRaises(rule_filter.RuleValidationError):
            rule_filter.validate_rules(tree, {"genre"})

    def test_empty_group_rejected(self):
        with self.assertRaises(rule_filter.RuleValidationError):
            rule_filter.validate_rules({"all": []}, {"genre"})

    def test_all_and_any_same_node_rejected(self):
        with self.assertRaises(rule_filter.RuleValidationError):
            rule_filter.validate_rules(
                {"all": [], "any": []}, set(),
            )


class RuleMatchTests(unittest.TestCase):
    def extract(self, field: str):
        data = {
            "genre": ["Rock", "Pop"],
            "year": 1987,
            "playCount": 12,
            "loved": True,
        }
        return data.get(field)

    def test_string_eq_case_insensitive_list_field(self):
        self.assertTrue(rule_filter.matches(
            {"all": [{"field": "genre", "op": "eq", "value": "rock"}]}, self.extract))

    def test_list_field_any_match(self):
        self.assertTrue(rule_filter.matches(
            {"all": [{"field": "genre", "op": "eq", "value": "pop"}]}, self.extract))

    def test_list_field_no_match(self):
        self.assertFalse(rule_filter.matches(
            {"all": [{"field": "genre", "op": "eq", "value": "jazz"}]}, self.extract))

    def test_numeric_compare(self):
        self.assertTrue(rule_filter.matches(
            {"all": [{"field": "playCount", "op": "gt", "value": 10}]}, self.extract))
        self.assertFalse(rule_filter.matches(
            {"all": [{"field": "year", "op": "gte", "value": 1990}]}, self.extract))

    def test_nested_any(self):
        tree = {"all": [
            {"field": "year", "op": "eq", "value": 1987},
            {"any": [
                {"field": "playCount", "op": "lt", "value": 5},
                {"field": "playCount", "op": "gt", "value": 10},
            ]},
        ]}
        self.assertTrue(rule_filter.matches(tree, self.extract))

    def test_boolean_ops(self):
        self.assertTrue(rule_filter.matches(
            {"all": [{"field": "loved", "op": "isTrue", "value": None}]}, self.extract))
        self.assertFalse(rule_filter.matches(
            {"all": [{"field": "loved", "op": "isFalse", "value": None}]}, self.extract))

    def test_malformed_stored_tree_fails_closed(self):
        self.assertFalse(rule_filter.matches(
            {"all": [{"field": "genre", "op": "bogus", "value": 1}]}, self.extract))

    def test_missing_field_is_false(self):
        self.assertFalse(rule_filter.matches(
            {"all": [{"field": "rating", "op": "gte", "value": 1}]}, self.extract))


if __name__ == "__main__":
    unittest.main()
