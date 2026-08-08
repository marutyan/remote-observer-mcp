from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from remote_observer_mcp.errors import ObserverError

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}$")
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@:+,-]+$")


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    unit: str
    logs: bool = False


@dataclass(frozen=True, slots=True)
class RepoConfig:
    path: str
    secret_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContainerConfig:
    name: str
    logs: bool = False


@dataclass(frozen=True, slots=True)
class ProcessConfig:
    name: str


@dataclass(frozen=True, slots=True)
class HostConfig:
    host_id: str
    transport: str
    ssh_alias: str | None
    gpu: bool
    services: Mapping[str, ServiceConfig]
    repos: Mapping[str, RepoConfig]
    containers: Mapping[str, ContainerConfig]
    processes: Mapping[str, ProcessConfig]

    def service(self, resource_id: str) -> ServiceConfig:
        return _resource(self.services, resource_id, "service")

    def repo(self, resource_id: str) -> RepoConfig:
        return _resource(self.repos, resource_id, "repository")

    def container(self, resource_id: str) -> ContainerConfig:
        return _resource(self.containers, resource_id, "container")

    def process(self, resource_id: str) -> ProcessConfig:
        return _resource(self.processes, resource_id, "process")


@dataclass(frozen=True, slots=True)
class AppConfig:
    hosts: Mapping[str, HostConfig]

    def host(self, host_id: str) -> HostConfig:
        try:
            return self.hosts[host_id]
        except KeyError as error:
            raise ObserverError("unknown_host", f"unknown host: {host_id}") from error


def load_config(path: Path) -> AppConfig:
    try:
        with path.open("rb") as file:
            raw = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ObserverError("invalid_configuration", f"cannot load configuration: {error}") from error

    _ensure_keys(raw, {"hosts"}, "root")
    hosts_raw = _mapping(raw.get("hosts"), "hosts")
    hosts: dict[str, HostConfig] = {}
    for host_id, host_raw in hosts_raw.items():
        _validate_id(host_id, "host ID")
        hosts[host_id] = _parse_host(host_id, _mapping(host_raw, f"host {host_id}"))
    return AppConfig(hosts=MappingProxyType(hosts))


def _parse_host(host_id: str, raw: Mapping[str, Any]) -> HostConfig:
    _ensure_keys(
        raw,
        {"transport", "ssh_alias", "gpu", "services", "repos", "containers", "processes"},
        f"host {host_id}",
    )
    transport = raw.get("transport")
    if transport not in {"local", "ssh"}:
        _invalid(f"host {host_id}: transport must be 'local' or 'ssh'")

    ssh_alias = raw.get("ssh_alias")
    if transport == "ssh":
        if not isinstance(ssh_alias, str):
            _invalid(f"host {host_id}: ssh transport requires ssh_alias")
        _validate_id(ssh_alias, f"host {host_id} ssh_alias")
    elif ssh_alias is not None:
        _invalid(f"host {host_id}: local transport must not define ssh_alias")

    gpu = raw.get("gpu", False)
    if not isinstance(gpu, bool):
        _invalid(f"host {host_id}: gpu must be boolean")

    return HostConfig(
        host_id=host_id,
        transport=transport,
        ssh_alias=ssh_alias,
        gpu=gpu,
        services=_parse_services(raw.get("services", {}), host_id),
        repos=_parse_repos(raw.get("repos", {}), host_id),
        containers=_parse_containers(raw.get("containers", {}), host_id),
        processes=_parse_processes(raw.get("processes", {}), host_id),
    )


def _parse_services(raw: Any, host_id: str) -> Mapping[str, ServiceConfig]:
    result: dict[str, ServiceConfig] = {}
    for resource_id, value in _mapping(raw, f"host {host_id} services").items():
        _validate_id(resource_id, "service ID")
        section = _mapping(value, f"service {resource_id}")
        _ensure_keys(section, {"unit", "logs"}, f"service {resource_id}")
        unit = _safe_resource(section.get("unit"), f"service {resource_id} unit")
        logs = _boolean(section.get("logs", False), f"service {resource_id} logs")
        result[resource_id] = ServiceConfig(unit=unit, logs=logs)
    return MappingProxyType(result)


def _parse_repos(raw: Any, host_id: str) -> Mapping[str, RepoConfig]:
    result: dict[str, RepoConfig] = {}
    for resource_id, value in _mapping(raw, f"host {host_id} repos").items():
        _validate_id(resource_id, "repository ID")
        section = _mapping(value, f"repository {resource_id}")
        _ensure_keys(section, {"path", "secret_patterns"}, f"repository {resource_id}")
        path = section.get("path")
        if not isinstance(path, str) or not _SAFE_PATH_RE.fullmatch(path):
            _invalid(f"repository {resource_id}: path must match the v1 safe absolute-path grammar")
        patterns = section.get("secret_patterns", [])
        if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
            _invalid(f"repository {resource_id}: secret_patterns must be an array of strings")
        result[resource_id] = RepoConfig(path=path, secret_patterns=tuple(patterns))
    return MappingProxyType(result)


def _parse_containers(raw: Any, host_id: str) -> Mapping[str, ContainerConfig]:
    result: dict[str, ContainerConfig] = {}
    for resource_id, value in _mapping(raw, f"host {host_id} containers").items():
        _validate_id(resource_id, "container ID")
        section = _mapping(value, f"container {resource_id}")
        _ensure_keys(section, {"name", "logs"}, f"container {resource_id}")
        name = _safe_resource(section.get("name"), f"container {resource_id} name")
        logs = _boolean(section.get("logs", False), f"container {resource_id} logs")
        result[resource_id] = ContainerConfig(name=name, logs=logs)
    return MappingProxyType(result)


def _parse_processes(raw: Any, host_id: str) -> Mapping[str, ProcessConfig]:
    result: dict[str, ProcessConfig] = {}
    for resource_id, value in _mapping(raw, f"host {host_id} processes").items():
        _validate_id(resource_id, "process ID")
        section = _mapping(value, f"process {resource_id}")
        _ensure_keys(section, {"name"}, f"process {resource_id}")
        name = _safe_resource(section.get("name"), f"process {resource_id} name")
        result[resource_id] = ProcessConfig(name=name)
    return MappingProxyType(result)


def _resource(mapping: Mapping[str, Any], resource_id: str, kind: str) -> Any:
    try:
        return mapping[resource_id]
    except KeyError as error:
        raise ObserverError("unknown_resource", f"unknown {kind}: {resource_id}") from error


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _invalid(f"{context} must be a TOML table")
    return value


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        _invalid(f"{context} must be boolean")
    return value


def _safe_resource(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _RESOURCE_RE.fullmatch(value):
        _invalid(f"{context} contains unsupported characters")
    return value


def _validate_id(value: str, context: str) -> None:
    if not _ID_RE.fullmatch(value):
        _invalid(f"{context} contains unsupported characters")


def _ensure_keys(raw: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        _invalid(f"{context} contains unknown keys: {', '.join(sorted(unknown))}")


def _invalid(message: str) -> None:
    raise ObserverError("invalid_configuration", message)
