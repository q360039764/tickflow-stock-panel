"""Provider registry."""
from __future__ import annotations

from app.data_providers.local_projects_provider import LocalProjectsProvider
from app.data_providers.tickflow_provider import TickFlowProvider

_PROVIDERS = {
    "local_projects": LocalProjectsProvider,
    "tickflow": TickFlowProvider,
}


def get_provider(name: str = "local_projects"):
    provider_cls = _PROVIDERS.get((name or "local_projects").lower())
    if provider_cls is None:
        raise ValueError(f"Unsupported data provider: {name}")
    return provider_cls()
