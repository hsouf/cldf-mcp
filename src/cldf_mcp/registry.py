"""Dataset registry: which CLDF releases to load, pinned by version and checksum."""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

ROLES = ("features", "catalog")


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    title: str
    role: str
    version: str
    base_url: str
    metadata: str
    files: dict[str, str | None]
    short_title: str = ""
    edition_note: str = ""
    repository: str = ""
    homepage: str = ""
    license: str = ""
    citation: str = ""
    names_table: str | None = None
    bibliography: str | None = None
    feature_parameters: tuple[str, ...] | None = None
    language_url: str | None = None
    feature_url: str | None = None
    contribution_url: str | None = None
    source_url: str | None = None
    local_path: str | None = None
    extra: dict = field(default_factory=dict)

    def url_for(self, filename: str) -> str:
        return self.base_url.rstrip("/") + "/" + filename


_KNOWN = {f for f in DatasetSpec.__dataclass_fields__} - {"id", "extra"}


def _spec(ds_id: str, raw: dict) -> DatasetSpec:
    missing = [k for k in ("title", "role", "version", "metadata") if k not in raw]
    if missing:
        raise ValueError(f"dataset {ds_id!r}: missing required keys {missing}")
    if raw["role"] not in ROLES:
        raise ValueError(f"dataset {ds_id!r}: role must be one of {ROLES}")
    if not raw.get("base_url") and not raw.get("local_path"):
        raise ValueError(f"dataset {ds_id!r}: needs base_url (download) or local_path (local CLDF directory)")
    kwargs = {k: v for k, v in raw.items() if k in _KNOWN}
    kwargs.setdefault("base_url", "")
    files = {name: (checksum or None) for name, checksum in (raw.get("files") or {}).items()}
    for required in (raw["metadata"],):
        files.setdefault(required, None)
    kwargs["files"] = files
    if "feature_parameters" in kwargs and kwargs["feature_parameters"] is not None:
        kwargs["feature_parameters"] = tuple(kwargs["feature_parameters"])
    extra = {k: v for k, v in raw.items() if k not in _KNOWN}
    return DatasetSpec(id=ds_id, extra=extra, **kwargs)


def load_registry(extra_config: Path | None = None) -> dict[str, DatasetSpec]:
    """Built-in pinned datasets, optionally extended/overridden by a user TOML file."""
    text = resources.files("cldf_mcp").joinpath("datasets.toml").read_text(encoding="utf-8")
    raw = tomllib.loads(text).get("datasets", {})
    if extra_config is not None:
        user = tomllib.loads(Path(extra_config).read_text(encoding="utf-8")).get("datasets", {})
        for ds_id, conf in user.items():
            if conf.get("enabled", True) is False:
                raw.pop(ds_id, None)
            else:
                raw[ds_id] = {**raw.get(ds_id, {}), **conf}
    specs = {ds_id: _spec(ds_id, conf) for ds_id, conf in raw.items()}
    catalogs = [s.id for s in specs.values() if s.role == "catalog"]
    if len(catalogs) > 1:
        raise ValueError(f"only one catalog dataset is supported, got {catalogs}")
    return specs


def fingerprint(specs: dict[str, DatasetSpec]) -> str:
    """Changes whenever a pinned release or loader-relevant setting changes."""
    payload = {
        ds_id: {
            "version": s.version,
            "files": s.files,
            "role": s.role,
            "feature_parameters": s.feature_parameters,
            "local_path": s.local_path,
            "names_table": s.names_table,
            "bibliography": s.bibliography,
        }
        for ds_id, s in sorted(specs.items())
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
