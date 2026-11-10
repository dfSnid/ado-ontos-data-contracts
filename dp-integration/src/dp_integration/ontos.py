"""A small client for the Ontos v1.0.2 REST API, and the ways to get a token for it.

Ontos runs as a Databricks App, and the Apps proxy accepts Databricks OAuth tokens only. In a
pipeline the publisher identity has an Entra token, so WorkspaceTokenExchange swaps it for a
Databricks token at the workspace token endpoint. No federation policy is needed on Azure
Databricks for an Entra token of a service principal the workspace knows.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

TokenProvider = Callable[[], str]


class OntosError(RuntimeError):
    """An Ontos call failed. `status` is the HTTP status, or None if no response came back."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class StaticToken:
    """A token you already have, for example ONTOS_TOKEN."""

    def __init__(self, token: str):
        if not token:
            raise ValueError("empty token")
        self._token = token

    def __call__(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return "StaticToken(***)"


class WorkspaceTokenExchange:
    """Exchanges an Entra token for a Databricks OAuth token (RFC 8693), cached until near expiry."""

    GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"

    def __init__(self, workspace_url: str, subject_token: TokenProvider,
                 session: requests.Session | None = None, timeout: float = 60):
        self.workspace_url = workspace_url.rstrip("/")
        self.subject_token, self.timeout = subject_token, timeout
        self.session = session or requests.Session()
        self._token, self._expires = "", 0.0

    def __call__(self) -> str:
        if self._token and time.monotonic() < self._expires:
            return self._token
        r = self.session.post(f"{self.workspace_url}/oidc/v1/token", timeout=self.timeout, data={
            "grant_type": self.GRANT,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "subject_token": self.subject_token(),
            "scope": "all-apis"})
        if not r.ok:
            raise OntosError(f"token exchange at {self.workspace_url}: HTTP {r.status_code} {r.text[:300]}",
                             r.status_code)
        body = r.json()
        self._token = body["access_token"]
        self._expires = time.monotonic() + max(int(body.get("expires_in", 3600)) - 300, 0)
        log.debug("exchanged an Entra token for a Databricks token (%ss)", body.get("expires_in"))
        return self._token

    def __repr__(self) -> str:
        return f"WorkspaceTokenExchange({self.workspace_url!r})"


class SdkToken:
    """The token of a databricks.sdk WorkspaceClient, for local runs with a CLI profile.
    The profile must be a Databricks OAuth login, because the Apps proxy rejects Entra tokens."""

    def __init__(self, ws):
        self.ws = ws

    def __call__(self) -> str:
        header = self.ws.config.authenticate().get("Authorization", "")
        return header.removeprefix("Bearer ")

    def __repr__(self) -> str:
        return f"SdkToken({self.ws.config.host!r})"


class OntosClient:
    """Typed calls to the Ontos endpoints the integration uses. Use it as a context manager to
    close the HTTP session at the end: `with OntosClient(url, token) as ontos: ...`."""

    def __init__(self, base_url: str, token: TokenProvider, session: requests.Session | None = None,
                 timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.token, self.timeout = token, timeout
        self.session = session or requests.Session()

    def call(self, method: str, path: str, body: Any = None) -> Any:
        log.debug("%s %s", method, path)
        try:
            r = self.session.request(method, self.base_url + path, json=body, timeout=self.timeout,
                                     headers={"Authorization": f"Bearer {self.token()}"})
        except requests.RequestException as e:
            raise OntosError(f"{method} {path}: {e}") from e
        if not r.ok:
            raise OntosError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        return r.json() if r.content else None

    # Data contracts
    def list_contracts(self, include_history: bool = True) -> list[dict]:
        return self.call("GET", f"/api/data-contracts?include_history={str(include_history).lower()}")

    def get_contract(self, contract_id: str) -> dict:
        return self.call("GET", f"/api/data-contracts/{contract_id}")

    def import_odcs(self, document: dict) -> dict:
        return self.call("POST", "/api/data-contracts/odcs/import", document)

    def new_version(self, contract_id: str, version: str) -> dict:
        """A draft copy of the contract header as `version`, linked to it as the next version."""
        return self.call("POST", f"/api/data-contracts/{contract_id}/versions", {"new_version": version})

    def update_contract(self, contract_id: str, body: dict) -> dict:
        return self.call("PUT", f"/api/data-contracts/{contract_id}", body)

    def delete_contract(self, contract_id: str) -> None:
        self.call("DELETE", f"/api/data-contracts/{contract_id}")

    def change_status(self, contract_id: str, status: str) -> dict:
        return self.call("POST", f"/api/data-contracts/{contract_id}/change-status", {"new_status": status})

    def add_quality_item(self, contract_id: str, item: dict) -> dict:
        body = dict(item, entity_type="data_contract", entity_id=contract_id)
        return self.call("POST", f"/api/entities/data_contract/{contract_id}/quality-items", body)

    # Data domains and teams
    def list_domains(self) -> list[dict]:
        return self.call("GET", "/api/data-domains")

    def create_domain(self, domain: dict) -> dict:
        return self.call("POST", "/api/data-domains", domain)

    def update_domain(self, domain_id: str, domain: dict) -> dict:
        return self.call("PUT", f"/api/data-domains/{domain_id}", domain)

    def list_teams(self) -> list[dict]:
        return self.call("GET", "/api/teams")

    def create_team(self, team: dict) -> dict:
        return self.call("POST", "/api/teams", team)

    def update_team(self, team_id: str, team: dict) -> dict:
        return self.call("PUT", f"/api/teams/{team_id}", team)

    def add_team_member(self, team_id: str, member: dict) -> dict:
        return self.call("POST", f"/api/teams/{team_id}/members", member)

    # Data products
    def list_products(self) -> list[dict]:
        return self.call("GET", "/api/data-products")

    def get_product(self, product_id: str) -> dict:
        return self.call("GET", f"/api/data-products/{product_id}")

    def create_product(self, product: dict) -> dict:
        return self.call("POST", "/api/data-products", product)

    def update_product(self, product_id: str, product: dict) -> dict:
        """A full replace, status included: Ontos applies the body's status without checking the
        transition, so send the current status and move it with change_product_status."""
        return self.call("PUT", f"/api/data-products/{product_id}", product)

    def new_product_version(self, product_id: str, version: str) -> dict:
        return self.call("POST", f"/api/data-products/{product_id}/versions", {"new_version": version})

    def change_product_status(self, product_id: str, status: str) -> dict:
        return self.call("POST", f"/api/data-products/{product_id}/change-status", {"new_status": status})

    def certify_product(self, product_id: str, level_order: int, notes: str | None = None) -> dict:
        """Needs the product active and the PRODUCTS approval privilege."""
        return self.call("POST", f"/api/data-products/{product_id}/certify",
                         {"certification_level": level_order, "notes": notes})

    def decertify_product(self, product_id: str, notes: str | None = None) -> dict:
        return self.call("POST", f"/api/data-products/{product_id}/decertify", {"notes": notes})

    def set_publication_scope(self, product_id: str, scope: str) -> dict:
        """none, domain, organization or external; anything but none needs the product active."""
        return self.call("POST", f"/api/data-products/{product_id}/set-publication-scope", {"scope": scope})

    def list_certification_levels(self) -> list[dict]:
        return self.call("GET", "/api/certification-levels")

    # Roles
    def list_roles(self) -> list[dict]:
        return self.call("GET", "/api/settings/roles")

    def create_role(self, role: dict) -> dict:
        return self.call("POST", "/api/settings/roles", role)

    def update_role(self, role: dict) -> dict:
        return self.call("PUT", f"/api/settings/roles/{role['id']}", role)

    # The caller
    def user_info(self) -> dict:
        return self.call("GET", "/api/user/info")

    def user_permissions(self) -> dict:
        return self.call("GET", "/api/user/permissions")

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> OntosClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"OntosClient({self.base_url!r})"
