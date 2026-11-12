"""Quality results from `datacontract test --output-format json`, one item per dimension.

The checks are advisory: the results are shown in Ontos, they never fail a run.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

log = logging.getLogger(__name__)

OTHER = "other"


@dataclass(frozen=True)
class QualityItem:
    title: str
    description: str
    dimension: str
    checks_passed: int
    checks_total: int
    measured_at: str

    @property
    def score_percent(self) -> float:
        return round(100.0 * self.checks_passed / self.checks_total, 1) if self.checks_total else 0.0

    def to_ontos(self, source: str) -> dict[str, Any]:
        """The body of an Ontos quality item (entity_type and entity_id are added by the caller)."""
        return dict(asdict(self), source=source, score_percent=self.score_percent)

    def __str__(self) -> str:
        return f"{self.dimension}: {self.score_percent}% ({self.checks_passed}/{self.checks_total})"


class QualityResults(Mapping[tuple[str, str], list[QualityItem]]):
    """{(contract id, version): [QualityItem]}. Empty when there were no quality runs."""

    def __init__(self, items: Mapping[tuple[str, str], list[QualityItem]] | None = None):
        self._items = dict(items or {})

    @classmethod
    def from_runs(cls, runs: Iterable[Mapping[str, Any]], dimensions: Iterable[str]) -> QualityResults:
        dimensions = set(dimensions)
        items = {}
        for run in runs:
            by_dimension: dict[str, list[bool]] = defaultdict(list)
            for check in run.get("checks") or []:
                dimension = check.get("dimension")
                by_dimension[dimension if dimension in dimensions else OTHER].append(check.get("result") == "passed")
            items[(str(run["dataContractId"]), str(run["dataContractVersion"]))] = [
                QualityItem(title=f"datacontract test: {dimension}",
                            description=f"Run {run.get('runId')} on server {run.get('server')}, "
                                        f"result {run.get('result')} (advisory)",
                            dimension=dimension, checks_passed=sum(results),
                            checks_total=len(results), measured_at=str(run.get("timestampEnd") or ""))
                for dimension, results in sorted(by_dimension.items())]
        return cls(items)

    @classmethod
    def from_dir(cls, directory: str | Path | None, dimensions: Iterable[str]) -> QualityResults:
        """Every datacontract JSON result in a folder. Other JSON files are skipped."""
        if not directory:
            return cls()
        runs = []
        for path in sorted(Path(directory).glob("*.json")):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                log.warning("not a JSON file, skipped: %s", e, extra={"source": str(path)})
                continue
            if isinstance(run, dict) and "dataContractId" in run:
                runs.append(run)
        return cls.from_runs(runs, dimensions)

    def for_contract(self, contract_id: str, version: Any) -> list[QualityItem]:
        return self._items.get((contract_id, str(version)), [])

    def __getitem__(self, key: tuple[str, str]) -> list[QualityItem]:
        return self._items[key]

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"QualityResults({len(self)} contract versions)"
