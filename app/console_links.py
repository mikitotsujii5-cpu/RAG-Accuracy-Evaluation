"""Build safe links from the App to the current Databricks Workspace UI."""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlparse, urlunparse

from settings import Settings


_TRUSTED_WORKSPACE_SUFFIXES = (
    ".azuredatabricks.net",
    ".cloud.databricks.com",
    ".gcp.databricks.com",
)
_AZURE_WORKSPACE_HOST = re.compile(r"^adb-(\d+)\.", re.IGNORECASE)


def build_console_links(settings: Settings) -> dict[str, str]:
    """Return server-owned Workspace links for feature badges in the App UI."""

    workspace = _workspace_target(settings)
    if workspace is None:
        return {}
    netloc, organization_id = workspace

    def console_url(path: str) -> str:
        query = urlencode({"o": organization_id}) if organization_id else ""
        return urlunparse(("https", netloc, path, "", query, ""))

    links: dict[str, str] = {
        "ai_parse_document": console_url("/sql/editor"),
        "fmapi": console_url("/ml/endpoints"),
    }

    if settings.uc_catalog and settings.uc_schema:
        catalog_path = _resource_path(settings.uc_catalog, settings.uc_schema)
        links["catalog"] = console_url(catalog_path)
        links["document_table"] = console_url(
            _resource_path(
                settings.uc_catalog,
                settings.uc_schema,
                "toyota_document_registry",
            )
        )
        links["evaluation_dataset"] = console_url(
            _resource_path(
                settings.uc_catalog,
                settings.uc_schema,
                "toyota_rag_eval_cases",
            )
        )

    try:
        volume_parts = settings.volume_path.strip("/").split("/")
    except Exception:
        volume_parts = []
    if len(volume_parts) == 4 and volume_parts[0] == "Volumes":
        links["volume"] = console_url(_resource_path(*volume_parts[1:]))

    profiles = settings.resolved_index_profiles
    profile = next(
        (
            item
            for item in profiles
            if item.index_name == settings.default_index_name
        ),
        profiles[0] if profiles else None,
    )
    if profile is not None:
        source_parts = profile.source_table.split(".")
        index_parts = profile.index_name.split(".")
        if len(source_parts) == 3:
            links["delta_table"] = console_url(_resource_path(*source_parts))
        if len(index_parts) == 3:
            index_url = console_url(_resource_path(*index_parts))
            links["ai_search"] = index_url
            links["index_profile"] = index_url

    if settings.prep_job_id:
        links["prep_job"] = console_url(
            f"/jobs/{quote(settings.prep_job_id, safe='')}"
        )
    if settings.eval_job_id:
        links["eval_job"] = console_url(
            f"/jobs/{quote(settings.eval_job_id, safe='')}"
        )
    if settings.mlflow_experiment_id:
        links["mlflow"] = console_url(
            "/ml/experiments/"
            + quote(settings.mlflow_experiment_id, safe="")
        )
    if settings.warehouse_id:
        links["sql_warehouse"] = console_url(
            "/sql/warehouses/" + quote(settings.warehouse_id, safe="")
        )
    return links


def _workspace_target(settings: Settings) -> tuple[str, str | None] | None:
    raw = settings.workspace_ui_host or settings.databricks_host
    if not raw:
        # DATABRICKS_HOST is a documented Apps system variable. Keep a SDK
        # fallback because some runtime/build combinations expose the same
        # resolved host only through the SDK configuration provider.
        try:
            from databricks.sdk import WorkspaceClient

            raw = WorkspaceClient().config.host
        except Exception:
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = urlparse(raw.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or not parsed.hostname.lower().endswith(_TRUSTED_WORKSPACE_SUFFIXES)
        ):
            return None
    except ValueError:
        return None

    hostname = parsed.hostname.lower()
    netloc = hostname + (":443" if parsed.port == 443 else "")
    azure_match = _AZURE_WORKSPACE_HOST.match(hostname)
    organization_id = azure_match.group(1) if azure_match else None
    return netloc, organization_id


def _resource_path(*parts: str) -> str:
    return "/explore/data/" + "/".join(quote(part, safe="") for part in parts)
