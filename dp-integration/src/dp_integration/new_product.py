"""Start a new data product from the template: `dpi new-product`.

Writes two sets of files from `product_template/`:

- into a data-contracts checkout: the contract, its access file and the product file, all at
  status draft (G1) and version 0.1.0;
- into a new folder: the product's Databricks Asset Bundle, a Lakeflow pipeline that applies the
  contract's rules as expectations, and its Azure DevOps deploy pipeline.

Every generated file passes the blocking CI checks as it stands, so the first pull request is
green; the producer then replaces the <placeholder> values. Nothing is ever overwritten.

Template files end in `.tmpl`. In their paths `__domain__`, `__contract__`, `__product__` and
`__module__` are replaced, and a leading `dot-` becomes `.`. In their text `{{key}}` is replaced;
an unknown key is an error, so a typo in a template cannot reach a generated file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "product_template"
VERSION = "0.1.0"

_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
_IDENT = re.compile(r"[a-z][a-z0-9_]*")
_KEY = re.compile(r"\{\{(\w+)\}\}")


class TemplateError(ValueError):
    pass


def _title(words: str) -> str:
    text = words.replace("-", " ").replace("_", " ")
    return text[:1].upper() + text[1:]


@dataclass(frozen=True)
class NewProduct:
    domain: str
    product: str
    contract: str
    dataset: str
    catalog_dev: str
    catalog_prod: str

    @classmethod
    def build(cls, domain: str, product: str, contract: str | None = None, dataset: str | None = None,
              catalog_dev: str | None = None, catalog_prod: str | None = None) -> NewProduct:
        """Fill the defaults and check every name; raises TemplateError on a bad one."""
        new = cls(domain=domain, product=product, contract=contract or product,
                  dataset=dataset or product.replace("-", "_"),
                  catalog_dev=catalog_dev or f"{domain}_dev", catalog_prod=catalog_prod or f"{domain}_prod")
        for label, value, rx, hint in (
                ("domain", new.domain, _IDENT, "lower case, digits and _"),
                ("product", new.product, _ID, "lower case words joined by -"),
                ("contract", new.contract, _ID, "lower case words joined by -"),
                ("dataset", new.dataset, _IDENT, "lower case, digits and _"),
                ("dev catalog", new.catalog_dev, _IDENT, "lower case, digits and _"),
                ("prod catalog", new.catalog_prod, _IDENT, "lower case, digits and _")):
            if not rx.fullmatch(value):
                raise TemplateError(f"{label} '{value}': use {hint}")
        return new

    @property
    def values(self) -> dict[str, str]:
        module = self.product.replace("-", "_")
        return {
            "domain": self.domain, "domain_title": _title(self.domain),
            "product": self.product, "product_name": _title(self.product), "port_name": _title(self.product),
            "contract": self.contract, "contract_name": _title(self.contract),
            "dataset": self.dataset, "module": module, "schema": module, "bundle": f"dp-{self.product}",
            "catalog_dev": self.catalog_dev, "catalog_prod": self.catalog_prod, "version": VERSION,
        }

    def files(self, part: str) -> dict[Path, str]:
        """The rendered files of one template part (`data-contracts` or `bundle`), by relative path."""
        values, root = self.values, TEMPLATE / part
        rendered = {}
        for src in sorted(root.rglob("*.tmpl")):
            rel = src.relative_to(root).as_posix()[: -len(".tmpl")]
            for key in ("domain", "contract", "product", "module"):
                rel = rel.replace(f"__{key}__", values[key])
            parts = [("." + p[4:]) if p.startswith("dot-") else p for p in rel.split("/")]
            rendered[Path(*parts)] = self._render(src.read_text(encoding="utf-8"), values, src.name)
        return rendered

    @staticmethod
    def _render(text: str, values: dict[str, str], name: str) -> str:
        def sub(m: re.Match) -> str:
            if m.group(1) not in values:
                raise TemplateError(f"{name}: unknown template key {{{{{m.group(1)}}}}}")
            return values[m.group(1)]
        return _KEY.sub(sub, text)

    def write(self, contracts_root: Path, bundle_dir: Path) -> list[Path]:
        """Write both parts; refuses before writing anything if a target file already exists."""
        planned = [(contracts_root / rel, text) for rel, text in self.files("data-contracts").items()]
        planned += [(bundle_dir / rel, text) for rel, text in self.files("bundle").items()]
        existing = [str(path) for path, _ in planned if path.exists()]
        if existing:
            raise TemplateError("already exists, nothing written: " + ", ".join(existing))
        for path, text in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return [path for path, _ in planned]

    def registry_entry(self, publisher_group: str) -> str:
        """The domains.yaml entry for a domain the registry does not have yet. The publisher's
        group is in every domain team, so the sync can see the drafts it creates in Ontos."""
        return (f"  - name: {self.domain}\n"
                f"    description: <What the {self.domain} domain covers.>\n"
                f"    team:\n"
                f"      name: {self.domain}-domain\n"
                f"      title: {_title(self.domain)} domain team\n"
                f"      groups: [dp-{self.domain}-stewards, dp-{self.domain}-producers, {publisher_group}]\n")
