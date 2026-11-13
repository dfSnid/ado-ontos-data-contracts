"""Mandatory-field check: a JSON Schema overlay on top of ODCS (blocking in CI).

ODCS lint checks that a contract is valid ODCS. This check adds what the organisation requires
on top, such as a named Data Steward, and points each finding at the line it relates to.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


class LineIndex:
    """Line numbers of a YAML document by path. A path that is not in the document (a missing
    key) resolves to its deepest parent that is, which is where the key should be added."""

    def __init__(self, text: str):
        self._lines: dict[tuple, int] = {}
        root = yaml.compose(text)
        if root is not None:
            self._walk(root, ())

    def _walk(self, node: yaml.Node, path: tuple) -> None:
        self._lines[path] = node.start_mark.line + 1
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                self._walk(value, path + (key.value,))
        elif isinstance(node, yaml.SequenceNode):
            for i, value in enumerate(node.value):
                self._walk(value, path + (i,))

    def __getitem__(self, path) -> int:
        path = tuple(path)
        while path and path not in self._lines:
            path = path[:-1]
        return self._lines.get(path, 1)

    def __len__(self) -> int:
        return len(self._lines)


@dataclass(frozen=True)
class Finding:
    source: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: {self.message}"


class MandatoryFieldChecker:
    """`tiers`: the governance tiers this platform publishes (tags.tier_values), by number. A
    contract at any other tier is a finding, so a tier whose sign-off is not built cannot merge."""

    def __init__(self, schema: dict[str, Any], tiers: Mapping[int, str] | None = None,
                 tier_property: str = "governanceTier"):
        Draft202012Validator.check_schema(schema)
        self.schema = schema
        self._validator = Draft202012Validator(schema)
        self.tiers, self.tier_property = tiers, tier_property

    @classmethod
    def from_file(cls, path: str | Path, tiers: Mapping[int, str] | None = None,
                  tier_property: str = "governanceTier") -> MandatoryFieldChecker:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), tiers, tier_property)

    def check(self, path: str | Path) -> list[Finding]:
        """Every missing or invalid mandatory field in one contract file, in document order."""
        text = Path(path).read_text(encoding="utf-8")
        try:
            lines = LineIndex(text)
            document = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            return [Finding(str(path), mark.line + 1 if mark else 1, f"not valid YAML: {e}")]
        errors = sorted(self._validator.iter_errors(document),
                        key=lambda e: lines[e.absolute_path])
        findings = [Finding(str(path), lines[e.absolute_path], self.describe(e)) for e in errors]
        return findings + self._tier_findings(document, lines, str(path))

    def _tier_findings(self, document: Any, lines: LineIndex, source: str) -> list[Finding]:
        if self.tiers is None or not isinstance(document, dict):
            return []
        findings = []
        for i, prop in enumerate(document.get("customProperties") or []):
            if not isinstance(prop, dict) or prop.get("property") != self.tier_property:
                continue
            value = prop.get("value")
            if isinstance(value, int) and not isinstance(value, bool) and value not in self.tiers:
                allowed = ", ".join(f"{t} ({name})" for t, name in sorted(self.tiers.items()))
                findings.append(Finding(source, lines[("customProperties", i, "value")],
                                        f"{self.tier_property} {value} is not set up on this platform, which "
                                        f"publishes {allowed} (tags.tier_values in platform.yaml)"))
        return findings

    @staticmethod
    def describe(error: ValidationError) -> str:
        if error.validator == "contains":
            return f"missing {error.schema.get('errorMessage', 'a required item')}"
        if error.validator == "required":
            return error.message.replace("is a required property", "is mandatory")
        where = ".".join(str(p) for p in error.absolute_path) or "contract"
        return f"{where}: {error.message}"
