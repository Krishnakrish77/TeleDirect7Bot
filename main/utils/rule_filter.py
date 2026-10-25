"""Shared JSON rule-tree evaluator.

A rule tree is ``{"all": [...]}`` / ``{"any": [...]}`` (may nest) whose
leaves are ``{"field": value, "op": operator, "value": operand}``. Operators
and field extraction are supplied by the caller so the same engine serves
smart playlists (track predicates) and other per-item filters without
either module importing the other.

Supported operators (whitelist — unknown ops are validation errors):
  eq / ne           equality (case-insensitive for strings)
  contains          substring, case-insensitive
  gt / gte / lt / lte   numeric comparison
  inTheLast         field is a datetime within N days (value: int days)
  isTrue / isFalse  boolean truthiness of the field
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

# Fields a rule may reference; each caller passes its own extractor map
# (field name -> callable(item) -> value). Values may be str/int/float/bool/
# datetime/None. A rule on a missing field is simply False, never an error.
STRING_OPS = {"eq", "ne", "contains"}
NUMBER_OPS = {"gt", "gte", "lt", "lte"}
ALL_OPS = STRING_OPS | NUMBER_OPS | {"inTheLast", "isTrue", "isFalse"}

_MAX_DEPTH = 3
_MAX_LEAVES = 12


class RuleValidationError(ValueError):
    """Raised when a rule tree references unknown fields/operators or is
    structurally invalid (too deep, too many leaves, wrong types)."""


def _norm_str(value: Any) -> str:
    return str(value or "").strip().lower()


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _leaf_matches(extract: Callable[[str], Any], field: str, op: str, value: Any) -> bool:
    actual = extract(field)

    # A list-valued field (e.g. a track's genre tags) matches when ANY
    # element matches the leaf.
    if isinstance(actual, list):
        return any(_leaf_matches(lambda _: element, field, op, value) for element in actual)

    if op == "isTrue":
        return bool(actual)
    if op == "isFalse":
        return not bool(actual)

    if op == "inTheLast":
        days = _as_number(value)
        if days is None or actual is None:
            return False
        if not isinstance(actual, datetime):
            return False
        aware = actual if actual.tzinfo else actual.replace(tzinfo=timezone.utc)
        return aware >= datetime.now(timezone.utc) - timedelta(days=days)

    if op in ("eq", "ne"):
        if isinstance(actual, str) or isinstance(value, str):
            matched = _norm_str(actual) == _norm_str(value)
        else:
            a_num, v_num = _as_number(actual), _as_number(value)
            matched = a_num is not None and a_num == v_num
        return matched if op == "eq" else not matched

    if op == "contains":
        return _norm_str(value) in _norm_str(actual)

    if op in NUMBER_OPS:
        a_num, v_num = _as_number(actual), _as_number(value)
        if a_num is None or v_num is None:
            return False
        if op == "gt":
            return a_num > v_num
        if op == "gte":
            return a_num >= v_num
        if op == "lt":
            return a_num < v_num
        return a_num <= v_num

    return False  # unreachable — validated against ALL_OPS


def _count_leaves(node: dict) -> int:
    if "all" in node or "any" in node:
        children = node.get("all") or node.get("any") or []
        if not isinstance(children, list):
            raise RuleValidationError("all/any must be lists")
        return sum(_count_leaves(child) for child in children)
    return 1


def _node_matches(node: dict, extract: Callable[[str], Any], depth: int = 0) -> bool:
    """Evaluate one rule node. Groups need ≥1 child (all([]) is vacuously
    true, any([]) is vacuously false — an empty group matches nothing useful,
    so we make both strict)."""
    if not isinstance(node, dict):
        raise RuleValidationError("rule nodes must be objects")

    if "all" in node or "any" in node:
        if depth >= _MAX_DEPTH:
            raise RuleValidationError(f"rule tree deeper than {_MAX_DEPTH} levels")
        if "all" in node and "any" in node:
            raise RuleValidationError("a node cannot be both all and any; nest one inside the other")
        children = node.get("all") if "all" in node else node.get("any")
        if not isinstance(children, list) or not children:
            raise RuleValidationError("all/any need a non-empty list of rules")
        results = (_node_matches(child, extract, depth + 1) for child in children)
        return all(results) if "all" in node else any(results)

    field = node.get("field")
    op = node.get("op")
    if not field or not isinstance(field, str):
        raise RuleValidationError("rule needs a string field")
    if op not in ALL_OPS:
        raise RuleValidationError(f"unknown operator: {op!r}")
    if op in NUMBER_OPS or op == "inTheLast":
        if _as_number(node.get("value")) is None:
            raise RuleValidationError(f"operator {op} needs a numeric value")
    return _leaf_matches(extract, field, op, node.get("value"))


def validate_rules(rules: Any, allowed_fields: set[str]) -> dict:
    """Validate a rule tree against ``allowed_fields``; returns the normalized
    tree (the input dict when already canonical). Raises RuleValidationError.
    """
    if not isinstance(rules, dict) or not rules:
        raise RuleValidationError("rules must be a non-empty object")
    leaves = _count_leaves(rules)
    if leaves > _MAX_LEAVES:
        raise RuleValidationError(f"too many rules (max {_MAX_LEAVES})")

    def _walk(node: dict, depth: int = 0) -> None:
        if not isinstance(node, dict):
            raise RuleValidationError("rule nodes must be objects")
        if "all" in node or "any" in node:
            if depth >= _MAX_DEPTH:
                raise RuleValidationError(f"rule tree deeper than {_MAX_DEPTH} levels")
            children = node.get("all") if "all" in node else node.get("any")
            if not isinstance(children, list) or not children:
                raise RuleValidationError("all/any need a non-empty list of rules")
            for child in children:
                _walk(child, depth + 1)
            return
        field = node.get("field")
        if field not in allowed_fields:
            raise RuleValidationError(f"unknown field: {field!r}")
        op = node.get("op")
        if op not in ALL_OPS:
            raise RuleValidationError(f"unknown operator: {op!r}")
        if op in NUMBER_OPS or op == "inTheLast":
            if _as_number(node.get("value")) is None:
                raise RuleValidationError(f"operator {op} needs a numeric value")

    _walk(rules)
    return rules


def matches(rules: dict, extract: Callable[[str], Any]) -> bool:
    """True when the item satisfies the rule tree. Validation errors are
    impossible here if the tree came from validate_rules; a malformed stored
    tree logs and returns False (fail-closed) rather than raising."""
    try:
        return _node_matches(rules, extract, depth=0)
    except RuleValidationError as exc:
        logging.warning("rule_filter: invalid stored rules rejected: %s", exc)
        return False
    except Exception:
        logging.exception("rule_filter: rule evaluation failed")
        return False
