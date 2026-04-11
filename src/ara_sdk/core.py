"""Public Ara Python SDK core (provider-agnostic)."""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import os
import pathlib
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, NoReturn, Optional
from uuid import uuid4

DEFAULT_SUBAGENT_MAX_CONCURRENCY = 8
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 5
DEBUG_HTTP_ERRORS_ENV = "ARA_SDK_DEBUG_HTTP_ERRORS"
DEFAULT_API_BASE_URL = "https://api.ara.so"
ALLOWED_SANDBOX_POLICIES = frozenset({"shared", "dedicated", "ephemeral", "inherited"})
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
RESERVED_ENV_KEYS = frozenset({"SESSION_ID", "USER_ID", "APP_ID"})
RESERVED_ENV_PREFIXES = ("ARA_", "MODAL_")


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


def _env_flag_enabled(key: str) -> bool:
    return str(os.getenv(key, "")).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_secret_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if not normalized or not SECRET_NAME_RE.match(normalized):
        raise ValueError("Secret name must match [a-z0-9][a-z0-9_-]{0,62}[a-z0-9]")
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
    try:
        raw_source = inspect.getsource(fn)
    except (OSError, TypeError):
        raise ValueError(f"{context} requires source-visible functions (no lambdas/dynamic defs)") from None
    source = _strip_leading_decorators(raw_source)
    if not source.startswith("def "):
        raise ValueError(f"{context} only supports standard def functions")
    return source


def _validate_prompt_factory_signature(fn: Callable[..., Any]) -> None:
    signature = inspect.signature(fn)
    params = [
        p
        for p in signature.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    if len(params) != 1:
        raise ValueError("@app.agent(prompt_factory=True) requires exactly one input parameter")
    return_annotation = signature.return_annotation
    if isinstance(return_annotation, str):
        normalized = return_annotation.strip().strip("'\"").lower()
        if normalized in {"str", "builtins.str"}:
            return
    if return_annotation not in (inspect._empty, str):
        raise ValueError("@app.agent(prompt_factory=True) return annotation must be str (or omitted)")


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
        name_or_env_dict: str | dict[str, Any],
        env_dict: Optional[dict[str, Any]] = None,
        *,
        required_keys: Optional[list[str]] = None,
        name: Optional[str] = None,
    ) -> "SecretDefinition":
        if isinstance(name_or_env_dict, dict):
            if env_dict is not None:
                raise ValueError("from_dict(dict, ...) does not accept a second env_dict argument")
            values = {str(k): "" if v is None else str(v) for k, v in name_or_env_dict.items()}
            if not values:
                raise ValueError("from_dict requires a non-empty env_dict")
            resolved_name = _normalize_secret_name(name) if name is not None else _generated_secret_name("dict", values)
            return cls(
                resolved_name,
                values=values,
                required_keys=required_keys,
                source="dict",
            )
        if not isinstance(name_or_env_dict, str) or not isinstance(env_dict, dict) or not env_dict:
            raise ValueError("from_dict requires either (name, env_dict) or (env_dict)")
        if name is not None:
            positional_name = _normalize_secret_name(name_or_env_dict)
            keyword_name = _normalize_secret_name(name)
            if positional_name != keyword_name:
                raise ValueError("from_dict positional name conflicts with name= keyword")
        return cls(
            name_or_env_dict,
            values={str(k): "" if v is None else str(v) for k, v in env_dict.items()},
            required_keys=required_keys,
            source="dict",
        )

    @classmethod
    def from_dotenv(
        cls,
        name: Optional[str] = None,
        filename: str = ".env",
        *,
        required_keys: Optional[list[str]] = None,
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
        resolved_name = _normalize_secret_name(name) if name is not None else _generated_secret_name("dotenv", values)
        return cls(resolved_name, values=values, required_keys=required_keys, source="dotenv")

    @classmethod
    def from_local_environ(
        cls,
        name: str,
        env_keys: list[str],
        *,
        required_keys: Optional[list[str]] = None,
    ) -> "SecretDefinition":
        if not isinstance(env_keys, list) or not env_keys:
            raise ValueError("from_local_environ requires a non-empty env_keys list")
        values: dict[str, str] = {}
        missing: list[str] = []
        for raw_key in env_keys:
            key = _validate_env_key(raw_key)
            value = os.getenv(key)
            if value is None:
                missing.append(key)
                continue
            values[key] = str(value)
        if missing:
            raise ValueError(f"Missing environment variables for secret {name}: {', '.join(missing)}")
        return cls(name, values=values, required_keys=required_keys, source="local_environ")

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
        name_or_env_dict: str | dict[str, Any],
        env_dict: Optional[dict[str, Any]] = None,
        *,
        required_keys: Optional[list[str]] = None,
        name: Optional[str] = None,
    ) -> SecretDefinition:
        return SecretDefinition.from_dict(
            name_or_env_dict,
            env_dict,
            required_keys=required_keys,
            name=name,
        )

    @staticmethod
    def from_dotenv(
        name: Optional[str] = None,
        filename: str = ".env",
        *,
        required_keys: Optional[list[str]] = None,
    ) -> SecretDefinition:
        return SecretDefinition.from_dotenv(name, filename=filename, required_keys=required_keys)

    @staticmethod
    def from_local_environ(
        name: str,
        env_keys: list[str],
        *,
        required_keys: Optional[list[str]] = None,
    ) -> SecretDefinition:
        return SecretDefinition.from_local_environ(name, env_keys=env_keys, required_keys=required_keys)


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
    seen_names: set[str] = set()
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
        if definition.name in seen_names:
            continue
        seen_names.add(definition.name)
        refs.append(definition.ref())
        definitions.append(definition)
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
        return {
            "id": schedule_id,
            "kind": "cron",
            "cron": expr,
            "timezone": timezone_name,
            "run": run,
        }
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


class App:
    """Public app declaration object."""

    def __init__(
        self,
        name: str,
        *,
        slug: Optional[str] = None,
        project_name: Optional[str] = None,
        description: str = "",
        interfaces: Optional[dict[str, Any]] = None,
        runtime_profile: Optional[dict[str, Any]] = None,
        agent: Optional[dict[str, Any]] = None,
    ):
        self.name = str(name or "").strip()
        self.project_name = str(project_name or "").strip()
        source = self.project_name or slug or self.name
        self.slug = _slugify(source)
        if not self.name:
            raise ValueError("App(name=...) requires a non-empty name")
        if not self.slug:
            raise ValueError("App(...) could not derive a slug")
        self.description = str(description or "").strip()
        self._agent = dict(agent or {})
        self._interfaces = dict(interfaces or {})
        self._runtime_profile = dict(runtime_profile or {})
        self._agents: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._default_agent_id: str = ""
        self._local_entrypoint: Optional[Callable[..., Any]] = None

    def _upsert_agent(self, item: dict[str, Any]) -> None:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            return
        for idx, existing in enumerate(self._agents):
            if str(existing.get("id") or "").strip() == item_id:
                self._agents[idx] = item
                return
        self._agents.append(item)

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

    def agent(
        self,
        id: Optional[str] = None,
        *,
        task: str = "",
        instructions: str = "",
        prompt_factory: bool = False,
        entrypoint: bool = False,
        skills: Optional[list[str]] = None,
        schedules: Optional[list[dict[str, Any]]] = None,
        handoff_to: Optional[list[str]] = None,
        runtime: Optional[dict[str, Any]] = None,
        sandbox: Optional[dict[str, Any]] = None,
        always_on: bool = True,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            agent_id = str(id or _slugify(fn.__name__.replace("_", "-"))).strip()
            if not agent_id:
                raise ValueError("@app.agent requires a non-empty id")
            task_text = str(task or instructions or fn.__doc__ or "").strip() or f"Run agent {agent_id}"
            instructions_text = str(instructions or task_text).strip()
            prompt_factory_spec: Optional[dict[str, Any]] = None
            if prompt_factory:
                _validate_prompt_factory_signature(fn)
                source = _extract_callable_source(fn, context="@app.agent(prompt_factory=True)")
                params_schema = _callable_parameters_schema(fn)
                prompt_factory_spec = {
                    "function_name": fn.__name__,
                    "source": source,
                    "parameters": params_schema,
                }
                if not str(task or "").strip() and not str(instructions or "").strip():
                    task_text = f"Generate instructions at runtime via prompt factory '{fn.__name__}'."
                    instructions_text = task_text
            normalized_schedules: list[dict[str, Any]] = []
            for schedule_item in schedules or []:
                normalized_schedules.append(_normalize_schedule_spec(schedule_item))
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
            if prompt_factory_spec is not None:
                agent_row["prompt_factory"] = prompt_factory_spec
            if skills is not None:
                agent_row["skills"] = _normalize_string_items(skills)
            if isinstance(runtime, dict) and runtime:
                runtime_cfg = dict(runtime)
                runtime_cfg.pop("__secret_definitions", None)
                agent_row["runtime"] = runtime_cfg
            if isinstance(sandbox, dict) and sandbox:
                agent_row["sandbox"] = dict(sandbox)
            self._upsert_agent(agent_row)
            if entrypoint or not self._default_agent_id:
                self._default_agent_id = agent_id
            setattr(fn, "__ara_agent__", agent_row)
            return fn

        return decorator

    def local_entrypoint(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._local_entrypoint = fn
            return fn

        return decorator

    def tool(
        self,
        *,
        id: Optional[str] = None,
        description: str = "",
        parameters: Optional[dict[str, Any]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_id = str(id or _slugify(fn.__name__.replace("_", "-"))).strip()
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
            tool_description = str(description or fn.__doc__ or "").strip()
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
            replaced = False
            for idx, existing in enumerate(self._tools):
                existing_fn = existing.get("function") if isinstance(existing.get("function"), dict) else {}
                existing_name = str(existing_fn.get("name") or "").strip()
                if existing_name == tool_id:
                    self._tools[idx] = item
                    replaced = True
                    break
            if not replaced:
                self._tools.append(item)
            setattr(fn, "__ara_tool__", item)
            return fn

        return decorator

    def call_local_entrypoint(self, input_payload: dict[str, str]) -> Any:
        if self._local_entrypoint is None:
            raise RuntimeError("No @app.local_entrypoint() registered")
        fn = self._local_entrypoint
        params = list(inspect.signature(fn).parameters.values())
        if not params:
            return fn()
        if len(params) == 1:
            return fn(input_payload)
        kwargs = {p.name: input_payload[p.name] for p in params if p.name in input_payload}
        return fn(**kwargs)

    @property
    def manifest(self) -> dict[str, Any]:
        agent = dict(self._agent)
        workflows: list[dict[str, Any]] = []

        if self._agents:
            agent_rows = [dict(row) for row in self._agents]
            agent["agents"] = agent_rows
            default_agent_id = str(self._default_agent_id or self._agents[0].get("id") or "").strip()
            if default_agent_id:
                agent["default_agent_id"] = default_agent_id

            profiles: list[dict[str, Any]] = []
            subagents: list[dict[str, Any]] = []
            for row in agent_rows:
                agent_id = str(row.get("id") or "").strip()
                if not agent_id:
                    continue
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
                        "channels": [],
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
                    run_agent_id = str(schedule_run.get("agent_id") or agent_id).strip() if schedule_run_type == "agent" else agent_id
                    schedule_run_input = schedule_run.get("input")
                    schedule_message = ""
                    if isinstance(schedule_run_input, dict):
                        schedule_message = str(schedule_run_input.get("message") or "").strip()
                    elif isinstance(schedule_run_input, str):
                        schedule_message = schedule_run_input.strip()
                    workflow_id = f"{agent_id}--{normalized_schedule['id']}"
                    trigger = {
                        "type": "cron",
                        "cron": str(normalized_schedule.get("cron") or "").strip(),
                        "schedule": str(normalized_schedule.get("cron") or "").strip(),
                        "timezone": str(normalized_schedule.get("timezone") or "UTC").strip() or "UTC",
                    }
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

        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "agent": agent,
            "workflows": workflows,
            "interfaces": dict(self._interfaces),
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
    def cron(*, id: str, expr: str, timezone: str = "UTC", run: dict[str, Any]) -> dict[str, Any]:
        spec = {
            "id": str(id or "").strip(),
            "kind": "cron",
            "cron": str(expr or "").strip(),
            "timezone": str(timezone or "UTC").strip() or "UTC",
            "run": dict(run or {}),
        }
        return _normalize_schedule_spec(spec)

    @staticmethod
    def every(*, id: str, seconds: int, run: dict[str, Any]) -> dict[str, Any]:
        spec = {
            "id": str(id or "").strip(),
            "kind": "every",
            "every_seconds": int(seconds),
            "run": dict(run or {}),
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


invoke = _Invoke()
schedule = _Schedule()
scheduler = _Scheduler()


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
            with urllib.request.urlopen(req, timeout=30) as response:
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
        agent_id: Optional[str],
        input_payload: dict[str, Any],
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
        return self._request(
            f"/v1/apps/{app_id}/run",
            method="POST",
            headers=headers,
            body={"agent_id": agent_id, "workflow_id": agent_id, "warmup": bool(warmup), "input": input_payload},
            auth_header=auth_header,
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
        if not os.getenv("ARA_API_BASE_URL", "").strip():
            os.environ["ARA_API_BASE_URL"] = DEFAULT_API_BASE_URL
        # Prefer long-lived SDK API key; keep legacy access token as fallback.
        api_key = os.getenv("ARA_API_KEY", "").strip() or os.getenv("ARA_ACCESS_TOKEN", "").strip()
        if not api_key:
            raise RuntimeError(
                "Missing required env var: ARA_API_KEY. "
                "Legacy ARA_ACCESS_TOKEN is still accepted as a fallback."
            )
        return cls(
            manifest=manifest,
            api_base_url=os.getenv("ARA_API_BASE_URL", DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL,
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
        return ""

    def _resolve_app_header_key(self, explicit: Optional[str] = None) -> str:
        if explicit:
            return explicit
        env_key = os.getenv("ARA_APP_HEADER_KEY", "").strip()
        if env_key:
            return env_key
        return ""

    def _extract_secret_sync_plan(self, runtime_profile: dict[str, Any]) -> list[SecretDefinition]:
        return _collect_runtime_secret_definitions(runtime_profile)

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
        reconcile_runtime_secrets = "secret_refs" in runtime_profile
        secret_definitions = self._extract_secret_sync_plan(runtime_profile)
        runtime_profile.pop("__secret_definitions", None)

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
            created = self.http.create_app({**payload, "slug": self.manifest.get("slug")})
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
        agent_id: Optional[str],
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
        if not os.getenv("ARA_API_BASE_URL", "").strip():
            os.environ["ARA_API_BASE_URL"] = DEFAULT_API_BASE_URL
        api_key = os.getenv("ARA_API_KEY", "").strip() or os.getenv("ARA_ACCESS_TOKEN", "").strip()
        if not api_key:
            raise RuntimeError(
                "Missing required env var: ARA_API_KEY. "
                "Legacy ARA_ACCESS_TOKEN is still accepted as a fallback."
            )
        return cls(
            api_base_url=os.getenv("ARA_API_BASE_URL", DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL,
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


def run_cli(app: App | dict[str, Any], argv: Optional[list[str]] = None, *, default_command: str = "deploy") -> None:
    app_obj = app if isinstance(app, App) else None
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
    p_run.add_argument("--agent", default="")
    p_run.add_argument("--message", default="")
    p_run.add_argument("--input", action="append", default=[])
    p_run.add_argument("--input-json", default="")
    p_run.add_argument("--runtime-key", default="")
    p_run.add_argument("--app-header-key", default="")

    p_events = sub.add_parser("events")
    p_events.add_argument("--agent", default="")
    p_events.add_argument("--event-type", default="webhook.message.received")
    p_events.add_argument("--channel", default="webhook")
    p_events.add_argument("--source", default="webhook")
    p_events.add_argument("--message", default="")
    p_events.add_argument("--input", action="append", default=[])
    p_events.add_argument("--metadata", action="append", default=[])
    p_events.add_argument("--idempotency-key", default="")
    p_events.add_argument("--runtime-key", default="")
    p_events.add_argument("--app-header-key", default="")

    p_run_async = sub.add_parser("run-async")
    p_run_async.add_argument("--agent", default="")
    p_run_async.add_argument("--message", default="")
    p_run_async.add_argument("--input", action="append", default=[])
    p_run_async.add_argument("--input-json", default="")
    p_run_async.add_argument("--response-mode", choices=["poll", "webhook"], default="poll")
    p_run_async.add_argument("--callback-url", default="")
    p_run_async.add_argument("--callback-secret", default="")
    p_run_async.add_argument("--callback-event", action="append", default=[])
    p_run_async.add_argument("--run-id", default="")
    p_run_async.add_argument("--idempotency-key", default="")
    p_run_async.add_argument("--runtime-key", default="")
    p_run_async.add_argument("--app-header-key", default="")

    p_run_status = sub.add_parser("run-status")
    p_run_status.add_argument("--run-id", default="")
    p_run_status.add_argument("--runtime-key", default="")
    p_run_status.add_argument("--app-header-key", default="")

    p_logs = sub.add_parser("logs")
    p_logs.add_argument("--runtime-key", default="")
    p_logs.add_argument("--app-header-key", default="")

    p_invite = sub.add_parser("invite")
    p_invite.add_argument("--email", default="")
    p_invite.add_argument("--role", default="viewer")
    p_invite.add_argument("--expires-hours", type=int, default=24 * 7)

    p_local = sub.add_parser("local")
    p_local.add_argument("--input", action="append", default=[])

    sub.add_parser("setup")
    p_setup_auth = sub.add_parser("setup-auth")
    p_setup_auth.add_argument("--x-key-name", default="")
    p_setup_auth.add_argument("--x-key-rpm", type=int, default=30)
    p_setup_auth.add_argument("--ensure-runtime-key", default="true")

    args = parser.parse_args(argv)
    command = args.command or default_command
    if command == "up":
        command = "deploy"

    if command == "local":
        _read_dotenv(pathlib.Path(os.getcwd()) / ".env")
        if app_obj is None:
            raise RuntimeError("local command requires an App(...) instance")
        print(json.dumps({"ok": True, "result": app_obj.call_local_entrypoint(_parse_pairs(args.input))}, indent=2))
        return

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
        print(
            json.dumps(
                {
                    "ok": True,
                    "slug": str(manifest.get("slug") or ""),
                    "runtime_key_created": bool(deploy_out.get("runtime_key_created")),
                    "runtime_key": str(deploy_out.get("runtime_key") or ""),
                    "next": {
                        "setup_auth_command": "ara setup-auth app.py",
                    },
                },
                indent=2,
            )
        )
        return

    if command == "run":
        payload = _parse_json_object_arg(args.input_json, flag_name="--input-json") if str(args.input_json).strip() else _parse_pairs(args.input)
        if args.message:
            payload["message"] = args.message
        run_id = str(payload.get("run_id") or "").strip() or _new_run_id()
        payload.setdefault("run_id", run_id)
        payload.setdefault("idempotency_key", f"{_slugify(args.agent or 'default-agent')}-{_slugify(run_id)}")
        print(
            json.dumps(
                client.run(
                    agent_id=args.agent or None,
                    input_payload=payload,
                    runtime_key=args.runtime_key or None,
                    app_header_key=args.app_header_key or None,
                ),
                indent=2,
            )
        )
        return

    if command == "events":
        payload = _parse_pairs(args.input)
        metadata = _parse_pairs(args.metadata)
        idem = str(args.idempotency_key or "").strip() or f"{_slugify(args.event_type)}-{_slugify(_new_run_id())}"
        print(
            json.dumps(
                client.events(
                    agent_id=args.agent or None,
                    event_type=args.event_type,
                    channel=args.channel,
                    source=args.source,
                    message=args.message,
                    payload=payload,
                    metadata=metadata,
                    idempotency_key=idem,
                    runtime_key=args.runtime_key or None,
                    app_header_key=args.app_header_key or None,
                ),
                indent=2,
            )
        )
        return

    if command == "run-async":
        payload = _parse_json_object_arg(args.input_json, flag_name="--input-json") if str(args.input_json).strip() else _parse_pairs(args.input)
        if args.message:
            payload["message"] = args.message
        run_id = str(args.run_id or "").strip() or _new_run_id()
        idem = str(args.idempotency_key or "").strip() or f"run-{_slugify(run_id)}"
        callback = None
        if args.response_mode == "webhook":
            if not str(args.callback_url or "").strip():
                raise RuntimeError("run-async with --response-mode webhook requires --callback-url")
            callback = {
                "url": args.callback_url,
                "secret": args.callback_secret or "",
                "events": args.callback_event or ["run.completed", "run.failed"],
            }
        print(
            json.dumps(
                client.run_async(
                    agent_id=args.agent or None,
                    input_payload=payload,
                    response_mode=args.response_mode,
                    callback=callback,
                    run_id=run_id,
                    idempotency_key=idem,
                    runtime_key=args.runtime_key or None,
                    app_header_key=args.app_header_key or None,
                ),
                indent=2,
            )
        )
        return

    if command == "run-status":
        rid = str(args.run_id or "").strip()
        if not rid:
            raise RuntimeError("run-status requires --run-id")
        print(
            json.dumps(
                client.run_status(
                    run_id=rid,
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

    if command == "invite":
        email = str(args.email or "").strip()
        if not email:
            raise RuntimeError("invite requires --email")
        print(json.dumps(client.invite(email=email, role=args.role, expires_in_hours=args.expires_hours), indent=2))
        return

    if command == "setup":
        print(json.dumps(client.setup(), indent=2))
        return

    if command == "setup-auth":
        print(
            json.dumps(
                client.setup_auth(
                    x_key_name=args.x_key_name or None,
                    x_key_rpm=int(args.x_key_rpm),
                    ensure_runtime_key=str(args.ensure_runtime_key).lower() != "false",
                ),
                indent=2,
            )
        )
        return

    parser.print_help()
