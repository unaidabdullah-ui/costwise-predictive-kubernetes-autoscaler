"""Client for the traffic generator's control API (behind ``kubectl port-forward``).

The control token is sent in the ``X-Control-Token`` header and never logged or
returned to the browser.
"""

from __future__ import annotations

from typing import Any

import requests


class GeneratorError(RuntimeError):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class GeneratorClient:
    def __init__(self, base_url: str, token: str, timeout: float = 3.0, session: requests.Session | None = None
                 ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"X-Control-Token": self._token} if method == "POST" else {}
        try:
            resp = self.session.request(method, f"{self.base_url}{path}", json=body, headers=headers,
                                        timeout=self.timeout)
        except requests.RequestException as exc:
            raise GeneratorError("The traffic generator is not reachable.",
                                 "Run .\\start-demo.ps1 (it starts the traffic-generator pod and its port-forward)."
                                 ) from exc
        if resp.status_code in (401, 403):
            raise GeneratorError("The traffic generator rejected the control token.",
                                 "Run .\\start-demo.ps1 again to re-sync the token from the costwise-secrets Secret.")
        if resp.status_code == 503:
            raise GeneratorError("The traffic generator has no control token configured.",
                                 "Run .\\start-demo.ps1 again (it creates the costwise-secrets Secret).")
        try:
            data = resp.json()
        except ValueError as exc:
            raise GeneratorError(f"Unexpected answer from the traffic generator (HTTP {resp.status_code}).") from exc
        if resp.status_code >= 400:
            raise GeneratorError(str(data.get("detail") or data))
        return data

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/control/status")

    def set_scenario(self, scenario: str) -> dict[str, Any]:
        return self._request("POST", "/control/scenario", {"scenario": scenario})

    def add_users(self, count: int = 300) -> dict[str, Any]:
        return self._request("POST", "/control/users", {"add": int(count)})
