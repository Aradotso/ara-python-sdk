"""Public Ara Python SDK core (provider-agnostic)."""

from __future__ import annotations

import argparse
import asyncio
import ast
import base64
import hashlib
import http.server
import inspect
import json
import logging
import os
import pathlib
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, NoReturn, Optional
from uuid import uuid4

DEFAULT_SUBAGENT_MAX_CONCURRENCY = 8
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 5
DEBUG_HTTP_ERRORS_ENV = "ARA_SDK_DEBUG_HTTP_ERRORS"
DEFAULT_API_BASE_URL = "https://api.ara.so"
CLI_CREDENTIALS_FILENAME = "credentials.json"
CLI_CREDENTIALS_DIRNAME = ".ara"
CLI_RUNTIME_KEYS_FILENAME = ".runtime-keys.local"
CLI_SSH_DIRNAME = "ssh"
CLI_SSH_ALIAS = "ara-personal"
CLI_SSH_KEY_BASENAME = "ara_personal_ed25519"
CLI_SSH_PROXY_TOKEN_FILENAME = "ara_personal_proxy_token"
CLI_WORKSPACE_PATH = "/root/.ara/workspace"
CONNECT_EXCHANGE_RETRY_DELAYS_SECONDS = (0.0, 4.0, 10.0, 20.0)
_JWT_REFRESH_SKEW_SECONDS = 30
_CLI_OAUTH_CALLBACK_HOST = "127.0.0.1"
_CLI_OAUTH_CALLBACK_PORT = 53682
_CLI_OAUTH_CALLBACK_PORT_ENV = "ARA_CLI_OAUTH_PORT"
_CLI_OAUTH_ALLOWED_PROVIDERS = frozenset(
    {
        "google",
        "github",
        "gitlab",
        "azure",
        "bitbucket",
        "discord",
        "facebook",
        "linkedin_oidc",
        "notion",
        "slack",
        "spotify",
        "twitch",
        "twitter",
    }
)
ALLOWED_SANDBOX_POLICIES = frozenset({"shared", "dedicated", "ephemeral", "inherited"})
ALLOWED_FASTAPI_ENDPOINT_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})
ALLOWED_ENDPOINT_AUTH_MODES = frozenset({"none", "header", "bearer", "hmac"})
MAGIC_NUMBER_SPAWN_DEFAULT_MAX_RECURSIVE_DEPTH = 1
MAGIC_NUMBER_SPAWN_HARD_MAX_RECURSIVE_DEPTH = 5
MAGIC_NUMBER_SPAWN_DEFAULT_MAX_CHILDREN_PER_PARENT = 6
MAGIC_NUMBER_SPAWN_HARD_MAX_CHILDREN_PER_PARENT = 24
MAGIC_NUMBER_SPAWN_DEFAULT_MAX_TOTAL_CHILD_SESSIONS_PER_RUN = 24
MAGIC_NUMBER_SPAWN_HARD_MAX_TOTAL_CHILD_SESSIONS_PER_RUN = 80
MAGIC_NUMBER_SPAWN_DEFAULT_EPHEMERAL_TTL_MINUTES = 10
MAGIC_NUMBER_SPAWN_HARD_MAX_EPHEMERAL_TTL_MINUTES = 240
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SECRET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]$")
PROJECT_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SCHEDULE_AT_TIME_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")
RESERVED_ENV_KEYS = frozenset({"SESSION_ID", "USER_ID", "APP_ID"})
RESERVED_ENV_PREFIXES = ("ARA_", "MODAL_")
logger = logging.getLogger(__name__)

_MINIMAL_DEFAULT_PROJECT_NAME = "automation-app"
_minimal_app_singleton: Optional["_AutomationApp"] = None
_minimal_app_uses_default_name = False
_minimal_app_owner_module = ""


def _slugify(value: str) -> str:
    out = []
    prev_dash = False
    for ch in str(value or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
            continue
        if not prev_dash:
            out.append("-")
            prev_dash = True
    slug = "".join(out).strip("-")
    return slug[:120]


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{ts}-{uuid4().hex[:8]}"


def _calling_module_name(*, depth: int = 2) -> str:
    frame = inspect.currentframe()
    try:
        cursor = frame
        steps = max(1, int(depth))
        for _ in range(steps):
            cursor = cursor.f_back if cursor is not None else None
        module_name = cursor.f_globals.get("__name__") if cursor is not None else ""
        return str(module_name or "").strip()
    finally:
        del frame


def _env_flag_enabled(key: str) -> bool:
    return str(os.getenv(key, "")).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_secret_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if not normalized or not SECRET_NAME_RE.match(normalized):
        raise ValueError("Secret name must match [a-z0-9][a-z0-9_-]{0,62}[a-z0-9]")
    return normalized


def _normalize_project_name(project_name: str) -> str:
    normalized = str(project_name or "").strip()
    if not normalized or not PROJECT_NAME_RE.match(normalized):
        raise ValueError(
            "project_name must match [a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])? "
            "(lowercase letters, digits, hyphens only; no underscores)"
        )
    return normalized


def _validate_env_key(key: str) -> str:
    normalized = str(key or "").strip()
    if not normalized:
        raise ValueError("Environment key cannot be empty")
    if not ENV_KEY_RE.match(normalized):
        raise ValueError(f"Invalid environment key: {normalized}")
    if normalized in RESERVED_ENV_KEYS or any(normalized.startswith(prefix) for prefix in RESERVED_ENV_PREFIXES):
        raise ValueError(f"Reserved environment key is not allowed: {normalized}")
    return normalized


def _stable_secret_suffix(values: dict[str, str]) -> str:
    key_fingerprint = json.dumps(sorted(values.keys()), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(key_fingerprint.encode("utf-8")).hexdigest()[:12]


def _generated_secret_name(prefix: str, values: dict[str, str]) -> str:
    return _normalize_secret_name(f"sdk-{prefix}-{_stable_secret_suffix(values)}")


def _normalize_required_keys(required_keys: Optional[list[str]]) -> list[str]:
    if not required_keys:
        return []
    if not isinstance(required_keys, list):
        raise ValueError("required_keys must be a list[str]")
    out: list[str] = []
    seen: set[str] = set()
    for item in required_keys:
        key = _validate_env_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect._empty or annotation is None:
        return {"type": "string"}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation in (dict,):
        return {"type": "object"}
    if annotation in (list, tuple, set):
        return {"type": "array"}
    origin = getattr(annotation, "__origin__", None)
    if origin in (dict,):
        return {"type": "object"}
    if origin in (list, tuple, set):
        return {"type": "array"}
    if origin is Callable:
        return {"type": "string"}
    return {"type": "string"}


def _callable_parameters_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in signature.parameters.values():
        if param.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue
        schema = _annotation_to_json_schema(param.annotation)
        if param.default is not inspect._empty:
            try:
                json.dumps(param.default)
                schema["default"] = param.default
            except TypeError:
                pass
        properties[param.name] = schema
        if param.default is inspect._empty:
            required.append(param.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _ensure_json_serializable(value: Any, *, context: str) -> None:
    try:
        json.dumps(value)
    except TypeError as exc:
        raise ValueError(f"{context} must be JSON-serializable") from exc


def _strip_leading_decorators(source: str) -> str:
    dedented = textwrap.dedent(source)
    try:
        module = ast.parse(dedented)
    except SyntaxError:
        return dedented.strip()

    lines = dedented.splitlines()
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return "\n".join(lines[node.lineno - 1 :]).strip()
    return dedented.strip()


def _extract_callable_source(fn: Callable[..., Any], *, context: str) -> str:
    source_override = getattr(fn, "__ara_source_override__", None)
    if isinstance(source_override, str) and source_override.strip():
        source = _strip_leading_decorators(source_override)
        if not source.startswith("def "):
            raise ValueError(f"{context} only supports standard def functions")
        return source
    try:
        raw_source = inspect.getsource(fn)
    except (OSError, TypeError):
        raise ValueError(f"{context} requires source-visible functions (no lambdas/dynamic defs)") from None
    source = _strip_leading_decorators(raw_source)
    if not source.startswith("def "):
        raise ValueError(f"{context} only supports standard def functions")
    return source


def _extract_secret_keys_from_source(source: str) -> list[str]:
    try:
        module = ast.parse(textwrap.dedent(str(source or "")))
    except SyntaxError:
        return []

    out: list[str] = []
    seen: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            fn_name = ""
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn_name = node.func.attr

            if fn_name == "secret" and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    candidate = first_arg.value.strip()
                    if candidate:
                        try:
                            key = _validate_env_key(candidate)
                        except ValueError:
                            key = ""
                        if key and key not in seen:
                            seen.add(key)
                            out.append(key)
            self.generic_visit(node)

    _Visitor().visit(module)
    return out


def _validate_agent_prompt_signature(fn: Callable[..., Any]) -> None:
    signature = inspect.signature(fn)
    params = [
        p
        for p in signature.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    if len(params) != 1:
        raise ValueError("@app.agent requires exactly one input parameter")
    return_annotation = signature.return_annotation
    if isinstance(return_annotation, str):
        normalized = return_annotation.strip().strip("'\"").lower()
        if normalized in {"str", "builtins.str"}:
            return
    if return_annotation not in (inspect._empty, str):
        raise ValueError("@app.agent return annotation must be str (or omitted)")


class SecretDefinition:
    def __init__(
        self,
        name: str,
        *,
        values: Optional[dict[str, str]] = None,
        required_keys: Optional[list[str]] = None,
        source: str,
    ):
        self.name = _normalize_secret_name(name)
        self.values = self._normalize_values(values)
        self.required_keys = _normalize_required_keys(required_keys)
        self.source = source

    @staticmethod
    def _normalize_values(values: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if values is None:
            return None
        if not isinstance(values, dict):
            raise ValueError("Secret values must be a dict[str, str]")
        if not values:
            raise ValueError("Secret values cannot be empty")
        out: dict[str, str] = {}
        for raw_key, raw_value in values.items():
            key = _validate_env_key(raw_key)
            out[key] = "" if raw_value is None else str(raw_value)
        return out

    @classmethod
    def from_name(cls, name: str, required_keys: Optional[list[str]] = None) -> "SecretDefinition":
        return cls(name, required_keys=required_keys, source="name")

    @classmethod
    def from_dict(
        cls,
        env_dict: dict[str, Any],
    ) -> "SecretDefinition":
        if not isinstance(env_dict, dict) or not env_dict:
            raise ValueError("from_dict requires a non-empty env_dict")
        values = {str(k): "" if v is None else str(v) for k, v in env_dict.items()}
        return cls(
            _generated_secret_name("dict", values),
            values=values,
            source="dict",
        )

    @classmethod
    def from_dotenv(
        cls,
        filename: str = ".env",
    ) -> "SecretDefinition":
        dotenv_path = pathlib.Path(filename)
        if not dotenv_path.exists() or not dotenv_path.is_file():
            raise ValueError(f"Secret dotenv file not found: {dotenv_path}")
        values: dict[str, str] = {}
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                values[key] = value
        if not values:
            raise ValueError(f"Secret dotenv file has no key=value entries: {dotenv_path}")
        return cls(_generated_secret_name("dotenv", values), values=values, source="dotenv")

    def ref(self) -> dict[str, Any]:
        out = {"name": self.name}
        if self.required_keys:
            out["required_keys"] = list(self.required_keys)
        return out


class Secret:
    @staticmethod
    def from_name(name: str, required_keys: Optional[list[str]] = None) -> SecretDefinition:
        return SecretDefinition.from_name(name, required_keys=required_keys)

    @staticmethod
    def from_dict(
        env_dict: dict[str, Any],
    ) -> SecretDefinition:
        return SecretDefinition.from_dict(env_dict)

    @staticmethod
    def from_dotenv(
        filename: str = ".env",
    ) -> SecretDefinition:
        return SecretDefinition.from_dotenv(filename=filename)


def _normalize_runtime_env_map(raw_env: Optional[dict[str, Any]]) -> dict[str, str]:
    if raw_env is None:
        return {}
    if not isinstance(raw_env, dict):
        raise ValueError("runtime(env=...) expects dict[str, str]")
    out: dict[str, str] = {}
    for raw_key, raw_value in raw_env.items():
        key = _validate_env_key(raw_key)
        out[key] = "" if raw_value is None else str(raw_value)
    return out


def _normalize_runtime_secrets(raw_secrets: Optional[list[Any]]) -> tuple[list[dict[str, Any]], list[SecretDefinition]]:
    if raw_secrets is None:
        return [], []
    if not isinstance(raw_secrets, list):
        raise ValueError("runtime(secrets=...) expects a list")
    refs: list[dict[str, Any]] = []
    definitions: list[SecretDefinition] = []
    seen_by_name: dict[str, SecretDefinition] = {}
    next_suffix_by_base: dict[str, int] = {}

    def _equivalent(a: SecretDefinition, b: SecretDefinition) -> bool:
        return (
            a.values == b.values
            and a.required_keys == b.required_keys
            and a.source == b.source
        )

    def _suffixed_name(base_name: str, suffix_number: int) -> str:
        suffix = f"-{suffix_number}"
        max_base_len = max(2, 64 - len(suffix))
        trimmed_base = base_name[:max_base_len].rstrip("-_")
        if len(trimmed_base) < 2:
            trimmed_base = (base_name[:max_base_len] or "sdk").ljust(2, "x")
        return _normalize_secret_name(f"{trimmed_base}{suffix}")

    for item in raw_secrets:
        if isinstance(item, SecretDefinition):
            definition = item
        elif isinstance(item, str):
            definition = SecretDefinition.from_name(item)
        elif isinstance(item, dict):
            definition = SecretDefinition.from_name(
                str(item.get("name") or ""),
                required_keys=item.get("required_keys") if isinstance(item.get("required_keys"), list) else None,
            )
        else:
            raise ValueError("runtime(secrets=...) items must be SecretDefinition, str, or dict")
        base_name = definition.name
        next_suffix_by_base.setdefault(base_name, 2)
        candidate = definition
        while True:
            existing = seen_by_name.get(candidate.name)
            if existing is None:
                seen_by_name[candidate.name] = candidate
                refs.append(candidate.ref())
                definitions.append(candidate)
                break
            if _equivalent(existing, candidate):
                break
            # Keep first reference-only declarations for the same name, matching
            # historical behavior for Secret.from_name(...) duplicates.
            if existing.values is None or candidate.values is None:
                break
            suffix_number = next_suffix_by_base[base_name]
            next_suffix_by_base[base_name] = suffix_number + 1
            candidate = SecretDefinition(
                _suffixed_name(base_name, suffix_number),
                values=candidate.values,
                required_keys=candidate.required_keys,
                source=candidate.source,
            )
    return refs, definitions


def _collect_runtime_secret_definitions(runtime_profile: dict[str, Any]) -> list[SecretDefinition]:
    if not isinstance(runtime_profile, dict):
        return []
    raw = runtime_profile.pop("__secret_definitions", [])
    if not isinstance(raw, list):
        return []
    out: list[SecretDefinition] = []
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, SecretDefinition):
            continue
        if item.name in seen_names:
            continue
        seen_names.add(item.name)
        out.append(item)
    return out


def file(path: str, content: str, *, executable: bool = False) -> dict[str, Any]:
    path_value = str(path or "").strip()
    if not path_value:
        raise ValueError("file() requires a non-empty path")
    return {"path": path_value, "content": str(content or ""), "executable": bool(executable)}


def local_file(
    source: str | pathlib.Path,
    path: Optional[str] = None,
    *,
    executable: bool = True,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    src = pathlib.Path(source)
    if not src.exists() or not src.is_file():
        raise ValueError(f"local_file() source not found: {src}")
    target = str(path or src.name).strip()
    if not target:
        raise ValueError("local_file() requires a non-empty target path")
    return file(target, src.read_text(encoding=encoding), executable=executable)


def entrypoint(command: str, *, shell: str = "bash", args: Optional[list[str]] = None) -> dict[str, Any]:
    cmd = str(command or "").strip()
    if not cmd:
        raise ValueError("entrypoint() requires a non-empty command")
    return {
        "entrypoint": cmd,
        "shell": str(shell or "bash").strip() or "bash",
        "args": [str(a).strip() for a in (args or []) if str(a).strip()],
    }


def runtime(
    *,
    files: Optional[list[dict[str, Any]]] = None,
    startup: Optional[dict[str, Any]] = None,
    image: Optional[str] = None,
    model: Optional[str] = None,
    memory_mb: Optional[int] = None,
    volume_size_mb: Optional[int] = None,
    python_packages: Optional[list[str]] = None,
    node_packages: Optional[list[str]] = None,
    env: Optional[dict[str, Any]] = None,
    secrets: Optional[list[Any]] = None,
) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    if files:
        profile["files"] = [dict(item) for item in files]
    if startup:
        profile["startup"] = dict(startup)
    if image:
        profile["image"] = str(image).strip()
    model_name = str(model or "").strip()
    if model_name:
        profile["model"] = model_name
    if memory_mb is not None:
        profile["memory_mb"] = int(memory_mb)
    if volume_size_mb is not None:
        profile["volume_size_mb"] = int(volume_size_mb)
    if python_packages:
        profile["python_packages"] = [str(pkg).strip() for pkg in python_packages if str(pkg).strip()]
    if node_packages:
        profile["node_packages"] = [str(pkg).strip() for pkg in node_packages if str(pkg).strip()]
    if env is not None:
        profile["env"] = _normalize_runtime_env_map(env)
    if secrets is not None:
        secret_refs, secret_defs = _normalize_runtime_secrets(secrets)
        profile["secret_refs"] = secret_refs
        if secret_defs:
            profile["__secret_definitions"] = secret_defs
    return profile


def sandbox(
    *,
    policy: str = "shared",
    max_concurrency: Optional[int] = None,
    idle_ttl_minutes: Optional[int] = None,
    key: Optional[str] = None,
    allow_spawn: Optional[bool] = None,
    spawn_to: Optional[list[str]] = None,
    max_spawn_depth: Optional[int] = None,
    max_children_per_parent: Optional[int] = None,
    max_total_child_sessions_per_run: Optional[int] = None,
    ephemeral_ttl_minutes: Optional[int] = None,
    child_policy: Optional[str] = None,
    child_runtime: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    normalized_policy = str(policy or "shared").strip().lower()
    if normalized_policy not in ALLOWED_SANDBOX_POLICIES:
        allowed = ", ".join(sorted(ALLOWED_SANDBOX_POLICIES))
        raise ValueError(f"sandbox(policy=...) must be one of: {allowed}")
    out: dict[str, Any] = {"policy": normalized_policy}
    key_value = str(key or "").strip()
    if key_value:
        out["key"] = key_value
    out["max_concurrency"] = max(1, int(max_concurrency or DEFAULT_SUBAGENT_MAX_CONCURRENCY))
    if idle_ttl_minutes is not None:
        out["idle_ttl_minutes"] = max(1, int(idle_ttl_minutes))
    if child_policy is not None:
        normalized_child_policy = str(child_policy or "").strip().lower()
        if normalized_child_policy not in ALLOWED_SANDBOX_POLICIES:
            allowed = ", ".join(sorted(ALLOWED_SANDBOX_POLICIES))
            raise ValueError(f"sandbox(child_policy=...) must be one of: {allowed}")
    if max_spawn_depth is not None:
        max_spawn_depth = max(0, int(max_spawn_depth))
    if spawn_to is not None and not isinstance(spawn_to, list):
        raise ValueError("sandbox(spawn_to=...) expects a list[str]")
    targets = [str(target).strip() for target in (spawn_to or []) if str(target).strip()]
    if allow_spawn is False:
        spawn_enabled = False
    else:
        spawn_enabled = bool(allow_spawn) or bool(targets)
    if spawn_enabled:
        spawn_cfg: dict[str, Any] = {"allow": True, "to": targets}
        if max_spawn_depth is None:
            depth = MAGIC_NUMBER_SPAWN_DEFAULT_MAX_RECURSIVE_DEPTH
        else:
            depth = max(0, int(max_spawn_depth))
        if depth > MAGIC_NUMBER_SPAWN_HARD_MAX_RECURSIVE_DEPTH:
            raise ValueError(
                "sandbox(max_spawn_depth=...) exceeds hard limit "
                f"{MAGIC_NUMBER_SPAWN_HARD_MAX_RECURSIVE_DEPTH}"
            )
        spawn_cfg["max_depth"] = depth
        child_limit = max(
            1,
            int(max_children_per_parent or MAGIC_NUMBER_SPAWN_DEFAULT_MAX_CHILDREN_PER_PARENT),
        )
        if child_limit > MAGIC_NUMBER_SPAWN_HARD_MAX_CHILDREN_PER_PARENT:
            raise ValueError(
                "sandbox(max_children_per_parent=...) exceeds hard limit "
                f"{MAGIC_NUMBER_SPAWN_HARD_MAX_CHILDREN_PER_PARENT}"
            )
        spawn_cfg["max_children_per_parent"] = child_limit
        total_limit = max(
            1,
            int(
                max_total_child_sessions_per_run
                or MAGIC_NUMBER_SPAWN_DEFAULT_MAX_TOTAL_CHILD_SESSIONS_PER_RUN
            ),
        )
        if total_limit > MAGIC_NUMBER_SPAWN_HARD_MAX_TOTAL_CHILD_SESSIONS_PER_RUN:
            raise ValueError(
                "sandbox(max_total_child_sessions_per_run=...) exceeds hard limit "
                f"{MAGIC_NUMBER_SPAWN_HARD_MAX_TOTAL_CHILD_SESSIONS_PER_RUN}"
            )
        spawn_cfg["max_total_child_sessions_per_run"] = total_limit
        ttl_minutes = max(
            1,
            int(ephemeral_ttl_minutes or MAGIC_NUMBER_SPAWN_DEFAULT_EPHEMERAL_TTL_MINUTES),
        )
        if ttl_minutes > MAGIC_NUMBER_SPAWN_HARD_MAX_EPHEMERAL_TTL_MINUTES:
            raise ValueError(
                "sandbox(ephemeral_ttl_minutes=...) exceeds hard limit "
                f"{MAGIC_NUMBER_SPAWN_HARD_MAX_EPHEMERAL_TTL_MINUTES}"
            )
        spawn_cfg["ephemeral_ttl_minutes"] = ttl_minutes
        if child_policy is not None:
            spawn_cfg["child_policy"] = normalized_child_policy
        if child_runtime is not None:
            cr = dict(child_runtime)
            cr.pop("__secret_definitions", None)
            spawn_cfg["child_runtime"] = cr
        out["spawn"] = spawn_cfg
    return out


def git_artifact(
    repo_url: str,
    *,
    ref: str = "main",
    subdir: str = "",
) -> dict[str, Any]:
    url = str(repo_url or "").strip()
    if not url:
        raise ValueError("git_artifact() requires a non-empty repo_url")
    return {
        "type": "git",
        "repo_url": url,
        "ref": str(ref or "main").strip() or "main",
        "subdir": str(subdir or "").strip(),
    }


def tarball_artifact(
    url: str,
    *,
    strip_prefix: str = "",
) -> dict[str, Any]:
    source = str(url or "").strip()
    if not source:
        raise ValueError("tarball_artifact() requires a non-empty url")
    return {
        "type": "tarball",
        "url": source,
        "strip_prefix": str(strip_prefix or "").strip(),
    }


def command_adapter(
    entrypoint: str,
    *,
    framework: str = "custom",
    transport: str = "stdio",
    args: Optional[list[str]] = None,
    artifact: Optional[dict[str, Any]] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    command = str(entrypoint or "").strip()
    if not command:
        raise ValueError("command_adapter() requires a non-empty entrypoint")
    out: dict[str, Any] = {
        "type": "command",
        "framework": str(framework or "custom").strip() or "custom",
        "transport": str(transport or "stdio").strip() or "stdio",
        "entrypoint": command,
        "args": [str(arg).strip() for arg in (args or []) if str(arg).strip()],
    }
    if artifact and isinstance(artifact, dict):
        out["artifact"] = dict(artifact)
    if env and isinstance(env, dict):
        out["env"] = {str(k).strip(): str(v) for k, v in env.items() if str(k).strip()}
    return out


def _framework_adapter(
    framework: str,
    entrypoint: str,
    *,
    transport: str = "stdio",
    args: Optional[list[str]] = None,
    artifact: Optional[dict[str, Any]] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    merged_env = {"AGENT_FRAMEWORK": framework}
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items() if str(k).strip()})
    return command_adapter(
        entrypoint,
        framework=framework,
        transport=transport,
        args=args,
        artifact=artifact,
        env=merged_env,
    )


def langgraph_adapter(
    entrypoint: str = "python3 langgraph_worker.py",
    *,
    transport: str = "stdio",
    args: Optional[list[str]] = None,
    artifact: Optional[dict[str, Any]] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    return _framework_adapter(
        "langgraph", entrypoint, transport=transport, args=args, artifact=artifact, env=env,
    )


def langchain_adapter(
    entrypoint: str = "python3 langchain_worker.py",
    *,
    transport: str = "stdio",
    args: Optional[list[str]] = None,
    artifact: Optional[dict[str, Any]] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    return _framework_adapter(
        "langchain", entrypoint, transport=transport, args=args, artifact=artifact, env=env,
    )


def agno_adapter(
    entrypoint: str = "python3 agno_worker.py",
    *,
    transport: str = "stdio",
    args: Optional[list[str]] = None,
    artifact: Optional[dict[str, Any]] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    return _framework_adapter(
        "agno", entrypoint, transport=transport, args=args, artifact=artifact, env=env,
    )


def event_envelope(
    event_type: str,
    *,
    source: str = "api",
    channel: str = "api",
    message: str = "",
    payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    event_name = str(event_type or "").strip()
    if not event_name:
        raise ValueError("event_envelope() requires a non-empty event_type")
    meta = dict(metadata or {})
    run_id = str(meta.get("run_id") or "").strip() or _new_run_id()
    meta["run_id"] = run_id
    if not str(meta.get("idempotency_key") or "").strip():
        meta["idempotency_key"] = f"{_slugify(event_name)}-{_slugify(run_id)}"
    return {
        "event": {
            "type": event_name,
            "source": str(source or "api").strip() or "api",
            "channel": str(channel or "api").strip() or "api",
            "message": str(message or ""),
            "payload": dict(payload or {}),
            "metadata": meta,
        }
    }


# =============================================================================
# TODO(ARA-SDK-WEB-ENDPOINTS): Add @ara.asgi_app and @ara.wsgi_app support.
# -----------------------------------------------------------------------------
# This SDK pass intentionally implements only @ara.fastapi_endpoint to keep the
# first HTTP endpoint abstraction narrow and predictable.
#
# Planned follow-ups:
# - @ara.asgi_app(...) for mounting full FastAPI/Starlette applications
# - @ara.wsgi_app(...) for Flask/Django compatibility
# - endpoint router manifest shape compatible with all three endpoint types
# =============================================================================
def _normalize_fastapi_endpoint_spec(spec: Any, *, default_label: str = "") -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("fastapi_endpoint(...) expects a configuration dict")

    method = str(spec.get("method") or "POST").strip().upper() or "POST"
    if method not in ALLOWED_FASTAPI_ENDPOINT_METHODS:
        allowed = ", ".join(sorted(ALLOWED_FASTAPI_ENDPOINT_METHODS))
        raise ValueError(f"fastapi_endpoint(method=...) must be one of: {allowed}")

    raw_label = str(spec.get("label") or default_label or "").strip()
    normalized_label = _slugify(raw_label)
    if not normalized_label:
        raise ValueError(
            "fastapi_endpoint(...) requires a non-empty label or a function name "
            "that can be slugified"
        )

    path_value = str(spec.get("path") or "").strip()
    if path_value:
        if not path_value.startswith("/"):
            path_value = f"/{path_value}"
        path_value = re.sub(r"/{2,}", "/", path_value)
    else:
        path_value = f"/{normalized_label}"

    auth_mode = str(spec.get("auth") or "none").strip().lower() or "none"
    if auth_mode not in ALLOWED_ENDPOINT_AUTH_MODES:
        allowed = ", ".join(sorted(ALLOWED_ENDPOINT_AUTH_MODES))
        raise ValueError(f"fastapi_endpoint(auth=...) must be one of: {allowed}")

    auth_header_name = str(spec.get("auth_header_name") or "").strip()
    if auth_mode == "header" and not auth_header_name:
        auth_header_name = "X-Ara-Endpoint-Secret"

    auth_secret_env = str(spec.get("auth_secret_env") or "").strip()
    if auth_mode != "none" and not auth_secret_env:
        auth_secret_env = f"ARA_ENDPOINT_{normalized_label.upper().replace('-', '_')}_SECRET"

    return {
        "type": "fastapi_endpoint",
        "method": method,
        "path": path_value,
        "label": normalized_label,
        "docs": bool(spec.get("docs", False)),
        "auth": {
            "mode": auth_mode,
            "header_name": auth_header_name,
            "secret_env": auth_secret_env,
        },
    }


def fastapi_endpoint(
    *,
    method: str = "POST",
    path: str = "",
    label: str = "",
    auth: str = "none",
    auth_header_name: str = "",
    auth_secret_env: str = "",
    docs: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    raw_spec = {
        "method": method,
        "path": path,
        "label": label,
        "auth": auth,
        "auth_header_name": auth_header_name,
        "auth_secret_env": auth_secret_env,
        "docs": docs,
    }

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        endpoint_spec = _normalize_fastapi_endpoint_spec(raw_spec, default_label=fn.__name__)
        setattr(fn, "__ara_fastapi_endpoint__", endpoint_spec)
        existing_agent = getattr(fn, "__ara_agent__", None)
        if isinstance(existing_agent, dict):
            existing_agent["fastapi_endpoint"] = dict(endpoint_spec)
        return fn

    return decorator


ScheduleRunSpec = dict[str, Any]
ScheduleSpec = dict[str, Any]


def _normalize_string_items(raw: Optional[list[str]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_connector_toolkit_slug(value: str) -> str:
    lowered = str(value or "").strip().lower()
    slug = "".join(ch for ch in lowered if ch.isalnum())
    if not slug:
        raise ValueError("Connector toolkit must contain letters or digits")
    return slug


def _normalize_connector_action_name(value: str) -> str:
    lowered = str(value or "").strip().lower()
    action = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    if not action:
        raise ValueError("Connector action must contain letters or digits")
    return action


class _ConnectorSkillRef:
    __slots__ = ("toolkit", "action")

    def __init__(self, toolkit: str, action: str = ""):
        self.toolkit = _normalize_connector_toolkit_slug(toolkit)
        self.action = _normalize_connector_action_name(action) if action else ""

    def __getattr__(self, name: str) -> "_ConnectorSkillRef":
        if name.startswith("_"):
            raise AttributeError(name)
        if self.action:
            raise AttributeError("Connector action already selected")
        return _ConnectorSkillRef(self.toolkit, _normalize_connector_action_name(name))

    def as_token(self) -> str:
        if self.action:
            return f"connector:{self.toolkit}:{self.action}"
        return f"connector:{self.toolkit}"

    def __str__(self) -> str:
        return self.as_token()

    def __repr__(self) -> str:
        if self.action:
            return f"<ara.connectors.{self.toolkit}.{self.action}>"
        return f"<ara.connectors.{self.toolkit}>"


class _ConnectorsNamespace:
    def __getattr__(self, name: str) -> _ConnectorSkillRef:
        if name.startswith("_"):
            raise AttributeError(name)
        return _ConnectorSkillRef(_normalize_connector_toolkit_slug(name))


connectors = _ConnectorsNamespace()


def _parse_connector_skill_token(token: str) -> Optional[tuple[str, str]]:
    text = str(token or "").strip()
    if not text:
        return None
    for prefix in ("connector:", "composio:"):
        if not text.startswith(prefix):
            continue
        remainder = text[len(prefix) :]
        toolkit_raw, _, action_raw = remainder.partition(":")
        toolkit = _normalize_connector_toolkit_slug(toolkit_raw)
        action = _normalize_connector_action_name(action_raw) if action_raw else ""
        return toolkit, action
    return None


def _normalize_automation_skill_items(raw: Optional[list[Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        if isinstance(item, _ConnectorSkillRef):
            value = item.as_token()
        else:
            value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _connector_refs_from_skill_items(skills: list[str]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in skills:
        parsed = _parse_connector_skill_token(item)
        if not parsed:
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        refs.append(parsed)
    return refs


def _merge_connector_tool_privileges(
    existing: Any,
    connector_refs: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    action_map: dict[str, Optional[set[str]]] = {}
    scopes_map: dict[str, list[str]] = {}

    if isinstance(existing, list):
        for row in existing:
            if not isinstance(row, dict):
                continue
            toolkit_raw = str(row.get("toolkit") or row.get("slug") or "").strip()
            if not toolkit_raw:
                continue
            toolkit = _normalize_connector_toolkit_slug(toolkit_raw)
            allowed_actions_raw = row.get("allowed_actions")
            if isinstance(allowed_actions_raw, list) and allowed_actions_raw:
                allowed_actions: set[str] = set()
                for action in allowed_actions_raw:
                    try:
                        allowed_actions.add(_normalize_connector_action_name(action))
                    except ValueError:
                        continue
                if not allowed_actions:
                    raise ValueError(
                        "tool_privileges allowed_actions contained no valid connector action names"
                    )
                action_map[toolkit] = allowed_actions
            else:
                action_map[toolkit] = None
            scopes_raw = row.get("scopes")
            if isinstance(scopes_raw, list):
                scopes_map[toolkit] = _normalize_string_items([str(scope or "").strip() for scope in scopes_raw])

    for toolkit, action in connector_refs:
        existing_actions = action_map.get(toolkit)
        if not action:
            action_map[toolkit] = None
            continue
        if existing_actions is None and toolkit in action_map:
            continue
        if existing_actions is None:
            existing_actions = set()
        existing_actions.add(action)
        action_map[toolkit] = existing_actions

    out: list[dict[str, Any]] = []
    for toolkit in sorted(action_map.keys()):
        actions = action_map[toolkit]
        out.append(
            {
                "toolkit": toolkit,
                "allowed_actions": sorted(actions) if isinstance(actions, set) else [],
                "scopes": list(scopes_map.get(toolkit, [])),
            }
        )
    return out


def _normalize_schedule_run(run: Any) -> ScheduleRunSpec:
    if not isinstance(run, dict):
        raise ValueError("schedule run must be a dict")
    run_type = str(run.get("type") or "").strip().lower()
    if run_type == "agent":
        agent_id = str(run.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError("invoke.agent(...) requires non-empty agent id")
        normalized: ScheduleRunSpec = {"type": "agent", "agent_id": agent_id}
        if "input" in run:
            input_payload = run["input"]
            _ensure_json_serializable(input_payload, context="invoke.agent(..., input=...)")
            normalized["input"] = input_payload
        return normalized
    if run_type == "tool":
        tool_name = str(run.get("tool_name") or run.get("tool") or "").strip()
        if not tool_name:
            raise ValueError("invoke.tool(...) requires non-empty tool name")
        args = run.get("args") if isinstance(run.get("args"), dict) else {}
        return {"type": "tool", "tool_name": tool_name, "args": dict(args)}
    raise ValueError("schedule run type must be 'agent' or 'tool'")


def _normalize_schedule_spec(spec: Any) -> ScheduleSpec:
    if not isinstance(spec, dict):
        raise ValueError("schedule spec must be a dict")
    schedule_id = str(spec.get("id") or "").strip()
    if not schedule_id:
        raise ValueError("schedule id is required")
    kind = str(spec.get("kind") or "").strip().lower()
    if kind not in {"cron", "every"}:
        raise ValueError("schedule kind must be 'cron' or 'every'")
    run = _normalize_schedule_run(spec.get("run"))
    if kind == "cron":
        expr = str(spec.get("cron") or spec.get("expr") or "").strip()
        if not expr:
            raise ValueError("cron schedule requires expr/cron")
        timezone_name = str(spec.get("timezone") or "UTC").strip() or "UTC"
        out = {
            "id": schedule_id,
            "kind": "cron",
            "cron": expr,
            "timezone": timezone_name,
            "run": run,
        }
        raw_one_shot_at = spec.get("one_shot_at")
        if raw_one_shot_at is not None and str(raw_one_shot_at).strip():
            try:
                parsed_one_shot_at = datetime.fromisoformat(str(raw_one_shot_at).strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("cron schedule one_shot_at must be a valid ISO8601 timestamp") from exc
            if parsed_one_shot_at.tzinfo is None:
                parsed_one_shot_at = parsed_one_shot_at.replace(tzinfo=timezone.utc)
            out["one_shot_at"] = parsed_one_shot_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return out
    seconds_raw = spec.get("every_seconds", spec.get("seconds"))
    try:
        seconds = int(seconds_raw)
    except (TypeError, ValueError):
        raise ValueError("every schedule requires integer seconds") from None
    if seconds < 60:
        raise ValueError("every schedule minimum interval is 60 seconds")
    return {
        "id": schedule_id,
        "kind": "every",
        "every_seconds": seconds,
        "run": run,
    }


def _schedule_entry_suffix(raw: str, fallback: str) -> str:
    suffix = _slugify(raw)
    return suffix or fallback


def _cron_from_at_token(raw_token: Any, *, default_timezone_name: str) -> dict[str, Any]:
    token = str(raw_token or "").strip()
    if not token:
        raise ValueError("app.schedule(at=...) entries cannot be empty")
    if len(token.split()) == 5:
        return {
            "kind": "cron",
            "cron": token,
            "timezone": default_timezone_name,
        }
    time_match = SCHEDULE_AT_TIME_RE.match(token)
    if time_match:
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
        if hour > 23 or minute > 59:
            raise ValueError("app.schedule(at=...) HH:MM values must be within 00:00-23:59")
        return {
            "kind": "cron",
            "cron": f"{minute} {hour} * * *",
            "timezone": default_timezone_name,
        }
    try:
        dt = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "app.schedule(at=...) entries must be HH:MM, 5-field cron, or ISO8601 timestamp"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    # ISO one-shot tokens are represented as cron + one_shot_at metadata.
    # If the computed cron slot is already in the past at deploy time, the next fire can be the
    # next yearly recurrence before the one-shot disable guard runs.
    return {
        "kind": "cron",
        "cron": f"{dt_utc.minute} {dt_utc.hour} {dt_utc.day} {dt_utc.month} *",
        "timezone": "UTC",
        "one_shot_at": dt_utc.isoformat().replace("+00:00", "Z"),
    }


def _normalize_schedule_entries_for_decorator(
    *,
    id: str,
    cron: str,
    at: Optional[list[Any]],
    timezone_name: str,
    run: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    schedule_base_id = _slugify(id)
    if not schedule_base_id:
        raise ValueError("app.schedule(...) requires a non-empty id or function name")

    sources: list[tuple[str, dict[str, Any]]] = []
    cron_expr = str(cron or "").strip()
    if cron_expr:
        sources.append(
            (
                "cron",
                {
                    "kind": "cron",
                    "cron": cron_expr,
                    "timezone": timezone_name,
                },
            )
        )
    for index, token in enumerate(at or [], start=1):
        cron_from_at = _cron_from_at_token(token, default_timezone_name=timezone_name)
        sources.append(
            (
                _schedule_entry_suffix(str(token), f"at-{index}"),
                cron_from_at,
            )
        )

    if not sources:
        raise ValueError("app.schedule(...) requires at least one of cron= or at=")

    normalized_run = _normalize_schedule_run(run) if isinstance(run, dict) else None
    use_suffixes = len(sources) > 1
    out: list[dict[str, Any]] = []
    for suffix, base in sources:
        schedule_id = schedule_base_id if not use_suffixes else f"{schedule_base_id}--{suffix}"
        candidate: dict[str, Any] = {"id": schedule_id, **base}
        # Validate schedule shape now using a temporary placeholder run.
        probe = dict(candidate)
        probe["run"] = {"type": "agent", "agent_id": "__schedule_placeholder__"}
        normalized_probe = _normalize_schedule_spec(probe)
        entry = {
            "id": normalized_probe["id"],
            "kind": normalized_probe["kind"],
        }
        if normalized_probe["kind"] == "cron":
            entry["cron"] = normalized_probe["cron"]
            entry["timezone"] = normalized_probe.get("timezone") or "UTC"
            if normalized_probe.get("one_shot_at"):
                entry["one_shot_at"] = str(normalized_probe["one_shot_at"])
        if normalized_run is not None:
            entry["run"] = dict(normalized_run)
        out.append(entry)
    return out


def _bind_schedule_entries_to_target(
    entries: list[dict[str, Any]],
    *,
    target_kind: str,
    target_id: str,
) -> list[dict[str, Any]]:
    normalized_specs: list[dict[str, Any]] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        base = {
            "id": str(raw.get("id") or "").strip(),
            "kind": str(raw.get("kind") or "").strip().lower(),
        }
        if base["kind"] == "cron":
            base["cron"] = str(raw.get("cron") or "").strip()
            base["timezone"] = str(raw.get("timezone") or "UTC").strip() or "UTC"
            if raw.get("one_shot_at") is not None:
                base["one_shot_at"] = raw.get("one_shot_at")
        else:
            base["every_seconds"] = int(raw.get("every_seconds", 0) or 0)

        run = raw.get("run") if isinstance(raw.get("run"), dict) else None
        if run is None:
            if target_kind == "agent":
                run = _invoke_builder.agent(target_id)
            elif target_kind == "tool":
                run = _invoke_builder.tool(target_id)
            else:
                raise ValueError(f"Unsupported schedule target kind: {target_kind}")
        spec = _normalize_schedule_spec({**base, "run": run})
        normalized_specs.append(spec)
    return normalized_specs


def _merge_schedule_specs(
    existing: Optional[list[dict[str, Any]]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [dict(item) for item in (existing or []) if isinstance(item, dict)]
    index_by_id = {str(item.get("id") or ""): idx for idx, item in enumerate(out)}
    for item in incoming:
        schedule_id = str(item.get("id") or "").strip()
        if not schedule_id:
            continue
        idx = index_by_id.get(schedule_id)
        if idx is None:
            index_by_id[schedule_id] = len(out)
            out.append(dict(item))
            continue
        out[idx] = dict(item)
    return out


def _schedule_spec_to_automation_args(spec: Any) -> dict[str, Any]:
    normalized = _normalize_schedule_spec(spec)
    run = normalized["run"]
    args: dict[str, Any] = {
        "name": normalized["id"],
        "schedule_kind": normalized["kind"],
    }
    if normalized["kind"] == "cron":
        args["schedule_expr"] = normalized["cron"]
        args["timezone"] = normalized.get("timezone") or "UTC"
    else:
        args["every_seconds"] = int(normalized["every_seconds"])

    if run["type"] == "agent":
        args["execution_kind"] = "app_agent_call"
        args["agent_id"] = run["agent_id"]
        if "input" in run and run["input"] is not None:
            args["input"] = run["input"]
    else:
        args["execution_kind"] = "app_tool_call"
        args["tool_name"] = run["tool_name"]
        if run.get("args"):
            args["tool_args"] = run["args"]
    return args


class _AutomationApp:
    """Public app declaration object."""

    def __init__(
        self,
        project_name: str,
        *,
        interfaces: Optional[dict[str, Any]] = None,
        runtime_profile: Optional[dict[str, Any]] = None,
        agent: Optional[dict[str, Any]] = None,
    ):
        self.project_name = _normalize_project_name(project_name)
        self.name = self.project_name
        self.slug = self.project_name
        self._agent = dict(agent or {})
        self._interfaces = dict(interfaces or {})
        self._runtime_profile = dict(runtime_profile or {})
        self._agents: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._default_agent_id: str = ""
        self._schedule_agent_fallback_id = "scheduled-jobs"

    def _upsert_agent(self, item: dict[str, Any]) -> None:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            return
        for idx, existing in enumerate(self._agents):
            if str(existing.get("id") or "").strip() == item_id:
                self._agents[idx] = item
                return
        self._agents.append(item)

    def _upsert_tool(self, item: dict[str, Any]) -> None:
        function_block = item.get("function") if isinstance(item.get("function"), dict) else {}
        tool_id = str(function_block.get("name") or "").strip()
        if not tool_id:
            return
        for idx, existing in enumerate(self._tools):
            existing_fn = existing.get("function") if isinstance(existing.get("function"), dict) else {}
            existing_name = str(existing_fn.get("name") or "").strip()
            if existing_name == tool_id:
                self._tools[idx] = item
                return
        self._tools.append(item)

    @staticmethod
    def _get_fn_schedule_entries(fn: Callable[..., Any]) -> list[dict[str, Any]]:
        raw = getattr(fn, "__ara_schedule_entries__", None)
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _set_fn_schedule_entries(fn: Callable[..., Any], entries: list[dict[str, Any]]) -> None:
        setattr(fn, "__ara_schedule_entries__", [dict(item) for item in entries if isinstance(item, dict)])

    @staticmethod
    def _workflow_for_agent(agent_row: dict[str, Any], *, trigger: Optional[dict[str, Any]] = None, workflow_id: Optional[str] = None, task: Optional[str] = None) -> dict[str, Any]:
        agent_id = str(agent_row.get("id") or "").strip()
        instructions = str(agent_row.get("instructions") or agent_row.get("task") or "").strip()
        trigger_cfg = dict(trigger or {"type": "api"})
        schedule_expr = str(trigger_cfg.get("cron") or trigger_cfg.get("schedule") or "").strip()
        if schedule_expr and str(trigger_cfg.get("type") or "").strip().lower() == "cron":
            trigger_cfg.setdefault("schedule", schedule_expr)
            trigger_cfg.setdefault("cron", schedule_expr)
            trigger_cfg.setdefault("timezone", "UTC")
        return {
            "id": str(workflow_id or agent_id),
            "mode": "task",
            "agent_id": agent_id,
            "task": str(task or instructions or f"Run agent {agent_id}").strip(),
            "run": {},
            "pipeline": [],
            "schedule": schedule_expr,
            "trigger": trigger_cfg,
        }

    @staticmethod
    def _workflow_for_tool_schedule(
        *,
        workflow_id: str,
        anchor_agent_id: str,
        tool_name: str,
        tool_args: Optional[dict[str, Any]] = None,
        trigger: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        trigger_cfg = dict(trigger or {"type": "api"})
        schedule_expr = str(trigger_cfg.get("cron") or trigger_cfg.get("schedule") or "").strip()
        return {
            "id": str(workflow_id),
            "mode": "pipeline",
            "agent_id": str(anchor_agent_id),
            "task": f"Run scheduled tool {tool_name}",
            "run": {},
            "pipeline": [
                {
                    "id": f"{workflow_id}--tool",
                    "kind": "tool",
                    "tool_name": str(tool_name),
                    "args": dict(tool_args or {}),
                }
            ],
            "schedule": schedule_expr,
            "trigger": trigger_cfg,
        }

    def agent(
        self,
        id: Optional[str] = None,
        *,
        entrypoint: bool = False,
        skills: Optional[list[str]] = None,
        schedules: Optional[list[dict[str, Any]]] = None,
        handoff_to: Optional[list[str]] = None,
        runtime: Optional[dict[str, Any]] = None,
        sandbox: Optional[dict[str, Any]] = None,
        always_on: bool = True,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            agent_id = str(id or fn.__name__).strip()
            if not agent_id:
                raise ValueError("@app.agent requires a non-empty id")
            _validate_agent_prompt_signature(fn)
            source = _extract_callable_source(fn, context="@app.agent")
            params_schema = _callable_parameters_schema(fn)
            prompt_factory_spec = {
                "function_name": fn.__name__,
                "source": source,
                "parameters": params_schema,
            }
            instructions_text = (
                str(fn.__doc__ or "").strip()
                or f"Generate instructions at runtime via agent function '{fn.__name__}'."
            )
            task_text = instructions_text
            normalized_schedules: list[dict[str, Any]] = []
            for schedule_item in schedules or []:
                normalized_schedules.append(_normalize_schedule_spec(schedule_item))
            pending_schedule_entries = self._get_fn_schedule_entries(fn)
            if pending_schedule_entries:
                pending_bound = _bind_schedule_entries_to_target(
                    pending_schedule_entries,
                    target_kind="agent",
                    target_id=agent_id,
                )
                normalized_schedules = _merge_schedule_specs(normalized_schedules, pending_bound)
            agent_row: dict[str, Any] = {
                "id": agent_id,
                "task": task_text,
                "instructions": instructions_text,
                "persona": instructions_text,
                "schedules": normalized_schedules,
                "handoff_to": _normalize_string_items(handoff_to),
                "always_on": bool(always_on),
                "entrypoint": bool(entrypoint),
            }
            agent_row["prompt_factory"] = prompt_factory_spec
            if skills is not None:
                agent_row["skills"] = _normalize_string_items(skills)
            if isinstance(runtime, dict) and runtime:
                runtime_cfg = dict(runtime)
                runtime_cfg.pop("__secret_definitions", None)
                agent_row["runtime"] = runtime_cfg
            if isinstance(sandbox, dict) and sandbox:
                agent_row["sandbox"] = dict(sandbox)
            endpoint_cfg = getattr(fn, "__ara_fastapi_endpoint__", None)
            if isinstance(endpoint_cfg, dict):
                agent_row["fastapi_endpoint"] = dict(endpoint_cfg)
            self._upsert_agent(agent_row)
            if entrypoint or not self._default_agent_id:
                self._default_agent_id = agent_id
            setattr(fn, "__ara_agent__", agent_row)
            return fn

        return decorator

    def tool(
        self,
        *,
        id: Optional[str] = None,
        parameters: Optional[dict[str, Any]] = None,
        required_env: Optional[list[str]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_id = str(id or fn.__name__).strip()
            if not tool_id:
                raise ValueError("@app.tool requires a non-empty id")
            try:
                raw_source = inspect.getsource(fn)
            except (OSError, TypeError):
                raise ValueError("@app.tool requires source-visible functions (no lambdas/dynamic defs)") from None
            source = _strip_leading_decorators(raw_source)
            if not source.startswith("def "):
                raise ValueError("@app.tool only supports standard def functions")
            params_schema = dict(parameters) if isinstance(parameters, dict) else _callable_parameters_schema(fn)
            tool_description = str(fn.__doc__ or "").strip()
            inferred_required_env = _extract_secret_keys_from_source(source)
            explicit_required_env = _normalize_string_items(required_env)
            merged_required_env = _normalize_string_items([*explicit_required_env, *inferred_required_env])
            item = {
                "type": "function",
                "function": {
                    "name": tool_id,
                    "description": tool_description,
                    "parameters": params_schema,
                },
                "function_name": fn.__name__,
                "source": source,
            }
            if merged_required_env:
                item["required_env"] = merged_required_env
            pending_schedule_entries = self._get_fn_schedule_entries(fn)
            if pending_schedule_entries:
                item["schedules"] = _bind_schedule_entries_to_target(
                    pending_schedule_entries,
                    target_kind="tool",
                    target_id=tool_id,
                )
            self._upsert_tool(item)
            setattr(fn, "__ara_tool__", item)
            return fn

        return decorator

    def schedule(
        self,
        *,
        id: str = "",
        cron: str = "",
        at: Optional[list[Any]] = None,
        timezone: str = "UTC",
        run: Optional[dict[str, Any]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        timezone_name = str(timezone or "UTC").strip() or "UTC"

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            schedule_id = str(id or fn.__name__).strip()
            at_values: Optional[list[Any]]
            if at is None:
                at_values = None
            elif isinstance(at, list):
                at_values = list(at)
            else:
                at_values = [at]

            entries = _normalize_schedule_entries_for_decorator(
                id=schedule_id,
                cron=str(cron or "").strip(),
                at=at_values,
                timezone_name=timezone_name,
                run=run,
            )
            existing_entries = self._get_fn_schedule_entries(fn)
            merged_entries = _merge_schedule_specs(existing_entries, entries)
            self._set_fn_schedule_entries(fn, merged_entries)

            existing_agent = getattr(fn, "__ara_agent__", None)
            if isinstance(existing_agent, dict):
                agent_id = str(existing_agent.get("id") or fn.__name__).strip()
                bound = _bind_schedule_entries_to_target(entries, target_kind="agent", target_id=agent_id)
                existing_agent["schedules"] = _merge_schedule_specs(existing_agent.get("schedules"), bound)
                self._upsert_agent(existing_agent)

            existing_tool = getattr(fn, "__ara_tool__", None)
            if isinstance(existing_tool, dict):
                fn_block = existing_tool.get("function") if isinstance(existing_tool.get("function"), dict) else {}
                tool_id = str(fn_block.get("name") or fn.__name__).strip()
                bound = _bind_schedule_entries_to_target(entries, target_kind="tool", target_id=tool_id)
                existing_tool["schedules"] = _merge_schedule_specs(existing_tool.get("schedules"), bound)
                self._upsert_tool(existing_tool)

            if existing_agent is None and existing_tool is None:
                logger.warning(
                    "@app.schedule applied to %r which has no @app.agent or @app.tool decorator. "
                    "Schedule entries are stored but will not appear in the manifest. "
                    "Ensure @app.schedule is paired with @app.agent or @app.tool.",
                    fn.__name__,
                )

            return fn

        return decorator

    @property
    def manifest(self) -> dict[str, Any]:
        agent = dict(self._agent)
        workflows: list[dict[str, Any]] = []
        fastapi_endpoint_rows: list[dict[str, Any]] = []
        agent_rows = [dict(row) for row in self._agents]
        tool_schedule_specs: list[dict[str, Any]] = []
        for tool in self._tools:
            schedules = tool.get("schedules") if isinstance(tool.get("schedules"), list) else []
            for raw_schedule in schedules:
                if isinstance(raw_schedule, dict):
                    tool_schedule_specs.append(_normalize_schedule_spec(raw_schedule))

        if tool_schedule_specs:
            if not agent_rows:
                agent_rows.append(
                    {
                        "id": self._schedule_agent_fallback_id,
                        "task": "Execute scheduled tool jobs.",
                        "instructions": "Execute scheduled tool jobs.",
                        "persona": "Execute scheduled tool jobs.",
                        "schedules": [],
                        "handoff_to": [],
                        "always_on": False,
                        "entrypoint": False,
                    }
                )
            anchor_agent_id = str(self._default_agent_id or agent_rows[0].get("id") or "").strip()
            anchor_index = 0
            for idx, row in enumerate(agent_rows):
                if str(row.get("id") or "").strip() == anchor_agent_id:
                    anchor_index = idx
                    break
            anchor_row = dict(agent_rows[anchor_index])
            existing = anchor_row.get("schedules") if isinstance(anchor_row.get("schedules"), list) else []
            anchor_row["schedules"] = _merge_schedule_specs(existing, tool_schedule_specs)
            agent_rows[anchor_index] = anchor_row

        if agent_rows:
            agent["agents"] = agent_rows
            default_agent_id = str(self._default_agent_id or agent_rows[0].get("id") or "").strip()
            if default_agent_id:
                agent["default_agent_id"] = default_agent_id

            profiles: list[dict[str, Any]] = []
            subagents: list[dict[str, Any]] = []
            for row in agent_rows:
                agent_id = str(row.get("id") or "").strip()
                if not agent_id:
                    continue
                endpoint_cfg = row.get("fastapi_endpoint") if isinstance(row.get("fastapi_endpoint"), dict) else None
                if endpoint_cfg:
                    endpoint_row = dict(endpoint_cfg)
                    endpoint_row.setdefault("agent_id", agent_id)
                    endpoint_row.setdefault("workflow_id", agent_id)
                    fastapi_endpoint_rows.append(endpoint_row)
                instructions = str(row.get("instructions") or row.get("task") or "").strip()
                profile = {
                    "id": agent_id,
                    "instructions": instructions,
                    "persona": instructions,
                    "handoff_to": _normalize_string_items(row.get("handoff_to") if isinstance(row.get("handoff_to"), list) else []),
                    "always_on": bool(row.get("always_on", True)),
                }
                if isinstance(row.get("skills"), list):
                    profile["skills"] = _normalize_string_items(row["skills"])
                profiles.append(profile)

                runtime_cfg = dict(row.get("runtime") or {})
                runtime_cfg.pop("__secret_definitions", None)
                subagents.append(
                    {
                        "id": agent_id,
                        "workflow_id": agent_id,
                        "channels": ["linq"],
                        "runtime": runtime_cfg,
                        "sandbox": dict(row.get("sandbox") or {"policy": "shared", "max_concurrency": DEFAULT_SUBAGENT_MAX_CONCURRENCY}),
                        "hooks": [],
                    }
                )

                workflows.append(self._workflow_for_agent(row, workflow_id=agent_id))
                schedules = row.get("schedules") if isinstance(row.get("schedules"), list) else []
                for schedule_spec in schedules:
                    normalized_schedule = _normalize_schedule_spec(schedule_spec)
                    if normalized_schedule.get("kind") != "cron":
                        continue
                    schedule_run = normalized_schedule.get("run") if isinstance(normalized_schedule.get("run"), dict) else {}
                    schedule_run_type = str(schedule_run.get("type") or "").strip().lower()
                    workflow_id = f"{agent_id}--{normalized_schedule['id']}"
                    trigger = {
                        "type": "cron",
                        "cron": str(normalized_schedule.get("cron") or "").strip(),
                        "schedule": str(normalized_schedule.get("cron") or "").strip(),
                        "timezone": str(normalized_schedule.get("timezone") or "UTC").strip() or "UTC",
                    }
                    if normalized_schedule.get("one_shot_at"):
                        trigger["one_shot_at"] = str(normalized_schedule.get("one_shot_at"))
                    if schedule_run_type == "tool":
                        tool_name = str(schedule_run.get("tool_name") or "").strip()
                        if not tool_name:
                            continue
                        tool_args = schedule_run.get("args") if isinstance(schedule_run.get("args"), dict) else {}
                        workflows.append(
                            self._workflow_for_tool_schedule(
                                workflow_id=workflow_id,
                                anchor_agent_id=agent_id,
                                tool_name=tool_name,
                                tool_args=tool_args,
                                trigger=trigger,
                            )
                        )
                        continue
                    run_agent_id = str(schedule_run.get("agent_id") or agent_id).strip()
                    schedule_run_input = schedule_run.get("input")
                    schedule_message = ""
                    if isinstance(schedule_run_input, dict):
                        schedule_message = str(schedule_run_input.get("message") or "").strip()
                    elif isinstance(schedule_run_input, str):
                        schedule_message = schedule_run_input.strip()
                    schedule_task = str(row.get("task") or instructions or f"Run agent {run_agent_id}").strip()
                    workflows.append(
                        self._workflow_for_agent(
                            {
                                "id": run_agent_id,
                                "task": schedule_task,
                                "instructions": str(schedule_message or schedule_task).strip(),
                            },
                            trigger=trigger,
                            workflow_id=workflow_id,
                            task=schedule_task,
                        )
                    )

            if profiles:
                agent["profiles"] = profiles
                if default_agent_id:
                    agent["default_profile_id"] = default_agent_id
            if subagents:
                agent["subagents"] = subagents

        if self._tools:
            agent["tools"] = list(self._tools)

        interfaces_payload = dict(self._interfaces)
        if fastapi_endpoint_rows:
            existing_endpoint_rows = interfaces_payload.get("fastapi_endpoints")
            merged_endpoint_rows: list[dict[str, Any]] = []
            if isinstance(existing_endpoint_rows, list):
                for item in existing_endpoint_rows:
                    if isinstance(item, dict):
                        merged_endpoint_rows.append(dict(item))
            merged_endpoint_rows.extend(fastapi_endpoint_rows)
            interfaces_payload["fastapi_endpoints"] = merged_endpoint_rows

        return {
            "name": self.name,
            "slug": self.slug,
            "description": "",
            "agent": agent,
            "workflows": workflows,
            "interfaces": interfaces_payload,
            "runtime_profile": dict(self._runtime_profile),
        }


class _Invoke:
    @staticmethod
    def agent(agent_id: str, *, input: Optional[Any] = None) -> dict[str, Any]:
        resolved_agent_id = str(agent_id or "").strip()
        if not resolved_agent_id:
            raise ValueError("invoke.agent(...) requires a non-empty agent_id")
        out: dict[str, Any] = {
            "type": "agent",
            "agent_id": resolved_agent_id,
        }
        if input is not None:
            _ensure_json_serializable(input, context="invoke.agent(..., input=...)")
            out["input"] = input
        return out

    @staticmethod
    def tool(tool_name: str, *, args: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        resolved_tool_name = str(tool_name or "").strip()
        if not resolved_tool_name:
            raise ValueError("invoke.tool(...) requires a non-empty tool name")
        return {
            "type": "tool",
            "tool_name": resolved_tool_name,
            "args": dict(args or {}),
        }


class _Schedule:
    @staticmethod
    def cron(
        *,
        expr: str,
        run: dict[str, Any],
        id: Optional[str] = None,
        timezone: str = "UTC",
    ) -> dict[str, Any]:
        run_spec = dict(run or {})
        run_kind = str(run_spec.get("type") or "").strip().lower()
        if run_kind == "agent":
            target_hint = str(run_spec.get("agent_id") or "").strip() or "agent"
        elif run_kind == "tool":
            target_hint = str(run_spec.get("tool_name") or "").strip() or "tool"
        else:
            target_hint = "run"
        expr_slug = _slugify(str(expr or ""))[:24]
        generated_id = f"{target_hint}-{expr_slug or 'cron'}"
        spec = {
            "id": str(id or generated_id).strip(),
            "kind": "cron",
            "cron": str(expr or "").strip(),
            "timezone": str(timezone or "UTC").strip() or "UTC",
            "run": run_spec,
        }
        return _normalize_schedule_spec(spec)

    @staticmethod
    def every(*, seconds: int, run: dict[str, Any], id: Optional[str] = None) -> dict[str, Any]:
        run_spec = dict(run or {})
        run_kind = str(run_spec.get("type") or "").strip().lower()
        if run_kind == "agent":
            target_hint = str(run_spec.get("agent_id") or "").strip() or "agent"
        elif run_kind == "tool":
            target_hint = str(run_spec.get("tool_name") or "").strip() or "tool"
        else:
            target_hint = "run"
        generated_id = f"{target_hint}-every-{int(seconds)}s"
        spec = {
            "id": str(id or generated_id).strip(),
            "kind": "every",
            "every_seconds": int(seconds),
            "run": run_spec,
        }
        return _normalize_schedule_spec(spec)


class _Scheduler:
    @staticmethod
    def create(spec: dict[str, Any], *, app_id: Optional[str] = None) -> dict[str, Any]:
        args = _schedule_spec_to_automation_args(spec)
        if app_id:
            args["app_id"] = str(app_id).strip()
        return {
            "tool": "automation_create",
            "args": args,
        }

    @staticmethod
    def upsert(spec: dict[str, Any], *, app_id: Optional[str] = None) -> dict[str, Any]:
        args = _schedule_spec_to_automation_args(spec)
        if app_id:
            args["app_id"] = str(app_id).strip()
        args["upsert"] = True
        return {
            "tool": "automation_create",
            "args": args,
        }


# Internal builder instances remain available for private helpers even though
# the legacy public names are replaced with removed-API sentinels at module end.
_invoke_builder = _Invoke()
_schedule_builder = _Schedule()
_scheduler_builder = _Scheduler()


def _automation_project_name(automation_id: str) -> str:
    slug = _slugify(automation_id)
    if not slug:
        slug = _MINIMAL_DEFAULT_PROJECT_NAME
    return _normalize_project_name(slug[:63].rstrip("-"))


def _ensure_minimal_app(project_name: Optional[str] = None, *, owner_module: str = "") -> _AutomationApp:
    global _minimal_app_singleton, _minimal_app_uses_default_name, _minimal_app_owner_module
    resolved_project_name = str(project_name or "").strip()
    resolved_owner_module = str(owner_module or "").strip()
    if (
        _minimal_app_singleton is not None
        and resolved_owner_module
        and _minimal_app_owner_module
        and _minimal_app_owner_module != resolved_owner_module
    ):
        # Avoid state leaking across multiple automation script imports in one process.
        _minimal_app_singleton = None
        _minimal_app_uses_default_name = False
        _minimal_app_owner_module = ""
    if _minimal_app_singleton is None:
        if not resolved_project_name:
            resolved_project_name = _MINIMAL_DEFAULT_PROJECT_NAME
            _minimal_app_uses_default_name = True
        else:
            _minimal_app_uses_default_name = False
        _minimal_app_singleton = _AutomationApp(resolved_project_name)
        _minimal_app_owner_module = resolved_owner_module
        return _minimal_app_singleton

    if resolved_project_name and _minimal_app_uses_default_name and not _minimal_app_singleton._agents:
        normalized = _normalize_project_name(resolved_project_name)
        _minimal_app_singleton.project_name = normalized
        _minimal_app_singleton.name = normalized
        _minimal_app_singleton.slug = normalized
        _minimal_app_uses_default_name = False
    if resolved_owner_module and not _minimal_app_owner_module:
        _minimal_app_owner_module = resolved_owner_module
    return _minimal_app_singleton


def _pop_minimal_app() -> Optional[_AutomationApp]:
    global _minimal_app_singleton, _minimal_app_uses_default_name, _minimal_app_owner_module
    app = _minimal_app_singleton
    _minimal_app_singleton = None
    _minimal_app_uses_default_name = False
    _minimal_app_owner_module = ""
    return app


def secret(name: str, default: Optional[str] = None) -> str:
    key = _validate_env_key(name)
    value = str(os.getenv(key) or "").strip()
    if value:
        return value
    if default is not None:
        return str(default)
    raise RuntimeError(f"Missing required secret: {key}")


def env(name: str, default: Optional[str] = None) -> str:
    key = _validate_env_key(name)
    value = os.getenv(key)
    if value is None:
        return "" if default is None else str(default)
    return str(value)


def tool(
    fn: Optional[Callable[..., Any]] = None,
    *,
    id: Optional[str] = None,
    parameters: Optional[dict[str, Any]] = None,
    required_env: Optional[list[str]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]] | Callable[..., Any]:
    app = _ensure_minimal_app(owner_module=_calling_module_name())

    def decorator(inner_fn: Callable[..., Any]) -> Callable[..., Any]:
        return app.tool(id=id, parameters=parameters, required_env=required_env)(inner_fn)

    if fn is not None:
        return decorator(fn)
    return decorator


class Automation:
    def __init__(
        self,
        id: str,
        *,
        system_instructions: str = "",
        tools: Optional[list[Callable[..., Any]]] = None,
        skills: Optional[list[Any]] = None,
        allow_connector_tools: bool = True,
        required_env: Optional[list[str]] = None,
        entrypoint: str = "",
        execution: Optional[dict[str, Any]] = None,
    ):
        resolved_id = str(id or "").strip()
        if not resolved_id:
            raise ValueError("Automation(...) requires a non-empty id")
        app = _ensure_minimal_app(
            project_name=_automation_project_name(resolved_id),
            owner_module=_calling_module_name(),
        )

        tool_skill_names: list[str] = []
        for fn in tools or []:
            if not callable(fn):
                raise ValueError("Automation(..., tools=[...]) expects callables")
            if not hasattr(fn, "__ara_tool__"):
                app.tool()(fn)
            tool_row = getattr(fn, "__ara_tool__", None)
            if isinstance(tool_row, dict):
                fn_block = tool_row.get("function") if isinstance(tool_row.get("function"), dict) else {}
                tool_name = str(fn_block.get("name") or fn.__name__).strip()
                if tool_name:
                    tool_skill_names.append(tool_name)
        explicit_skill_names = _normalize_automation_skill_items(skills)
        connector_refs = _connector_refs_from_skill_items(explicit_skill_names)
        skill_names = _normalize_string_items([*tool_skill_names, *explicit_skill_names])
        app._interfaces["inherit_owner_tools"] = bool(allow_connector_tools)
        if connector_refs:
            app._interfaces["inherit_owner_tools"] = True
            app._interfaces["tool_privileges"] = _merge_connector_tool_privileges(
                app._interfaces.get("tool_privileges"),
                connector_refs,
            )

        instructions_text = str(system_instructions or "").strip()
        if not instructions_text:
            instructions_text = f"Run automation '{resolved_id}'."

        required_keys = _normalize_string_items(required_env)
        for fn in tools or []:
            tool_row = getattr(fn, "__ara_tool__", None)
            if isinstance(tool_row, dict):
                required_keys.extend(
                    _normalize_string_items(
                        tool_row.get("required_env")
                        if isinstance(tool_row.get("required_env"), list)
                        else []
                    )
                )
        required_keys = _normalize_string_items(required_keys)
        if required_keys:
            existing_required = app._runtime_profile.get("__required_env_keys")
            existing = _normalize_string_items(existing_required if isinstance(existing_required, list) else [])
            app._runtime_profile["__required_env_keys"] = _normalize_string_items([*existing, *required_keys])

        if entrypoint:
            startup = dict(app._runtime_profile.get("startup") or {})
            startup["entrypoint"] = str(entrypoint).strip()
            app._runtime_profile["startup"] = startup

        if isinstance(execution, dict) and execution:
            app._runtime_profile["execution"] = dict(execution)

        def _automation_entry(input: dict) -> str:  # noqa: A002
            return instructions_text

        _automation_entry.__doc__ = instructions_text
        setattr(
            _automation_entry,
            "__ara_source_override__",
            f"def _automation_entry(input: dict) -> str:\n    return {instructions_text!r}",
        )
        app.agent(
            id=resolved_id,
            entrypoint=True,
            skills=_normalize_string_items(skill_names),
        )(_automation_entry)

        self.id = resolved_id
        self.app = app


def _read_dotenv(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and not os.getenv(key):
            os.environ[key] = value


def _local_runtime_keys_path(base: pathlib.Path) -> pathlib.Path:
    return base / CLI_RUNTIME_KEYS_FILENAME


def _load_local_runtime_keys(base: pathlib.Path) -> dict[str, str]:
    path = _local_runtime_keys_path(base)
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to load runtime key cache file: %s", path, exc_info=True)
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in parsed.items():
        resolved_key = str(key or "").strip()
        resolved_value = str(value or "").strip()
        if resolved_key and resolved_value:
            out[resolved_key] = resolved_value
    return out


def _load_local_runtime_key(base: pathlib.Path, *, slug: str) -> str:
    return str(_load_local_runtime_keys(base).get(slug, "") or "").strip()


def _save_local_runtime_key(base: pathlib.Path, *, slug: str, runtime_key: str) -> None:
    resolved_slug = str(slug or "").strip()
    resolved_key = str(runtime_key or "").strip()
    if not resolved_slug or not resolved_key:
        return
    path = _local_runtime_keys_path(base)
    data = _load_local_runtime_keys(base)
    data[resolved_slug] = resolved_key
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.debug("Failed to clean runtime key temp file: %s", tmp_path, exc_info=True)


def _cli_credentials_path() -> pathlib.Path:
    return pathlib.Path.home() / CLI_CREDENTIALS_DIRNAME / CLI_CREDENTIALS_FILENAME


def _load_cli_credentials() -> dict[str, Any]:
    path = _cli_credentials_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to load CLI credentials file: %s", path, exc_info=True)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_cli_credentials(data: dict[str, Any]) -> None:
    path = _cli_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        logger.debug("Failed to enforce 0700 on credentials directory: %s", path.parent, exc_info=True)
    payload = dict(data or {})
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    blob = json.dumps(payload, indent=2) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(blob)


def _clear_cli_credentials() -> None:
    path = _cli_credentials_path()
    if path.exists():
        path.unlink()


def _resolve_api_base_url(default: str = DEFAULT_API_BASE_URL) -> str:
    env_url = os.getenv("ARA_API_BASE_URL", "").strip()
    if env_url:
        return env_url
    creds = _load_cli_credentials()
    saved_url = str(creds.get("api_base_url") or "").strip()
    if saved_url:
        return saved_url
    return default


def _coerce_supabase_expiry_iso(payload: dict[str, Any]) -> str:
    raw_expires_at = payload.get("expires_at")
    if isinstance(raw_expires_at, (int, float)):
        return datetime.fromtimestamp(float(raw_expires_at), tz=timezone.utc).isoformat()
    text = str(raw_expires_at or "").strip()
    if text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
    expires_in = int(payload.get("expires_in") or 0)
    if expires_in > 0:
        return (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    logger.warning("Supabase auth payload missing expiry metadata; refusing to cache JWT.", extra={"keys": sorted(payload.keys())})
    raise RuntimeError("Supabase auth response did not include a valid expires_at or expires_in field.")


def _parse_expiry_epoch(raw: Any) -> float:
    text = str(raw or "").strip()
    if not text:
        return 0.0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _supabase_token_request(
    *,
    supabase_url: str,
    supabase_anon_key: str,
    grant_type: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    url = f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type={urllib.parse.quote(grant_type)}"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        method="POST",
        data=payload,
        headers={
            "apikey": supabase_anon_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase auth request failed ({exc.code}): {details}") from exc


def _pkce_code_verifier() -> str:
    # RFC 7636 allows 43-128 chars from unreserved URL charset.
    return secrets.token_urlsafe(64)


def _pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _build_supabase_oauth_authorize_url(
    *,
    supabase_url: str,
    provider: str,
    redirect_to: str,
    code_challenge: str,
    state: str = "",
) -> str:
    params = {
        "provider": provider,
        "redirect_to": redirect_to,
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
    }
    if state:
        params["state"] = state
    return f"{supabase_url.rstrip('/')}/auth/v1/authorize?{urllib.parse.urlencode(params)}"


def _parse_oauth_callback_payload(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {}
    parsed = urllib.parse.urlparse(text)
    query_text = parsed.query if parsed.query else text
    pairs = urllib.parse.parse_qs(query_text, keep_blank_values=True)
    out: dict[str, str] = {}
    for key in ("code", "state", "error", "error_description"):
        value = pairs.get(key, [])
        out[key] = str(value[0]).strip() if value else ""
    return out


def _collect_oauth_callback_via_localhost(
    *,
    supabase_url: str,
    provider: str,
    code_challenge: str,
    expected_state: str,
    timeout_seconds: int,
    open_browser: bool,
) -> dict[str, str]:
    result: dict[str, str] = {}
    result_lock = threading.Lock()

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed_path = urllib.parse.urlparse(self.path)
            if parsed_path.path != "/auth/callback":
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            payload = _parse_oauth_callback_payload(self.path)
            incoming_state = str(payload.get("state") or "").strip()
            if incoming_state != expected_state:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"State mismatch")
                return
            with result_lock:
                for key in ("code", "state", "error", "error_description"):
                    value = str(payload.get(key) or "").strip()
                    if value and not str(result.get(key) or "").strip():
                        result[key] = value
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with result_lock:
                has_code = bool(str(result.get("code") or "").strip())
            if has_code:
                message = "Ara CLI login succeeded. You can close this tab and return to the terminal."
            else:
                message = "Ara CLI login was not completed. Return to the terminal for details."
            self.wfile.write(f"<html><body><p>{message}</p></body></html>".encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            _ = (format, args)
            return

    requested_port_text = str(os.getenv(_CLI_OAUTH_CALLBACK_PORT_ENV, "")).strip()
    requested_port = _CLI_OAUTH_CALLBACK_PORT
    if requested_port_text:
        try:
            requested_port = int(requested_port_text)
        except ValueError as exc:
            raise RuntimeError(f"Invalid {_CLI_OAUTH_CALLBACK_PORT_ENV} value: {requested_port_text}") from exc
    if not (1 <= requested_port <= 65535):
        raise RuntimeError(
            f"Invalid {_CLI_OAUTH_CALLBACK_PORT_ENV} value {requested_port}: must be 1-65535."
        )

    try:
        server = http.server.ThreadingHTTPServer((_CLI_OAUTH_CALLBACK_HOST, requested_port), _CallbackHandler)
    except OSError as exc:
        raise RuntimeError(
            f"Could not bind localhost callback server on {_CLI_OAUTH_CALLBACK_HOST}:{requested_port}. "
            f"Free that port or set {_CLI_OAUTH_CALLBACK_PORT_ENV} to another allowed port."
        ) from exc

    with server:
        server.timeout = 1
        callback_port = int(server.server_address[1])
        redirect_to = f"http://{_CLI_OAUTH_CALLBACK_HOST}:{callback_port}/auth/callback"
        launch_url = _build_supabase_oauth_authorize_url(
            supabase_url=supabase_url,
            provider=provider,
            redirect_to=redirect_to,
            code_challenge=code_challenge,
            state=expected_state,
        )

        print(f"Open this URL to sign in with {provider}:")
        print(launch_url)
        if open_browser:
            try:
                webbrowser.open(launch_url)
            except Exception:
                pass

        deadline = time.time() + max(30, int(timeout_seconds or 180))
        while time.time() <= deadline:
            server.handle_request()
            with result_lock:
                has_terminal_result = bool(result.get("code") or result.get("error"))
            if has_terminal_result:
                break

    with result_lock:
        final = dict(result)

    if final.get("error"):
        detail = final.get("error_description") or final.get("error")
        raise RuntimeError(f"OAuth login failed: {detail}")
    if final.get("code"):
        callback_state = str(final.get("state") or "").strip()
        if callback_state != expected_state:
            raise RuntimeError("OAuth callback state mismatch. Please retry `ara auth login`.")
        final["redirect_uri"] = redirect_to
        return final

    raise RuntimeError(
        "No localhost OAuth callback received before timeout. "
        "Retry `ara auth login` or use `ara auth login --api-key <ARA_API_KEY>`."
    )


def _collect_oauth_callback_via_polling(
    *,
    api_base_url: str,
    provider: str,
    code_challenge: str,
    timeout_seconds: int,
    open_browser: bool,
) -> dict[str, str]:
    auth_http = _Http(api_base_url, "")
    start_payload = auth_http.cli_auth_device_start(
        provider=provider,
        code_challenge=code_challenge,
        code_challenge_method="s256",
        timeout_seconds=int(timeout_seconds or 180),
    )
    authorize_url = str(start_payload.get("authorize_url") or start_payload.get("verification_uri") or "").strip()
    session_id = str(start_payload.get("session_id") or "").strip()
    poll_token = str(start_payload.get("poll_token") or "").strip()
    if not (authorize_url and session_id and poll_token):
        raise RuntimeError("Polling login init failed: server returned incomplete session payload.")

    print(f"Open this URL to sign in with {provider}:")
    print(authorize_url)
    if open_browser:
        try:
            webbrowser.open(authorize_url)
        except Exception:
            pass

    interval_seconds = int(start_payload.get("interval_seconds") or 2)
    interval_seconds = max(1, min(10, interval_seconds))
    deadline = time.time() + max(30, int(timeout_seconds or 180))
    seen_unknown_statuses: set[str] = set()
    while time.time() <= deadline:
        status_payload = auth_http.cli_auth_device_status(session_id=session_id, poll_token=poll_token)
        status = str(status_payload.get("status") or "").strip().lower()
        if status in {"pending", "waiting", "queued"}:
            time.sleep(interval_seconds)
            continue
        if status == "approved":
            auth_code = str(status_payload.get("auth_code") or "").strip()
            redirect_uri = str(status_payload.get("redirect_uri") or "").strip()
            if not auth_code or not redirect_uri:
                raise RuntimeError("Polling login approved but callback payload was incomplete.")
            return {
                "code": auth_code,
                "state": str(status_payload.get("state") or "").strip(),
                "redirect_uri": redirect_uri,
            }
        if status in {"error", "failed", "denied"}:
            detail = (
                str(status_payload.get("error_description") or "").strip()
                or str(status_payload.get("error") or "").strip()
                or "OAuth login failed."
            )
            raise RuntimeError(f"OAuth login failed: {detail}")
        if status == "expired":
            raise RuntimeError("OAuth login expired before completion.")
        if status == "consumed":
            raise RuntimeError(
                "OAuth login code already used. "
                "Retry `ara auth login` if this was unexpected."
            )
        unknown_status = status or "<empty>"
        if unknown_status not in seen_unknown_statuses:
            print(
                f"Warning: polling login returned unknown status '{unknown_status}', retrying.",
                file=sys.stderr,
            )
            seen_unknown_statuses.add(unknown_status)
        time.sleep(interval_seconds)

    raise RuntimeError(
        "No OAuth approval received before timeout. "
        "Retry `ara auth login` or use `ara auth login --api-key <ARA_API_KEY>`."
    )


def _refresh_cli_jwt_credentials_if_needed(creds: dict[str, Any]) -> dict[str, Any]:
    auth_type = str(creds.get("auth_type") or "").strip().lower()
    if auth_type != "supabase_jwt":
        return creds
    expires_epoch = _parse_expiry_epoch(creds.get("expires_at"))
    if expires_epoch > (time.time() + _JWT_REFRESH_SKEW_SECONDS):
        return creds
    refresh_token = str(creds.get("refresh_token") or "").strip()
    supabase_url = str(creds.get("supabase_url") or "").strip()
    supabase_anon_key = str(creds.get("supabase_anon_key") or "").strip()
    if not (refresh_token and supabase_url and supabase_anon_key):
        raise RuntimeError("Stored CLI credentials are missing refresh metadata; run `ara auth login` again.")
    refreshed = _supabase_token_request(
        supabase_url=supabase_url,
        supabase_anon_key=supabase_anon_key,
        grant_type="refresh_token",
        body={"refresh_token": refresh_token},
    )
    access_token = str(refreshed.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Supabase refresh did not return access_token")
    updated = dict(creds)
    updated["access_token"] = access_token
    next_refresh = str(refreshed.get("refresh_token") or "").strip()
    if next_refresh:
        updated["refresh_token"] = next_refresh
    updated["expires_at"] = _coerce_supabase_expiry_iso(refreshed)
    user = refreshed.get("user")
    if isinstance(user, dict):
        updated["user"] = {
            "id": str(user.get("id") or ""),
            "email": str(user.get("email") or ""),
        }
    _save_cli_credentials(updated)
    return updated


def _resolve_control_plane_bearer() -> str:
    env_key = os.getenv("ARA_API_KEY", "").strip()
    if env_key:
        return env_key
    legacy = os.getenv("ARA_ACCESS_TOKEN", "").strip()
    if legacy:
        return legacy
    creds = _load_cli_credentials()
    if not creds:
        return ""
    auth_type = str(creds.get("auth_type") or "").strip().lower()
    if auth_type == "supabase_jwt":
        refreshed = _refresh_cli_jwt_credentials_if_needed(creds)
        return str(refreshed.get("access_token") or "").strip()
    saved_api_key = str(creds.get("api_key") or "").strip()
    if saved_api_key:
        return saved_api_key
    return ""


class _Http:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        auth_header: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> Any:
        url = f"{self.base_url}{path}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        req_headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if auth_header is not None:
            if auth_header:
                req_headers["Authorization"] = auth_header
        elif self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, method=method, data=payload, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=int(timeout_seconds or 30)) as response:
                if response.status == 204:
                    return None
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if _env_flag_enabled(DEBUG_HTTP_ERRORS_ENV):
                raise RuntimeError(f"{method} {path} failed ({exc.code}): {details}") from exc
            raise RuntimeError(
                f"{method} {path} failed ({exc.code}). "
                f"Response body hidden by default; set {DEBUG_HTTP_ERRORS_ENV}=true to include it."
            ) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            message = str(exc.reason) if isinstance(exc, urllib.error.URLError) else str(exc)
            raise RuntimeError(f"{method} {path} failed (network): {message}") from exc

    def list_apps(self) -> dict[str, Any]:
        return self._request("/apps")

    def create_app(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("/apps", method="POST", body=body)

    def update_app(self, app_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(f"/apps/{app_id}", method="PATCH", body=body)

    def create_key(self, app_id: str, *, name: str, requests_per_minute: int) -> dict[str, Any]:
        return self._request(
            f"/apps/{app_id}/keys",
            method="POST",
            body={"name": name, "requests_per_minute": int(requests_per_minute)},
        )

    def list_x_keys(self, app_id: str) -> dict[str, Any]:
        return self._request(f"/apps/{app_id}/x-keys")

    def create_x_key(self, app_id: str, *, name: str, requests_per_minute: int) -> dict[str, Any]:
        return self._request(
            f"/apps/{app_id}/x-keys",
            method="POST",
            body={"name": name, "requests_per_minute": int(requests_per_minute)},
        )

    def revoke_x_key(self, app_id: str, key_id: str) -> None:
        _ = self._request(
            f"/apps/{app_id}/x-keys/{key_id}",
            method="DELETE",
        )

    def upsert_secret(self, app_id: str, *, name: str, values: dict[str, str]) -> dict[str, Any]:
        return self._request(
            f"/apps/{app_id}/secrets",
            method="POST",
            body={"name": name, "values": values},
        )

    def list_secrets(self, app_id: str) -> dict[str, Any]:
        return self._request(f"/apps/{app_id}/secrets")

    def delete_secret(self, app_id: str, name: str) -> None:
        _ = self._request(
            f"/apps/{app_id}/secrets/{name}",
            method="DELETE",
        )

    def run_app(
        self,
        app_id: str,
        *,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        input_payload: Optional[dict[str, Any]] = None,
        warmup: bool = False,
    ):
        headers: dict[str, str] = {}
        auth_header: Optional[str] = None
        if app_header_key:
            headers["X-Ara-App-Key"] = app_header_key
            auth_header = ""
        elif runtime_key:
            auth_header = f"Bearer {runtime_key}"
        else:
            raise RuntimeError("run_app requires runtime_key or app_header_key")
        run_timeout_seconds = 120
        raw_timeout = str(os.getenv("ARA_RUN_TIMEOUT_SECONDS", "120") or "120").strip()
        try:
            run_timeout_seconds = int(raw_timeout)
        except ValueError:
            run_timeout_seconds = 120
        if run_timeout_seconds < 30:
            run_timeout_seconds = 30
        return self._request(
            f"/v1/apps/{app_id}/run",
            method="POST",
            headers=headers,
            body={"agent_id": agent_id, "workflow_id": agent_id, "warmup": bool(warmup), "input": input_payload or {}},
            auth_header=auth_header,
            timeout_seconds=run_timeout_seconds,
        )

    def send_event(
        self,
        app_id: str,
        *,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
        agent_id: Optional[str],
        event_type: str,
        channel: str,
        source: str,
        message: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        auth_header: Optional[str] = None
        if app_header_key:
            headers["X-Ara-App-Key"] = app_header_key
            auth_header = ""
        elif runtime_key:
            auth_header = f"Bearer {runtime_key}"
        else:
            raise RuntimeError("send_event requires runtime_key or app_header_key")
        return self._request(
            f"/v1/apps/{app_id}/events",
            method="POST",
            headers=headers,
            body={
                "agent_id": agent_id,
                "workflow_id": agent_id,
                "event_type": event_type,
                "channel": channel,
                "source": source,
                "message": message,
                "payload": payload,
                "metadata": metadata,
            },
            auth_header=auth_header,
        )

    def submit_async_run(
        self,
        app_id: str,
        *,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
        agent_id: Optional[str],
        input_payload: dict[str, Any],
        warmup: bool = False,
        run_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        response_mode: str = "poll",
        callback: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        auth_header: Optional[str] = None
        if app_header_key:
            headers["X-Ara-App-Key"] = app_header_key
            auth_header = ""
        elif runtime_key:
            auth_header = f"Bearer {runtime_key}"
        else:
            raise RuntimeError("submit_async_run requires runtime_key or app_header_key")
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "workflow_id": agent_id,
            "warmup": bool(warmup),
            "input": input_payload,
            "response_mode": response_mode,
        }
        if run_id:
            body["run_id"] = run_id
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        if callback:
            body["callback"] = callback
        return self._request(
            f"/v1/apps/{app_id}/runs",
            method="POST",
            headers=headers,
            body=body,
            auth_header=auth_header,
        )

    def get_async_run_status(
        self,
        app_id: str,
        run_id: str,
        *,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        auth_header: Optional[str] = None
        if app_header_key:
            headers["X-Ara-App-Key"] = app_header_key
            auth_header = ""
        elif runtime_key:
            auth_header = f"Bearer {runtime_key}"
        else:
            raise RuntimeError("get_async_run_status requires runtime_key or app_header_key")
        return self._request(
            f"/v1/apps/{app_id}/runs/{run_id}",
            method="GET",
            headers=headers,
            auth_header=auth_header,
        )

    def stream_logs(
        self,
        app_id: str,
        *,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
    ):
        headers: dict[str, str] = {"Accept": "text/event-stream"}
        auth_header: Optional[str] = None
        if app_header_key:
            headers["X-Ara-App-Key"] = app_header_key
            auth_header = ""
        elif runtime_key:
            auth_header = f"Bearer {runtime_key}"
        else:
            raise RuntimeError("stream_logs requires runtime_key or app_header_key")

        # We intentionally mirror _request() auth header assembly here because
        # urllib streaming uses urlopen directly (instead of _request, which
        # buffers full responses and does not expose an iterable body stream).
        req_headers: dict[str, str] = {}
        if auth_header is not None:
            if auth_header:
                req_headers["Authorization"] = auth_header
        elif self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        req_headers.update(headers)
        req = urllib.request.Request(
            f"{self.base_url}/v1/apps/{app_id}/logs/stream",
            method="GET",
            headers=req_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if _env_flag_enabled(DEBUG_HTTP_ERRORS_ENV):
                raise RuntimeError(
                    f"GET /v1/apps/{app_id}/logs/stream failed ({exc.code}): {details}"
                ) from exc
            raise RuntimeError(
                f"GET /v1/apps/{app_id}/logs/stream failed ({exc.code}). "
                f"Response body hidden by default; set {DEBUG_HTTP_ERRORS_ENV}=true to include it."
            ) from exc

    def setup(self, app_id: str) -> dict[str, Any]:
        return self._request(f"/apps/{app_id}/setup")

    def invite(self, app_id: str, *, email: str, role: str, expires_in_hours: int) -> dict[str, Any]:
        return self._request(
            f"/apps/{app_id}/invites",
            method="POST",
            body={"email": email, "role": role, "expires_in_hours": int(expires_in_hours)},
        )

    def cli_auth_config(self) -> dict[str, Any]:
        return self._request("/auth/cli/config", method="GET", auth_header="")

    def cli_auth_device_start(
        self,
        *,
        provider: str,
        code_challenge: str,
        code_challenge_method: str = "s256",
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        return self._request(
            "/auth/cli/device/start",
            method="POST",
            auth_header="",
            body={
                "provider": provider,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "timeout_seconds": int(timeout_seconds or 180),
            },
        )

    def cli_auth_device_status(
        self,
        *,
        session_id: str,
        poll_token: str,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "session_id": str(session_id or "").strip(),
                "poll_token": str(poll_token or "").strip(),
            }
        )
        return self._request(f"/auth/cli/device/status?{query}", method="GET", auth_header="")

    def cli_whoami(self) -> dict[str, Any]:
        return self._request("/auth/cli/whoami", method="GET")

    def rotate_api_key(self) -> dict[str, Any]:
        return self._request("/apps/api-key/rotate", method="POST")

    def connect_token(self) -> dict[str, Any]:
        return self._request("/session/connect/token", method="POST", body={})

    def connect_exchange(self, *, token: str, public_key: str, key_comment: str) -> dict[str, Any]:
        return self._request(
            "/session/connect/exchange",
            method="POST",
            timeout_seconds=180,
            body={
                "token": token,
                "public_key": public_key,
                "key_comment": key_comment,
            },
        )


class AraClient:
    """Runtime client bound to one App manifest."""

    def __init__(self, *, manifest: dict[str, Any], api_base_url: str, api_key: str, cwd: pathlib.Path):
        self.manifest = dict(manifest)
        self.cwd = cwd
        self.http = _Http(api_base_url, api_key)

    @classmethod
    def from_env(cls, *, manifest: dict[str, Any], cwd: Optional[str] = None) -> "AraClient":
        base = pathlib.Path(cwd or os.getcwd())
        _read_dotenv(base / ".env")
        _read_dotenv(base / ".env.local")
        api_base_url = _resolve_api_base_url(DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL
        os.environ["ARA_API_BASE_URL"] = api_base_url
        api_key = _resolve_control_plane_bearer()
        if not api_key:
            raise RuntimeError("No credentials found. Set ARA_API_KEY or run `ara auth login`.")
        return cls(
            manifest=manifest,
            api_base_url=api_base_url,
            api_key=api_key,
            cwd=base,
        )

    def _find_app_by_slug(self) -> Optional[dict[str, Any]]:
        rows = self.http.list_apps().get("apps") or []
        for row in rows:
            if str(row.get("slug") or "") != str(self.manifest.get("slug") or ""):
                continue
            if str(row.get("role") or "") == "owner":
                return row
        return None

    def _resolve_runtime_key(self, explicit: Optional[str] = None) -> str:
        if explicit:
            return explicit
        env_key = os.getenv("ARA_RUNTIME_KEY", "").strip()
        if env_key:
            return env_key
        local_key = _load_local_runtime_key(self.cwd, slug=str(self.manifest.get("slug") or ""))
        if local_key:
            return local_key
        return ""

    def _resolve_app_header_key(self, explicit: Optional[str] = None) -> str:
        if explicit:
            return explicit
        env_key = os.getenv("ARA_APP_HEADER_KEY", "").strip()
        if env_key:
            return env_key
        return ""

    def _manifest_required_env_keys(self, runtime_profile: dict[str, Any]) -> list[str]:
        out: list[str] = []
        out.extend(
            _normalize_string_items(
                runtime_profile.get("__required_env_keys")
                if isinstance(runtime_profile.get("__required_env_keys"), list)
                else []
            )
        )
        agent_block = self.manifest.get("agent") if isinstance(self.manifest.get("agent"), dict) else {}
        tools = agent_block.get("tools") if isinstance(agent_block.get("tools"), list) else []
        for tool_row in tools:
            if not isinstance(tool_row, dict):
                continue
            out.extend(
                _normalize_string_items(
                    tool_row.get("required_env") if isinstance(tool_row.get("required_env"), list) else []
                )
            )
            source = str(tool_row.get("source") or "")
            if source:
                out.extend(_extract_secret_keys_from_source(source))
        return _normalize_string_items(out)

    def _build_env_secret_definition(self, keys: list[str]) -> Optional[SecretDefinition]:
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for key in _normalize_string_items(keys):
            value = str(os.getenv(key) or "").strip()
            if not value:
                missing.append(key)
                continue
            resolved[key] = value
        if missing:
            raise RuntimeError(
                "Missing required environment variables for deploy-time secret sync: "
                + ", ".join(missing)
            )
        if not resolved:
            return None
        return SecretDefinition.from_dict(resolved)

    def _extract_secret_sync_plan(self, runtime_profile: dict[str, Any]) -> tuple[list[SecretDefinition], dict[str, Any]]:
        definitions = _collect_runtime_secret_definitions(runtime_profile)
        extra_required_env = self._manifest_required_env_keys(runtime_profile)
        env_definition = self._build_env_secret_definition(extra_required_env)
        if env_definition is None:
            return definitions, runtime_profile

        definitions.append(env_definition)
        refs = runtime_profile.get("secret_refs") if isinstance(runtime_profile.get("secret_refs"), list) else []
        existing_refs = [dict(item) for item in refs if isinstance(item, dict)]
        existing_names = {str(item.get("name") or "").strip().lower() for item in existing_refs}
        if env_definition.name not in existing_names:
            existing_refs.append(env_definition.ref())
        runtime_profile["secret_refs"] = existing_refs
        return definitions, runtime_profile

    def _sync_secret_definitions(
        self,
        app_id: str,
        definitions: list[SecretDefinition],
        *,
        reconcile_runtime_secrets: bool,
    ) -> dict[str, Any]:
        def _raise_secrets_route_compat_error(exc: RuntimeError) -> NoReturn:
            message = str(exc)
            if f"/apps/{app_id}/secrets failed (404)" in message:
                raise RuntimeError(
                    "Secret sync failed because this backend does not support "
                    "App SDK secret routes yet. Upgrade backend to a version "
                    f"with /apps/{app_id}/secrets support, or remove runtime(secrets=...) declarations."
                ) from exc
            raise exc

        synced: list[str] = []
        referenced_only: list[str] = []
        desired_names: set[str] = set()
        for definition in definitions:
            desired_names.add(definition.name)
            if definition.values is None:
                referenced_only.append(definition.name)
                continue
            try:
                self.http.upsert_secret(app_id, name=definition.name, values=definition.values)
            except RuntimeError as exc:
                _raise_secrets_route_compat_error(exc)
            synced.append(definition.name)
        if reconcile_runtime_secrets:
            try:
                existing_rows = self.http.list_secrets(app_id).get("secrets") or []
            except RuntimeError as exc:
                _raise_secrets_route_compat_error(exc)
            for row in existing_rows:
                if not isinstance(row, dict):
                    continue
                existing_name = str(row.get("name") or "").strip().lower()
                if not existing_name or existing_name in desired_names:
                    continue
                try:
                    self.http.delete_secret(app_id, existing_name)
                except RuntimeError as exc:
                    # Idempotent reconciliation: concurrent deploys may have already
                    # deleted this stale secret.
                    if f"/apps/{app_id}/secrets/{existing_name} failed (404)" in str(exc):
                        continue
                    _raise_secrets_route_compat_error(exc)
        return {"synced": synced, "referenced_only": referenced_only}

    def deploy(
        self,
        *,
        activate: bool = True,
        key_name: Optional[str] = None,
        key_rpm: int = 60,
        warm: bool = False,
        warm_agent_id: Optional[str] = None,
        on_existing: Optional[str] = "update",
    ) -> dict[str, Any]:
        if on_existing is None:
            on_existing = "update"
        if on_existing not in ("update", "error"):
            raise ValueError("on_existing must be one of: update, error")

        existing = self._find_app_by_slug()
        app_id = str(existing.get("id")) if existing else ""
        if app_id and on_existing == "error":
            raise RuntimeError(
                f"Project '{self.manifest.get('slug')}' already exists for this account (app_id={app_id})."
            )

        runtime_profile = dict(self.manifest.get("runtime_profile") or {})
        had_secret_refs = "secret_refs" in runtime_profile
        secret_definitions, runtime_profile = self._extract_secret_sync_plan(runtime_profile)
        reconcile_runtime_secrets = had_secret_refs or ("secret_refs" in runtime_profile)
        runtime_profile.pop("__secret_definitions", None)
        runtime_profile.pop("__required_env_keys", None)

        payload = {
            "name": self.manifest.get("name"),
            "description": self.manifest.get("description") or "",
            "agent": self.manifest.get("agent") or {},
            "workflows": self.manifest.get("workflows") or [],
            "interfaces": self.manifest.get("interfaces") or {},
            "runtime_profile": runtime_profile,
        }

        if app_id:
            if activate:
                payload["status"] = "active"
            self.http.update_app(app_id, payload)
        else:
            try:
                created = self.http.create_app({**payload, "slug": self.manifest.get("slug")})
            except RuntimeError as exc:
                if "POST /apps failed (409)" in str(exc):
                    raise RuntimeError(
                        "Project name is already taken. Choose a different DNS-safe project_name "
                        "(lowercase letters, digits, hyphens) and retry deploy."
                    ) from None
                raise
            app_id = str((created.get("app") or {}).get("id") or "")
            if not app_id:
                raise RuntimeError("deploy failed: missing app id")
            if activate:
                self.http.update_app(app_id, {"status": "active"})

        secret_sync = self._sync_secret_definitions(
            app_id,
            secret_definitions,
            reconcile_runtime_secrets=reconcile_runtime_secrets,
        )

        key_out = self.http.create_key(
            app_id,
            name=(key_name or f"{self.manifest.get('slug')}-py-local"),
            requests_per_minute=int(key_rpm),
        )
        runtime_key = str(key_out.get("key") or "").strip()
        if not runtime_key:
            raise RuntimeError("deploy failed: runtime key missing")
        _save_local_runtime_key(self.cwd, slug=str(self.manifest.get("slug") or ""), runtime_key=runtime_key)

        warmup = None
        if warm:
            warmup = self.http.run_app(
                app_id,
                runtime_key=runtime_key,
                agent_id=warm_agent_id,
                input_payload={},
                warmup=True,
            )

        return {
            "app_id": app_id,
            "slug": self.manifest.get("slug"),
            "runtime_key_created": True,
            "runtime_key": runtime_key,
            "warmup": warmup,
            "secrets": secret_sync,
        }

    def run(
        self,
        *,
        agent_id: Optional[str] = None,
        input_payload: Optional[dict[str, Any]] = None,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
    ):
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        resolved_header_key = self._resolve_app_header_key(app_header_key)
        key = self._resolve_runtime_key(runtime_key) if not resolved_header_key else ""
        if not resolved_header_key and not key:
            raise RuntimeError("Missing runtime key. Set ARA_RUNTIME_KEY, ARA_APP_HEADER_KEY, or run deploy/setup-auth first.")
        return self.http.run_app(
            str(app["id"]),
            runtime_key=key,
            app_header_key=resolved_header_key,
            agent_id=agent_id,
            input_payload=input_payload or {},
        )

    def events(
        self,
        *,
        agent_id: Optional[str],
        event_type: str,
        channel: str,
        source: str,
        message: str,
        payload: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
    ) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        resolved_header_key = self._resolve_app_header_key(app_header_key)
        key = self._resolve_runtime_key(runtime_key) if not resolved_header_key else ""
        if not resolved_header_key and not key:
            raise RuntimeError("Missing runtime key. Set ARA_RUNTIME_KEY, ARA_APP_HEADER_KEY, or run deploy/setup-auth first.")
        return self.http.send_event(
            str(app["id"]),
            runtime_key=key,
            app_header_key=resolved_header_key,
            agent_id=agent_id,
            event_type=event_type,
            channel=channel,
            source=source,
            message=message,
            payload=payload or {},
            metadata=metadata or {},
            idempotency_key=idempotency_key,
        )

    def setup(self) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        return self.http.setup(str(app["id"]))

    def setup_auth(
        self,
        *,
        x_key_name: Optional[str] = None,
        x_key_rpm: int = 30,
        ensure_runtime_key: bool = True,
    ) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        app_id = str(app["id"])

        runtime_key = self._resolve_runtime_key()
        runtime_key_created = False
        if ensure_runtime_key and not runtime_key:
            key_out = self.http.create_key(
                app_id,
                name=f"{self.manifest.get('slug')}-py-local",
                requests_per_minute=60,
            )
            runtime_key = str(key_out.get("key") or "").strip()
            if runtime_key:
                runtime_key_created = True
                _save_local_runtime_key(self.cwd, slug=str(self.manifest.get("slug") or ""), runtime_key=runtime_key)

        app_header_key = self._resolve_app_header_key()
        x_key_created = False
        x_key_id = ""
        x_key_prefix = ""
        if not app_header_key:
            created = self.http.create_x_key(
                app_id,
                name=(x_key_name or f"{self.manifest.get('slug')}-x-header"),
                requests_per_minute=int(x_key_rpm),
            )
            app_header_key = str(created.get("key") or "").strip()
            x_key_created = bool(app_header_key)
            x_key_id = str(created.get("id") or "")
            x_key_prefix = str(created.get("key_prefix") or "")
        else:
            existing = self.http.list_x_keys(app_id).get("keys") or []
            if isinstance(existing, list):
                for item in existing:
                    if str(item.get("is_active")).lower() == "false":
                        continue
                    prefix = str(item.get("key_prefix") or "")
                    if prefix and app_header_key.startswith(prefix):
                        x_key_id = str(item.get("id") or "")
                        x_key_prefix = prefix
                        break

        return {
            "app_id": app_id,
            "slug": self.manifest.get("slug"),
            "runtime_key_present": bool(runtime_key),
            "runtime_key_created": runtime_key_created,
            "runtime_key": runtime_key,
            "app_header_key_present": bool(app_header_key),
            "app_header_key_created": x_key_created,
            "app_header_key_id": x_key_id,
            "app_header_key_prefix": x_key_prefix,
            "app_header_key": app_header_key,
        }

    def run_async(
        self,
        *,
        agent_id: Optional[str],
        input_payload: Optional[dict[str, Any]] = None,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
        response_mode: str = "poll",
        callback: Optional[dict[str, Any]] = None,
        run_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        warmup: bool = False,
    ) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        resolved_header_key = self._resolve_app_header_key(app_header_key)
        key = self._resolve_runtime_key(runtime_key) if not resolved_header_key else ""
        if not resolved_header_key and not key:
            raise RuntimeError("Missing runtime key. Set ARA_RUNTIME_KEY, ARA_APP_HEADER_KEY, or run deploy/setup-auth first.")
        return self.http.submit_async_run(
            str(app["id"]),
            runtime_key=key,
            app_header_key=resolved_header_key,
            agent_id=agent_id,
            input_payload=input_payload or {},
            warmup=warmup,
            run_id=run_id,
            idempotency_key=idempotency_key,
            response_mode=response_mode,
            callback=callback,
        )

    def run_status(
        self,
        *,
        run_id: str,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
    ) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        rid = str(run_id or "").strip()
        if not rid:
            raise RuntimeError("run_status requires run_id")
        resolved_header_key = self._resolve_app_header_key(app_header_key)
        key = self._resolve_runtime_key(runtime_key) if not resolved_header_key else ""
        if not resolved_header_key and not key:
            raise RuntimeError("Missing runtime key. Set ARA_RUNTIME_KEY, ARA_APP_HEADER_KEY, or run deploy/setup-auth first.")
        return self.http.get_async_run_status(
            str(app["id"]),
            rid,
            runtime_key=key,
            app_header_key=resolved_header_key,
        )

    def logs(
        self,
        *,
        runtime_key: Optional[str] = None,
        app_header_key: Optional[str] = None,
    ):
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        resolved_header_key = self._resolve_app_header_key(app_header_key)
        key = self._resolve_runtime_key(runtime_key) if not resolved_header_key else ""
        if not resolved_header_key and not key:
            raise RuntimeError("Missing runtime key. Set ARA_RUNTIME_KEY, ARA_APP_HEADER_KEY, or run deploy/setup-auth first.")
        for row in self.http.stream_logs(
            str(app["id"]),
            runtime_key=key,
            app_header_key=resolved_header_key,
        ):
            yield row

    def invite(self, *, email: str, role: str = "viewer", expires_in_hours: int = 24 * 7) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        return self.http.invite(str(app["id"]), email=email, role=role, expires_in_hours=expires_in_hours)


class AraRuntimeClient:
    """User-scoped runtime client (session/runtime tooling)."""

    def __init__(self, *, api_base_url: str, api_key: str, cwd: pathlib.Path):
        self.cwd = cwd
        self.http = _Http(api_base_url, api_key)

    @classmethod
    def from_env(cls, *, cwd: Optional[str] = None) -> "AraRuntimeClient":
        base = pathlib.Path(cwd or os.getcwd())
        _read_dotenv(base / ".env")
        _read_dotenv(base / ".env.local")
        api_base_url = _resolve_api_base_url(DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL
        os.environ["ARA_API_BASE_URL"] = api_base_url
        api_key = _resolve_control_plane_bearer()
        if not api_key:
            raise RuntimeError("No credentials found. Set ARA_API_KEY or run `ara auth login`.")
        return cls(
            api_base_url=api_base_url,
            api_key=api_key,
            cwd=base,
        )

    @staticmethod
    def _with_query(path: str, params: dict[str, Any]) -> str:
        encoded = urllib.parse.urlencode(
            {k: v for k, v in params.items() if str(v or "").strip()},
            doseq=True,
        )
        if not encoded:
            return path
        return f"{path}?{encoded}"

    def session_start(self) -> dict[str, Any]:
        return self.http._request("/session/start", method="POST", body={}, timeout_seconds=120)

    def session_status(self) -> dict[str, Any]:
        return self.http._request("/session/status", method="GET")

    def session_stop(self) -> dict[str, Any]:
        result = self.http._request("/session/stop", method="POST", body={})
        if result is None:
            return {"ok": True}
        if isinstance(result, dict):
            return result
        return {"ok": True, "result": result}

    def session_exec(self, *, command: str, timeout_seconds: int = 90) -> dict[str, Any]:
        command_text = str(command or "").strip()
        if not command_text:
            raise RuntimeError("session exec requires a non-empty command")
        timeout_value = max(5, min(int(timeout_seconds or 90), 600))
        request_timeout = max(30, timeout_value + 30)
        return self.http._request(
            "/session/terminal/exec",
            method="POST",
            body={
                "command": command_text,
                "timeout_seconds": timeout_value,
            },
            timeout_seconds=request_timeout,
        )

    def session_heartbeat(self) -> dict[str, Any]:
        result = self.http._request("/session/heartbeat", method="POST", body={})
        if result is None:
            return {"ok": False, "reason": "empty_response"}
        if isinstance(result, dict):
            return result
        return {"ok": bool(result), "result": result}

    def automation_list_jobs(self) -> dict[str, Any]:
        return self.http._request("/automations/jobs", method="GET")

    def automation_create_job(
        self,
        *,
        name: str,
        schedule_kind: str,
        timezone: str = "UTC",
        every_seconds: Optional[int] = None,
        schedule_expr: str = "",
        payload: Optional[dict[str, Any]] = None,
        execution_mode: str = "sandbox_required",
        misfire_policy: str = "fire_latest_only",
        max_retries: int = 3,
        retry_backoff_seconds: int = 30,
    ) -> dict[str, Any]:
        job_name = str(name or "").strip()
        if not job_name:
            raise RuntimeError("automation add requires --name")
        normalized_payload = dict(payload or {})
        payload_kind = str(normalized_payload.get("kind") or "").strip().lower()
        if payload_kind in {"agent_turn", "app_agent_call"}:
            normalized_payload.setdefault("deliver", True)
            normalized_payload.setdefault("channel", "linq")
        kind = str(schedule_kind or "").strip().lower()
        if kind not in {"every", "cron"}:
            raise RuntimeError("automation add requires schedule kind 'every' or 'cron'")
        if kind == "every":
            if every_seconds is None:
                raise RuntimeError("automation add with every schedule requires --every-seconds")
            try:
                every_seconds = int(every_seconds)
            except (TypeError, ValueError):
                raise RuntimeError("--every-seconds must be an integer") from None
        else:
            if not str(schedule_expr or "").strip():
                raise RuntimeError("automation add with cron schedule requires --cron")
        return self.http._request(
            "/automations/jobs",
            method="POST",
            body={
                "name": job_name,
                "schedule_kind": kind,
                "every_seconds": every_seconds if kind == "every" else None,
                "schedule_expr": str(schedule_expr or "").strip() if kind == "cron" else None,
                "timezone": str(timezone or "UTC").strip() or "UTC",
                "payload": normalized_payload,
                "execution_mode": str(execution_mode or "sandbox_required"),
                "misfire_policy": str(misfire_policy or "fire_latest_only"),
                "max_retries": int(max_retries),
                "retry_backoff_seconds": int(retry_backoff_seconds),
            },
        )

    def automation_update_job(
        self,
        *,
        job_id: str,
        name: Optional[str] = None,
        enabled: Optional[bool] = None,
        payload: Optional[dict[str, Any]] = None,
        max_retries: Optional[int] = None,
        retry_backoff_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        resolved_job_id = str(job_id or "").strip()
        if not resolved_job_id:
            raise RuntimeError("automation update requires --id")
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = str(name).strip()
        if enabled is not None:
            body["enabled"] = bool(enabled)
        if payload is not None:
            body["payload"] = dict(payload)
        if max_retries is not None:
            body["max_retries"] = int(max_retries)
        if retry_backoff_seconds is not None:
            body["retry_backoff_seconds"] = int(retry_backoff_seconds)
        if not body:
            raise RuntimeError(
                "automation update requires at least one field (--name/--enable/--disable/--payload-json/--max-retries/--retry-backoff-seconds)"
            )
        return self.http._request(
            f"/automations/jobs/{urllib.parse.quote(resolved_job_id, safe='')}",
            method="PATCH",
            body=body,
        )

    def automation_delete_job(self, *, job_id: str) -> dict[str, Any]:
        resolved_job_id = str(job_id or "").strip()
        if not resolved_job_id:
            raise RuntimeError("automation remove requires --id")
        return self.http._request(
            f"/automations/jobs/{urllib.parse.quote(resolved_job_id, safe='')}",
            method="DELETE",
        )

    def automation_list_runs(self, *, state: str = "") -> dict[str, Any]:
        path = self._with_query("/automations/runs", {"state": str(state or "").strip()})
        return self.http._request(path, method="GET")

    def automation_replay_run(self, *, run_id: str) -> dict[str, Any]:
        resolved_run_id = str(run_id or "").strip()
        if not resolved_run_id:
            raise RuntimeError("automation replay requires --run-id")
        return self.http._request(
            f"/automations/runs/{urllib.parse.quote(resolved_run_id, safe='')}/replay",
            method="POST",
            body={},
        )

    def automation_delete_job_safe(
        self,
        *,
        job_id: str,
        disable_on_failure: bool = True,
    ) -> dict[str, Any]:
        resolved_job_id = str(job_id or "").strip()
        if not resolved_job_id:
            raise RuntimeError("automation remove requires --id")
        try:
            delete_result = self.automation_delete_job(job_id=resolved_job_id)
            return {
                "ok": True,
                "action": "deleted",
                "job_id": resolved_job_id,
                "result": delete_result,
            }
        except RuntimeError as delete_exc:
            if not disable_on_failure:
                return {
                    "ok": False,
                    "action": "delete_failed",
                    "job_id": resolved_job_id,
                    "error": str(delete_exc),
                }
            try:
                disable_result = self.automation_update_job(
                    job_id=resolved_job_id,
                    enabled=False,
                )
            except RuntimeError as disable_exc:
                return {
                    "ok": False,
                    "action": "delete_failed_disable_failed",
                    "job_id": resolved_job_id,
                    "error": str(delete_exc),
                    "disable_error": str(disable_exc),
                }
            return {
                "ok": True,
                "action": "disabled_after_delete_failure",
                "job_id": resolved_job_id,
                "delete_error": str(delete_exc),
                "result": disable_result,
            }

    def automation_purge_jobs(
        self,
        *,
        disable_on_failure: bool = True,
    ) -> dict[str, Any]:
        rows = self.automation_list_jobs().get("jobs") or []
        results: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("id") or "").strip()
            if not job_id:
                continue
            result = self.automation_delete_job_safe(
                job_id=job_id,
                disable_on_failure=disable_on_failure,
            )
            result.setdefault("name", str(row.get("name") or ""))
            results.append(result)
        summary = {
            "ok": all(bool(item.get("ok")) for item in results),
            "initial_count": len(rows),
            "deleted_count": sum(1 for item in results if item.get("action") == "deleted"),
            "disabled_count": sum(1 for item in results if item.get("action") == "disabled_after_delete_failure"),
            "failed_count": sum(1 for item in results if not item.get("ok")),
            "results": results,
        }
        final_rows = self.automation_list_jobs().get("jobs") or []
        summary["remaining_count"] = len(final_rows)
        summary["remaining_enabled_count"] = sum(
            1 for item in final_rows if isinstance(item, dict) and bool(item.get("enabled"))
        )
        summary["remaining_job_ids"] = [
            str(item.get("id") or "")
            for item in final_rows
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        return summary

    def capabilities(self, *, session_id: str, agent_id: str = "") -> dict[str, Any]:
        path = self._with_query(
            "/session/runtime/capabilities",
            {"session_id": session_id, "agent_id": agent_id},
        )
        return self.http._request(path, method="GET")

    def skills(self, *, session_id: str) -> dict[str, Any]:
        path = self._with_query("/session/runtime/skills", {"session_id": session_id})
        return self.http._request(path, method="GET")

    def tools(self, *, session_id: str, kind: str = "all", agent_id: str = "") -> dict[str, Any]:
        path = self._with_query(
            "/session/runtime/tools",
            {"session_id": session_id, "kind": kind, "agent_id": agent_id},
        )
        return self.http._request(path, method="GET")

    def execute_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        agent_id: str = "",
    ) -> dict[str, Any]:
        return self.http._request(
            "/session/runtime/tools/execute",
            method="POST",
            body={
                "session_id": session_id,
                "tool_name": tool_name,
                "args": args,
                "agent_id": agent_id or None,
            },
        )

    def control_actions(self, *, session_id: str) -> dict[str, Any]:
        path = self._with_query("/session/runtime/control/actions", {"session_id": session_id})
        return self.http._request(path, method="GET")

    def control_call(
        self,
        *,
        session_id: str,
        action: str,
        args: dict[str, Any],
        timeout_ms: int = 8000,
    ) -> dict[str, Any]:
        return self.http._request(
            "/session/runtime/control/call",
            method="POST",
            body={
                "session_id": session_id,
                "action": action,
                "args": args,
                "timeout_ms": int(timeout_ms),
            },
        )

    def chat(
        self,
        *,
        message: str,
        model: str = "",
        conversation_id: str = "",
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Send a message through the full agent loop (LLM + tools + memory).

        Calls POST /chat (SSE) — the same code path as the Ara UI.
        Parses the event stream and returns a structured result.

        Returns::

            {
                "text": "final assistant response",
                "tool_calls": [
                    {"name": "read_file", "args": {"path": "..."}, "result": "..."},
                ],
                "reasoning": "...",
            }
        """
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
        }
        if model:
            body["model"] = model
        if conversation_id:
            body["chatId"] = conversation_id

        payload = json.dumps(body).encode("utf-8")
        req_headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.http.api_key:
            req_headers["Authorization"] = f"Bearer {self.http.api_key}"

        req = urllib.request.Request(
            f"{self.http.base_url}/chat",
            method="POST",
            data=payload,
            headers=req_headers,
        )

        text = ""
        reasoning = ""
        tool_calls: list[dict[str, Any]] = []
        pending_tools: dict[str, dict[str, Any]] = {}

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line == "data: [DONE]":
                        break
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    if etype == "text-delta":
                        text += event.get("delta", "")
                    elif etype == "reasoning-delta":
                        reasoning += event.get("delta", "")
                    elif etype == "tool-input-available":
                        tc_id = event.get("toolCallId", "")
                        entry = {
                            "name": event.get("toolName", ""),
                            "args": event.get("input", {}),
                            "result": "",
                        }
                        pending_tools[tc_id] = entry
                        tool_calls.append(entry)
                    elif etype == "tool-output-available":
                        tc_id = event.get("toolCallId", "")
                        if tc_id in pending_tools:
                            pending_tools[tc_id]["result"] = event.get("output", "")
                    elif etype == "error":
                        raise RuntimeError(event.get("errorText", "Agent error"))

        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"chat() failed ({exc.code}): {details}") from exc

        result: dict[str, Any] = {"text": text, "tool_calls": tool_calls}
        if reasoning:
            result["reasoning"] = reasoning
        return result


def _parse_pairs(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value
    return out


def _parse_json_object_arg(raw: str, *, flag_name: str) -> dict[str, Any]:
    value = str(raw or "").strip()
    if not value:
        return {}
    if value.startswith("@"):
        path = pathlib.Path(value[1:]).expanduser()
        if not path.exists():
            raise RuntimeError(f"{flag_name} file not found: {path}")
        value = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{flag_name} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{flag_name} must decode to a JSON object")
    return parsed


def _cli_ssh_dir_path() -> pathlib.Path:
    return pathlib.Path.home() / CLI_CREDENTIALS_DIRNAME / CLI_SSH_DIRNAME


def _cli_ssh_private_key_path() -> pathlib.Path:
    return _cli_ssh_dir_path() / CLI_SSH_KEY_BASENAME


def _cli_ssh_public_key_path() -> pathlib.Path:
    return pathlib.Path(str(_cli_ssh_private_key_path()) + ".pub")


def _cli_ssh_proxy_token_path() -> pathlib.Path:
    return _cli_ssh_dir_path() / CLI_SSH_PROXY_TOKEN_FILENAME


def _ensure_local_ssh_keypair() -> tuple[pathlib.Path, pathlib.Path]:
    key_dir = _cli_ssh_dir_path()
    key_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(key_dir, 0o700)
    except OSError:
        logger.debug("Failed to chmod ssh dir: %s", key_dir, exc_info=True)
    private_key = _cli_ssh_private_key_path()
    public_key = _cli_ssh_public_key_path()
    if not private_key.exists():
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(private_key),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if not public_key.exists():
        with public_key.open("w", encoding="utf-8") as fp:
            subprocess.run(
                ["ssh-keygen", "-y", "-f", str(private_key)],
                check=True,
                stdout=fp,
                stderr=subprocess.DEVNULL,
            )
        public_key.write_text(public_key.read_text(encoding="utf-8").strip() + "\n", encoding="utf-8")
    try:
        os.chmod(private_key, 0o600)
    except OSError:
        logger.debug("Failed to chmod private key: %s", private_key, exc_info=True)
    try:
        os.chmod(public_key, 0o644)
    except OSError:
        logger.debug("Failed to chmod public key: %s", public_key, exc_info=True)
    return private_key, public_key


def _write_proxy_token_file(token: str) -> pathlib.Path:
    path = _cli_ssh_proxy_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(token or "").strip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.debug("Failed to chmod proxy token file: %s", path, exc_info=True)
    return path


def _ssh_config_quote_path(path_value: pathlib.Path) -> str:
    raw = str(path_value)
    escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{escaped}\""


def _extract_connect_token(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise RuntimeError("connect URI is required")
    if value.startswith("ara://"):
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "ara":
            raise RuntimeError("connect URI must use ara:// scheme")
        params = urllib.parse.parse_qs(parsed.query or "")
        token = str((params.get("token") or [""])[0]).strip()
        if not token:
            raise RuntimeError("connect URI missing token query parameter")
        return token
    return value


def _api_base_to_ws_base(api_base_url: str) -> str:
    parsed = urllib.parse.urlparse(str(api_base_url or "").strip())
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        scheme = "wss"
    netloc = parsed.netloc
    if not netloc:
        raise RuntimeError(f"Invalid ARA_API_BASE_URL: {api_base_url}")
    return f"{scheme}://{netloc}"


def _upsert_ssh_config(alias: str, config_block: str) -> pathlib.Path:
    ssh_dir = pathlib.Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ssh_dir, 0o700)
    except OSError:
        logger.debug("Failed to chmod ~/.ssh", exc_info=True)
    config_path = ssh_dir / "config"
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    lines = existing.splitlines()
    start = None
    end = None
    host_re = re.compile(r"^\s*Host\s+(.+?)\s*$")
    for idx, line in enumerate(lines):
        match = host_re.match(line)
        if not match:
            continue
        host_name = match.group(1).strip()
        if start is None and host_name == alias:
            start = idx
            continue
        if start is not None and end is None:
            end = idx
            break
    new_lines: list[str] = []
    if start is None:
        new_lines = lines + ([""] if lines else []) + config_block.splitlines()
    else:
        if end is None:
            end = len(lines)
        new_lines = lines[:start] + config_block.splitlines() + lines[end:]
    payload = "\n".join(new_lines).rstrip() + "\n"
    config_path.write_text(payload, encoding="utf-8")
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        logger.debug("Failed to chmod ~/.ssh/config", exc_info=True)
    return config_path


def _is_transient_connect_exchange_error(exc: Exception) -> bool:
    message = str(exc).lower()
    transient_markers = (
        "post /session/connect/exchange failed (503)",
        "post /session/connect/exchange failed (network)",
        "timed out",
        "connection reset",
        "temporarily unavailable",
        "ssh_bootstrap_failed",
        "sandbox_unavailable",
    )
    return any(marker in message for marker in transient_markers)


def run_connect_cli(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ara local SSH connect helper")
    parser.add_argument("uri", help='connect URI, e.g. ara://connect?token=...')
    parser.add_argument("--key-comment", default=socket.gethostname() or "local")
    args = parser.parse_args(argv)

    token = _extract_connect_token(args.uri)
    api_base_url = _resolve_api_base_url(DEFAULT_API_BASE_URL)
    bearer = _resolve_control_plane_bearer()
    if not bearer:
        raise SystemExit("ara connect: not logged in. Run `ara auth login` first.")

    try:
        private_key, public_key = _ensure_local_ssh_keypair()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"ara connect: failed generating local SSH keypair ({exc})") from None
    try:
        public_key_text = public_key.read_text(encoding="utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"ara connect: failed reading generated public key ({exc})") from None

    http = _Http(api_base_url, bearer)
    exchange: Optional[dict[str, Any]] = None
    last_error: Optional[Exception] = None
    for attempt_index, delay_seconds in enumerate(CONNECT_EXCHANGE_RETRY_DELAYS_SECONDS):
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            exchange = http.connect_exchange(
                token=token,
                public_key=public_key_text,
                key_comment=str(args.key_comment or "local"),
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            should_retry = _is_transient_connect_exchange_error(exc) and (
                attempt_index < len(CONNECT_EXCHANGE_RETRY_DELAYS_SECONDS) - 1
            )
            if should_retry:
                continue
            raise SystemExit(f"ara connect: {exc}") from None
    if exchange is None:
        if last_error is not None:
            raise SystemExit(f"ara connect: {last_error}") from None
        raise SystemExit("ara connect: exchange failed unexpectedly")

    host_alias = str(exchange.get("host_alias") or CLI_SSH_ALIAS).strip() or CLI_SSH_ALIAS
    proxy_token = str(exchange.get("proxy_token") or "").strip()
    if not proxy_token:
        raise SystemExit("ara connect: exchange failed (missing proxy token)")
    proxy_token_file = _write_proxy_token_file(proxy_token)
    ssh_config_block = "\n".join(
        [
            f"Host {host_alias}",
            "  HostName 127.0.0.1",
            "  Port 22",
            "  User root",
            f"  IdentityFile {_ssh_config_quote_path(private_key)}",
            "  IdentitiesOnly yes",
            "  StrictHostKeyChecking no",
            "  UserKnownHostsFile /dev/null",
            f"  ProxyCommand ara ssh-proxy --token-file {shlex.quote(str(proxy_token_file))}",
        ]
    )
    config_path = _upsert_ssh_config(host_alias, ssh_config_block)

    commands = exchange.get("commands") if isinstance(exchange.get("commands"), dict) else {}
    ssh_command = str(commands.get("ssh") or f"ssh {host_alias}").strip()
    vscode_command = str(commands.get("vscode") or f"code --remote ssh-remote+{host_alias} {CLI_WORKSPACE_PATH}").strip()
    sshfs_command = str(commands.get("sshfs_mount") or f"mkdir -p ~/AraWorkspace && sshfs {host_alias}:{CLI_WORKSPACE_PATH} ~/AraWorkspace").strip()

    print(
        json.dumps(
            {
                "ok": True,
                "host_alias": host_alias,
                "ssh_config_path": str(config_path),
                "private_key_path": str(private_key),
                "proxy_token_file": str(proxy_token_file),
                "session_id": str(exchange.get("session_id") or ""),
                "commands": {
                    "ssh": ssh_command,
                    "vscode": vscode_command,
                    "sshfs_mount": sshfs_command,
                },
                "next": {
                    "terminal": ssh_command,
                    "vscode": vscode_command,
                },
            },
            indent=2,
        )
    )


async def _run_ssh_proxy(token: str) -> None:
    try:
        import websockets
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("ssh proxy requires the `websockets` package") from exc

    api_base = _resolve_api_base_url(DEFAULT_API_BASE_URL)
    ws_base = _api_base_to_ws_base(api_base)
    ws_url = f"{ws_base}/session/ssh/proxy"

    async with websockets.connect(  # type: ignore[attr-defined]
        ws_url,
        additional_headers={"Authorization": f"Bearer {token}"},
        ping_interval=30,
        ping_timeout=120,
        max_size=None,
    ) as ws:
        loop = asyncio.get_running_loop()

        async def _stdin_to_ws() -> None:
            fd = sys.stdin.fileno()
            while True:
                chunk = await loop.run_in_executor(None, os.read, fd, 65536)
                if not chunk:
                    await ws.close()
                    break
                await ws.send(chunk)

        async def _ws_to_stdout() -> None:
            fd = sys.stdout.fileno()
            async for message in ws:
                if isinstance(message, bytes):
                    os.write(fd, message)
                else:
                    os.write(fd, str(message).encode("utf-8"))

        t_in = asyncio.create_task(_stdin_to_ws())
        t_out = asyncio.create_task(_ws_to_stdout())
        done, pending = await asyncio.wait([t_in, t_out], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()


def run_ssh_proxy_cli(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ara SSH ProxyCommand bridge")
    parser.add_argument("--token", default="")
    parser.add_argument("--token-file", default="")
    args = parser.parse_args(argv)
    token = str(args.token or "").strip()
    token_file = str(args.token_file or "").strip()
    if not token and token_file:
        path = pathlib.Path(token_file).expanduser()
        if not path.exists():
            raise SystemExit(f"ara ssh-proxy: token file not found: {path}")
        token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("ara ssh-proxy: --token or --token-file is required")
    try:
        asyncio.run(_run_ssh_proxy(token))
    except RuntimeError as exc:
        raise SystemExit(f"ara ssh-proxy: {exc}") from None


def _format_runtime_log_line(row: dict[str, Any]) -> str:
    timestamp = str(row.get("timestamp") or row.get("created_at") or "").strip()
    level = str(row.get("level") or "info").strip().upper() or "INFO"
    run_id = str(row.get("run_id") or "-").strip() or "-"
    event_type = str(row.get("event_type") or "runtime.event").strip() or "runtime.event"
    message = str(row.get("message") or "").strip()
    base = f"{timestamp} {level} run={run_id} event={event_type}"
    return f"{base} {message}".strip()


def run_runtime_cli(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ara runtime CLI")
    sub = parser.add_subparsers(dest="scope", required=True)

    p_session = sub.add_parser("session")
    sub_session = p_session.add_subparsers(dest="command", required=True)
    sub_session.add_parser("start")
    sub_session.add_parser("status")
    sub_session.add_parser("stop")
    p_session_exec = sub_session.add_parser("exec")
    p_session_exec.add_argument("--command", dest="shell_command", default="")
    p_session_exec.add_argument("--timeout-seconds", type=int, default=90)
    p_session_keepalive = sub_session.add_parser("keepalive")
    p_session_keepalive.add_argument("--interval-seconds", type=int, default=30)
    p_session_keepalive.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="Number of heartbeats to send (0 = run forever).",
    )
    p_session_keepalive.add_argument(
        "--max-failures",
        type=int,
        default=3,
        help="Stop after this many consecutive heartbeat failures.",
    )

    p_automation = sub.add_parser("automation")
    sub_automation = p_automation.add_subparsers(dest="command", required=True)
    sub_automation.add_parser("list")
    p_automation_add = sub_automation.add_parser("add")
    p_automation_add.add_argument("--name", required=True)
    automation_schedule = p_automation_add.add_mutually_exclusive_group(required=True)
    automation_schedule.add_argument("--every-seconds", type=int, default=None)
    automation_schedule.add_argument("--cron", default="")
    p_automation_add.add_argument("--timezone", default="UTC")
    p_automation_add.add_argument(
        "--payload-json",
        default="",
        help="JSON object payload.",
    )
    p_automation_add.add_argument("--deliver", action="store_true")
    p_automation_add.add_argument(
        "--execution-mode",
        choices=["sandbox_required", "centralized_ok"],
        default="sandbox_required",
    )
    p_automation_add.add_argument(
        "--misfire-policy",
        choices=["fire_all", "fire_latest_only", "skip_if_late"],
        default="fire_latest_only",
    )
    p_automation_add.add_argument("--max-retries", type=int, default=3)
    p_automation_add.add_argument("--retry-backoff-seconds", type=int, default=30)

    p_automation_update = sub_automation.add_parser("update")
    p_automation_update.add_argument("--id", dest="job_id", required=True)
    p_automation_update.add_argument("--name", default=None)
    p_automation_update.add_argument("--payload-json", default="")
    p_automation_update.add_argument("--deliver", action="store_true")
    p_automation_update.add_argument("--enable", action="store_true")
    p_automation_update.add_argument("--disable", action="store_true")
    p_automation_update.add_argument("--max-retries", type=int, default=None)
    p_automation_update.add_argument("--retry-backoff-seconds", type=int, default=None)

    p_automation_remove = sub_automation.add_parser("remove")
    p_automation_remove.add_argument("--id", dest="job_id", required=True)
    p_automation_remove.add_argument("--yes", action="store_true")
    p_automation_remove.add_argument(
        "--no-disable-fallback",
        action="store_true",
        help="Do not disable the job when hard delete fails.",
    )

    p_automation_purge = sub_automation.add_parser("purge")
    p_automation_purge.add_argument(
        "--all",
        action="store_true",
        help="Required safety switch for bulk operation.",
    )
    p_automation_purge.add_argument("--yes", action="store_true")
    p_automation_purge.add_argument(
        "--no-disable-fallback",
        action="store_true",
        help="Do not disable jobs when hard delete fails.",
    )

    p_automation_enable = sub_automation.add_parser("enable")
    p_automation_enable.add_argument("--id", dest="job_id", required=True)

    p_automation_disable = sub_automation.add_parser("disable")
    p_automation_disable.add_argument("--id", dest="job_id", required=True)

    p_automation_runs = sub_automation.add_parser("runs")
    p_automation_runs.add_argument("--state", default="")

    p_automation_replay = sub_automation.add_parser("replay")
    p_automation_replay.add_argument("--run-id", required=True)

    p_cap = sub.add_parser("capabilities")
    p_cap.add_argument("--session", required=True)
    p_cap.add_argument("--agent", default="")

    p_skills = sub.add_parser("skills")
    sub_skills = p_skills.add_subparsers(dest="command", required=True)
    p_skills_list = sub_skills.add_parser("list")
    p_skills_list.add_argument("--session", required=True)

    p_tools = sub.add_parser("tools")
    sub_tools = p_tools.add_subparsers(dest="command", required=True)
    p_tools_list = sub_tools.add_parser("list")
    p_tools_list.add_argument("--session", required=True)
    p_tools_list.add_argument("--kind", choices=["all", "builtin", "app", "connector"], default="all")
    p_tools_list.add_argument("--agent", default="")
    p_tools_exec = sub_tools.add_parser("execute")
    p_tools_exec.add_argument("--session", required=True)
    p_tools_exec.add_argument("--tool", default="")
    p_tools_exec.add_argument("--agent", default="")
    p_tools_exec.add_argument("--arg", action="append", default=[])

    p_control = sub.add_parser("control")
    sub_control = p_control.add_subparsers(dest="command", required=True)
    p_control_actions = sub_control.add_parser("actions")
    p_control_actions.add_argument("--session", required=True)
    p_control_call = sub_control.add_parser("call")
    p_control_call.add_argument("--session", required=True)
    p_control_call.add_argument("--action", default="")
    p_control_call.add_argument("--timeout-ms", type=int, default=8000)
    p_control_call.add_argument("--arg", action="append", default=[])

    args = parser.parse_args(argv)
    try:
        client = AraRuntimeClient.from_env(cwd=os.getcwd())
    except RuntimeError as exc:
        raise SystemExit(f"ara runtime: {exc}") from None

    if args.scope == "session" and args.command == "start":
        print(json.dumps(client.session_start(), indent=2))
        return

    if args.scope == "session" and args.command == "status":
        print(json.dumps(client.session_status(), indent=2))
        return

    if args.scope == "session" and args.command == "stop":
        print(json.dumps(client.session_stop(), indent=2))
        return

    if args.scope == "session" and args.command == "exec":
        command = str(args.shell_command or "").strip()
        if not command:
            raise SystemExit("ara runtime: session exec requires --command")
        print(
            json.dumps(
                client.session_exec(
                    command=command,
                    timeout_seconds=int(args.timeout_seconds or 90),
                ),
                indent=2,
            )
        )
        return

    if args.scope == "session" and args.command == "keepalive":
        interval_seconds = max(1, min(int(args.interval_seconds or 30), 600))
        iterations = max(0, int(args.iterations or 0))
        max_failures = max(1, min(int(args.max_failures or 3), 20))
        sent = 0
        consecutive_failures = 0
        try:
            while iterations == 0 or sent < iterations:
                sent += 1
                timestamp = datetime.now(timezone.utc).isoformat()
                try:
                    heartbeat = client.session_heartbeat()
                except RuntimeError as exc:
                    consecutive_failures += 1
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "event": "heartbeat",
                                "iteration": sent,
                                "timestamp": timestamp,
                                "error": str(exc),
                                "consecutive_failures": consecutive_failures,
                            },
                            indent=2,
                        )
                    )
                else:
                    ok = bool(heartbeat.get("ok")) if isinstance(heartbeat, dict) else bool(heartbeat)
                    consecutive_failures = 0 if ok else (consecutive_failures + 1)
                    print(
                        json.dumps(
                            {
                                "ok": ok,
                                "event": "heartbeat",
                                "iteration": sent,
                                "timestamp": timestamp,
                                "response": heartbeat,
                                "consecutive_failures": consecutive_failures,
                            },
                            indent=2,
                        )
                    )
                if consecutive_failures >= max_failures:
                    raise SystemExit(
                        f"ara runtime: session keepalive exceeded max failures ({max_failures})"
                    )
                if iterations == 0 or sent < iterations:
                    time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "event": "summary",
                        "status": "interrupted",
                        "iterations": sent,
                    },
                    indent=2,
                )
            )
            return
        print(
            json.dumps(
                {
                    "ok": True,
                    "event": "summary",
                    "status": "completed",
                    "iterations": sent,
                },
                indent=2,
            )
        )
        return

    if args.scope == "automation" and args.command == "list":
        print(json.dumps(client.automation_list_jobs(), indent=2))
        return

    if args.scope == "automation" and args.command == "add":
        payload = _parse_json_object_arg(args.payload_json, flag_name="--payload-json")
        if not payload:
            raise SystemExit("ara runtime: automation add requires --payload-json")
        if args.deliver:
            payload["deliver"] = True
        schedule_kind = "every" if args.every_seconds is not None else "cron"
        print(
            json.dumps(
                client.automation_create_job(
                    name=args.name,
                    schedule_kind=schedule_kind,
                    timezone=args.timezone,
                    every_seconds=args.every_seconds,
                    schedule_expr=args.cron,
                    payload=payload,
                    execution_mode=args.execution_mode,
                    misfire_policy=args.misfire_policy,
                    max_retries=args.max_retries,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                ),
                indent=2,
            )
        )
        return

    if args.scope == "automation" and args.command == "update":
        payload: Optional[dict[str, Any]] = None
        if str(args.payload_json or "").strip():
            payload = _parse_json_object_arg(args.payload_json, flag_name="--payload-json")
        if args.deliver:
            if payload is None:
                payload = {}
            payload["deliver"] = True

        enabled: Optional[bool] = None
        if args.enable and args.disable:
            raise SystemExit("ara runtime: automation update cannot set both --enable and --disable")
        if args.enable:
            enabled = True
        elif args.disable:
            enabled = False

        print(
            json.dumps(
                client.automation_update_job(
                    job_id=args.job_id,
                    name=args.name,
                    enabled=enabled,
                    payload=payload,
                    max_retries=args.max_retries,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                ),
                indent=2,
            )
        )
        return

    if args.scope == "automation" and args.command == "remove":
        if not bool(args.yes):
            raise SystemExit("ara runtime: automation remove requires --yes")
        result = client.automation_delete_job_safe(
            job_id=args.job_id,
            disable_on_failure=not bool(args.no_disable_fallback),
        )
        print(json.dumps(result, indent=2))
        if not bool(result.get("ok")):
            raise SystemExit("ara runtime: automation remove failed")
        return

    if args.scope == "automation" and args.command == "purge":
        if not bool(args.all):
            raise SystemExit("ara runtime: automation purge requires --all")
        if not bool(args.yes):
            raise SystemExit("ara runtime: automation purge requires --yes")
        result = client.automation_purge_jobs(
            disable_on_failure=not bool(args.no_disable_fallback),
        )
        print(json.dumps(result, indent=2))
        if not bool(result.get("ok")):
            raise SystemExit("ara runtime: automation purge had failures")
        return

    if args.scope == "automation" and args.command == "enable":
        print(
            json.dumps(
                client.automation_update_job(job_id=args.job_id, enabled=True),
                indent=2,
            )
        )
        return

    if args.scope == "automation" and args.command == "disable":
        print(
            json.dumps(
                client.automation_update_job(job_id=args.job_id, enabled=False),
                indent=2,
            )
        )
        return

    if args.scope == "automation" and args.command == "runs":
        print(json.dumps(client.automation_list_runs(state=args.state), indent=2))
        return

    if args.scope == "automation" and args.command == "replay":
        print(json.dumps(client.automation_replay_run(run_id=args.run_id), indent=2))
        return

    if args.scope == "capabilities":
        print(
            json.dumps(
                client.capabilities(
                    session_id=args.session,
                    agent_id=args.agent or "",
                ),
                indent=2,
            )
        )
        return

    if args.scope == "skills" and args.command == "list":
        print(json.dumps(client.skills(session_id=args.session), indent=2))
        return

    if args.scope == "tools" and args.command == "list":
        print(
            json.dumps(
                client.tools(
                    session_id=args.session,
                    kind=args.kind or "all",
                    agent_id=args.agent or "",
                ),
                indent=2,
            )
        )
        return

    if args.scope == "tools" and args.command == "execute":
        tool_name = str(args.tool or "").strip()
        if not tool_name:
            raise SystemExit("ara runtime: tools execute requires --tool")
        print(
            json.dumps(
                client.execute_tool(
                    session_id=args.session,
                    tool_name=tool_name,
                    args=_parse_pairs(args.arg or []),
                    agent_id=args.agent or "",
                ),
                indent=2,
            )
        )
        return

    if args.scope == "control" and args.command == "actions":
        print(json.dumps(client.control_actions(session_id=args.session), indent=2))
        return

    if args.scope == "control" and args.command == "call":
        action = str(args.action or "").strip()
        if not action:
            raise SystemExit("ara runtime: control call requires --action")
        print(
            json.dumps(
                client.control_call(
                    session_id=args.session,
                    action=action,
                    args=_parse_pairs(args.arg or []),
                    timeout_ms=int(args.timeout_ms or 8000),
                ),
                indent=2,
            )
        )
        return

    parser.print_help()


def run_auth_cli(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ara auth CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login")
    p_login.add_argument("--api-base-url", default="")
    p_login.add_argument(
        "--api-key",
        default="",
        help="Store an existing ARA_API_KEY for CLI use (recommended for Google OAuth-only accounts).",
    )
    p_login.add_argument("--provider", default="google")
    p_login.add_argument("--timeout-seconds", type=int, default=180)
    p_login.add_argument("--no-browser", action="store_true")
    p_login.add_argument(
        "--auth-flow",
        choices=["poll"],
        default="poll",
        help="Login transport (polling-only).",
    )
    p_login.add_argument("--supabase-url", default="")
    p_login.add_argument("--supabase-anon-key", default="")

    p_whoami = sub.add_parser("whoami")
    p_whoami.add_argument("--api-base-url", default="")

    sub.add_parser("logout")

    p_rotate = sub.add_parser("rotate", help="Rotate your API key (invalidates the current one).")
    p_rotate.add_argument("--api-base-url", default="")

    args = parser.parse_args(argv)
    command = str(args.command or "").strip().lower()

    if command == "logout":
        _clear_cli_credentials()
        print(json.dumps({"ok": True, "status": "logged_out"}, indent=2))
        return

    api_base_url = str(getattr(args, "api_base_url", "") or "").strip() or _resolve_api_base_url(DEFAULT_API_BASE_URL)

    if command == "rotate":
        bearer = _resolve_control_plane_bearer()
        if not bearer:
            raise SystemExit("ara auth: not logged in. Run `ara auth login` or set ARA_API_KEY.")
        result = _Http(api_base_url, bearer).rotate_api_key()
        new_key = str(result.get("api_key") or "").strip()
        out: dict[str, Any] = {
            "ok": True,
            "status": "rotated",
            "key_prefix": result.get("key_prefix", ""),
            "key_identifier": result.get("key_identifier", ""),
        }
        credentials_updated = False
        if os.getenv("ARA_API_KEY", "").strip() or os.getenv("ARA_ACCESS_TOKEN", "").strip():
            out["warning"] = "Your ARA_API_KEY environment variable still holds the old key. Update it to: " + new_key
        else:
            creds = _load_cli_credentials()
            if str(creds.get("auth_type") or "").strip() == "cli_api_key":
                creds["api_key"] = new_key
                _save_cli_credentials(creds)
                credentials_updated = True
        out["credentials_updated"] = credentials_updated
        if credentials_updated:
            out["credentials_path"] = str(_cli_credentials_path())
        print(json.dumps(out, indent=2))
        return

    if command == "whoami":
        bearer = _resolve_control_plane_bearer()
        if not bearer:
            raise SystemExit("ara auth: not logged in. Run `ara auth login` or set ARA_API_KEY.")
        out = _Http(api_base_url, bearer).cli_whoami()
        if os.getenv("ARA_API_KEY", "").strip() or os.getenv("ARA_ACCESS_TOKEN", "").strip():
            source = "env"
        else:
            creds = _load_cli_credentials()
            auth_type = str(creds.get("auth_type") or "").strip()
            source = auth_type or ("cli_api_key" if str(creds.get("api_key") or "").strip() else "cli_jwt")
        out["auth_source"] = source
        print(json.dumps(out, indent=2))
        return

    # login
    provided_api_key = str(getattr(args, "api_key", "") or "").strip()
    if provided_api_key:
        _save_cli_credentials(
            {
                "auth_type": "cli_api_key",
                "api_base_url": api_base_url,
                "api_key": provided_api_key,
            }
        )
        whoami: dict[str, Any] = {}
        try:
            whoami = _Http(api_base_url, provided_api_key).cli_whoami()
        except RuntimeError:
            print(
                "Warning: could not verify API key against server; stored locally anyway.",
                file=sys.stderr,
            )
            whoami = {"ok": True, "user": {"id": "", "email": ""}}
        print(
            json.dumps(
                {
                    "ok": True,
                    "status": "logged_in",
                    "auth_type": "cli_api_key",
                    "api_base_url": api_base_url,
                    "user": whoami.get("user"),
                    "credentials_path": str(_cli_credentials_path()),
                },
                indent=2,
            )
        )
        return

    direct_supabase_url = str(args.supabase_url or "").strip()
    direct_supabase_anon = str(args.supabase_anon_key or "").strip()
    config_payload: dict[str, Any] = {}
    if direct_supabase_url and direct_supabase_anon:
        supabase_url = direct_supabase_url
        supabase_anon_key = direct_supabase_anon
    else:
        config_payload = _Http(api_base_url, "").cli_auth_config()
        supabase_url = str(config_payload.get("supabase_url") or "").strip()
        supabase_anon_key = str(config_payload.get("supabase_anon_key") or "").strip()
        if not str(getattr(args, "api_base_url", "") or "").strip():
            cfg_api_base = str(config_payload.get("api_base_url") or "").strip()
            if cfg_api_base:
                api_base_url = cfg_api_base
    if not (supabase_url and supabase_anon_key):
        raise SystemExit("ara auth: could not resolve Supabase auth config.")

    provider = str(args.provider or "google").strip().lower() or "google"
    if provider not in _CLI_OAUTH_ALLOWED_PROVIDERS:
        allowed = ", ".join(sorted(_CLI_OAUTH_ALLOWED_PROVIDERS))
        raise SystemExit(f"ara auth: unsupported OAuth provider '{provider}'. Allowed providers: {allowed}.")
    code_verifier = _pkce_code_verifier()
    code_challenge = _pkce_code_challenge(code_verifier)
    try:
        callback_payload = _collect_oauth_callback_via_polling(
            api_base_url=api_base_url,
            provider=provider,
            code_challenge=code_challenge,
            timeout_seconds=int(args.timeout_seconds or 180),
            open_browser=not bool(args.no_browser),
        )
        auth_code = str(callback_payload.get("code") or "").strip()
        redirect_uri = str(callback_payload.get("redirect_uri") or "").strip()
        if not auth_code:
            raise RuntimeError("OAuth callback did not include authorization code.")
        issued = _supabase_token_request(
            supabase_url=supabase_url,
            supabase_anon_key=supabase_anon_key,
            grant_type="pkce",
            body={
                "auth_code": auth_code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
        )
    except RuntimeError as exc:
        raise SystemExit(f"ara auth: login failed ({exc})") from None

    access_token = str(issued.get("access_token") or "").strip()
    refresh_token = str(issued.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise SystemExit("ara auth: login failed, missing access or refresh token.")
    expires_at = _coerce_supabase_expiry_iso(issued)
    user_payload = issued.get("user") if isinstance(issued.get("user"), dict) else {}
    _save_cli_credentials(
        {
            "auth_type": "supabase_jwt",
            "api_base_url": api_base_url,
            "supabase_url": supabase_url,
            "supabase_anon_key": supabase_anon_key,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "user": {
                "id": str(user_payload.get("id") or ""),
                "email": str(user_payload.get("email") or ""),
            },
        }
    )
    whoami: dict[str, Any] = {}
    try:
        whoami = _Http(api_base_url, access_token).cli_whoami()
    except RuntimeError:
        whoami = {
            "ok": True,
            "user": {
                "id": str(user_payload.get("id") or ""),
                "email": str(user_payload.get("email") or ""),
            },
        }
    print(
        json.dumps(
            {
                "ok": True,
                "status": "logged_in",
                "api_base_url": api_base_url,
                "expires_at": expires_at,
                "user": whoami.get("user"),
                "credentials_path": str(_cli_credentials_path()),
            },
            indent=2,
        )
    )


def _run_app_cli(app: _AutomationApp | dict[str, Any], argv: Optional[list[str]] = None, *, default_command: str = "deploy") -> None:
    app_obj = app if isinstance(app, _AutomationApp) else None
    manifest = app_obj.manifest if app_obj is not None else dict(app)

    parser = argparse.ArgumentParser(description="Ara Python SDK CLI")
    sub = parser.add_subparsers(dest="command")

    _deploy_parent = argparse.ArgumentParser(add_help=False)
    _deploy_parent.add_argument("--activate", default="true")
    _deploy_parent.add_argument("--key-name", default="")
    _deploy_parent.add_argument("--rpm", type=int, default=60)
    _deploy_parent.add_argument("--warm", default="false")
    _deploy_parent.add_argument("--warm-agent", default="")
    _deploy_parent.add_argument("--on-existing", choices=["update", "error"], default="update")

    sub.add_parser("deploy", parents=[_deploy_parent])
    sub.add_parser("up", parents=[_deploy_parent])

    p_run = sub.add_parser("run")
    p_run.add_argument("--runtime-key", default="")
    p_run.add_argument("--app-header-key", default="")

    p_logs = sub.add_parser("logs")
    p_logs.add_argument("--runtime-key", default="")
    p_logs.add_argument("--app-header-key", default="")

    args = parser.parse_args(argv)
    command = args.command or default_command
    if command == "up":
        command = "deploy"

    client = AraClient.from_env(manifest=manifest, cwd=os.getcwd())

    if command == "deploy":
        deploy_kwargs: dict[str, Any] = {
            "activate": str(args.activate).lower() != "false",
            "key_name": args.key_name or None,
            "key_rpm": int(args.rpm),
            "warm": str(args.warm).lower() == "true",
            "warm_agent_id": args.warm_agent or None,
            "on_existing": args.on_existing,
        }
        deploy_out = client.deploy(**deploy_kwargs)
        warmup_result = deploy_out.get("warmup") if isinstance(deploy_out, dict) else None
        warmup_run_id = (
            str(warmup_result.get("run_id") or "").strip()
            if isinstance(warmup_result, dict)
            else ""
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "slug": str(manifest.get("slug") or ""),
                    "runtime_key_created": bool(deploy_out.get("runtime_key_created")),
                    "runtime_key": str(deploy_out.get("runtime_key") or ""),
                    "warmup_run_id": warmup_run_id,
                },
                indent=2,
            )
        )
        return

    if command == "run":
        run_id = _new_run_id()
        payload = {"run_id": run_id, "idempotency_key": f"automation-{_slugify(run_id)}"}
        print(
            json.dumps(
                client.run(
                    agent_id=None,
                    input_payload=payload,
                    runtime_key=args.runtime_key or None,
                    app_header_key=args.app_header_key or None,
                ),
                indent=2,
            )
        )
        return

    if command == "logs":
        try:
            for row in client.logs(runtime_key=args.runtime_key or None, app_header_key=args.app_header_key or None):
                print(_format_runtime_log_line(row), flush=True)
        except KeyboardInterrupt:
            return
        return

    parser.print_help()


def _legacy_api_removed(name: str) -> RuntimeError:
    return RuntimeError(
        f"{name} is no longer supported in the minimal Ara SDK surface. "
        "Use ara.Automation(...), @ara.tool, ara.secret(...), and ara.env(...)."
    )


class _RemovedLegacyAPI:
    def __init__(self, name: str):
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        _ = args, kwargs
        raise _legacy_api_removed(self._name)

    def __getattr__(self, item: str) -> Any:
        _ = item
        raise _legacy_api_removed(self._name)

    def __repr__(self) -> str:
        return f"<removed legacy API {self._name}>"


def App(*args: Any, **kwargs: Any) -> Any:
    _ = args, kwargs
    raise _legacy_api_removed("App(...)")


Secret = _RemovedLegacyAPI("Secret")
fastapi_endpoint = _RemovedLegacyAPI("fastapi_endpoint(...)")
invoke = _RemovedLegacyAPI("invoke")
schedule = _RemovedLegacyAPI("schedule")
scheduler = _RemovedLegacyAPI("scheduler")
runtime = _RemovedLegacyAPI("runtime(...)")
sandbox = _RemovedLegacyAPI("sandbox(...)")
entrypoint = _RemovedLegacyAPI("entrypoint(...)")
file = _RemovedLegacyAPI("file(...)")
local_file = _RemovedLegacyAPI("local_file(...)")
AraRuntimeClient = _RemovedLegacyAPI("AraRuntimeClient")
run_runtime_cli = _RemovedLegacyAPI("run_runtime_cli")
run_connect_cli = _RemovedLegacyAPI("run_connect_cli")
run_ssh_proxy_cli = _RemovedLegacyAPI("run_ssh_proxy_cli")
