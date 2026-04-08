"""Public Ara Python SDK core (provider-agnostic)."""

from __future__ import annotations

import argparse
import inspect
import json
import os
import pathlib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

DEFAULT_SUBAGENT_MAX_CONCURRENCY = 4
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 5
DEBUG_HTTP_ERRORS_ENV = "ARA_SDK_DEBUG_HTTP_ERRORS"
DEFAULT_API_BASE_URL = "https://ara-api-prd.up.railway.app"


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
    return profile


def cron(expression: str, *, timezone: str = "UTC") -> dict[str, Any]:
    expr = str(expression or "").strip()
    if not expr:
        raise ValueError("cron() requires a non-empty expression")
    return {"type": "cron", "cron": expr, "schedule": expr, "timezone": str(timezone or "UTC")}


def sandbox(
    *,
    policy: str = "shared",
    max_concurrency: Optional[int] = None,
    idle_ttl_minutes: Optional[int] = None,
) -> dict[str, Any]:
    normalized_policy = str(policy or "shared").strip().lower()
    if normalized_policy != "shared":
        raise ValueError("Public SDK currently supports only sandbox(policy='shared').")
    out: dict[str, Any] = {"policy": "shared"}
    out["max_concurrency"] = max(1, int(max_concurrency or DEFAULT_SUBAGENT_MAX_CONCURRENCY))
    if idle_ttl_minutes is not None:
        out["idle_ttl_minutes"] = max(1, int(idle_ttl_minutes))
    return out


def subagent_hook(
    *,
    event: str,
    id: Optional[str] = None,
    task: Optional[str] = None,
    command: Optional[str] = None,
    trigger: Optional[dict[str, Any]] = None,
    schedule: Optional[dict[str, Any] | str] = None,
    channel: str = "api",
) -> dict[str, Any]:
    evt = str(event or "").strip()
    if not evt:
        raise ValueError("subagent_hook() requires event")
    if task and command:
        raise ValueError("subagent_hook() accepts either task= or command=, not both")
    hook_id = str(id or "").strip() or f"{_slugify(evt)}-hook"
    out: dict[str, Any] = {"id": hook_id, "event": evt, "channel": str(channel or "api").strip() or "api"}
    if task:
        out["task"] = str(task).strip()
    if command:
        out["command"] = str(command).strip()
    if trigger and isinstance(trigger, dict):
        out["trigger"] = dict(trigger)
    if schedule is not None:
        out["schedule"] = schedule
    return out


def _normalize_trigger(
    trigger: Optional[dict[str, Any]],
    schedule: Optional[dict[str, Any] | str],
) -> tuple[dict[str, Any], str]:
    trigger_cfg = dict(trigger) if isinstance(trigger, dict) else {}
    schedule_expr = ""
    if isinstance(schedule, dict):
        schedule_expr = str(schedule.get("cron") or schedule.get("schedule") or "").strip()
        trigger_cfg.setdefault("type", str(schedule.get("type") or "cron").strip() or "cron")
        if schedule_expr:
            trigger_cfg.setdefault("cron", schedule_expr)
            trigger_cfg.setdefault("schedule", schedule_expr)
        if schedule.get("timezone"):
            trigger_cfg.setdefault("timezone", str(schedule.get("timezone")))
    elif isinstance(schedule, str):
        schedule_expr = schedule.strip()
        if schedule_expr:
            trigger_cfg.setdefault("type", "cron")
            trigger_cfg.setdefault("cron", schedule_expr)
            trigger_cfg.setdefault("schedule", schedule_expr)
    else:
        schedule_expr = str(trigger_cfg.get("cron") or trigger_cfg.get("schedule") or "").strip()
    if not trigger_cfg:
        trigger_cfg = {"type": "api"}
    if "type" not in trigger_cfg:
        trigger_cfg["type"] = "api"
    return trigger_cfg, schedule_expr


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
        self._workflows: list[dict[str, Any]] = []
        self._profiles: list[dict[str, Any]] = []
        self._subagents: list[dict[str, Any]] = []
        self._local_entrypoint: Optional[Callable[..., Any]] = None

    def _upsert(self, rows: list[dict[str, Any]], item: dict[str, Any], *, key: str = "id") -> None:
        item_key = str(item.get(key) or "")
        if not item_key:
            return
        for idx, existing in enumerate(rows):
            if str(existing.get(key) or "") == item_key:
                rows[idx] = item
                return
        rows.append(item)

    def agent(
        self,
        id: Optional[str] = None,
        *,
        instructions: str = "",
        handoff_to: Optional[list[str]] = None,
        always_on: bool = True,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            profile_id = str(id or _slugify(fn.__name__.replace("_", "-"))).strip()
            if not profile_id:
                raise ValueError("@app.agent requires a non-empty id")
            text = str(instructions or fn.__doc__ or "").strip()
            profile = {
                "id": profile_id,
                "instructions": text,
                "persona": text,
                "handoff_to": [str(x).strip() for x in (handoff_to or []) if str(x).strip()],
                "always_on": bool(always_on),
            }
            self._upsert(self._profiles, profile)
            setattr(fn, "__ara_agent_profile__", profile)
            return fn

        return decorator

    def task(
        self,
        *,
        id: Optional[str] = None,
        agent: Optional[str] = None,
        task: Optional[str] = None,
        trigger: Optional[dict[str, Any]] = None,
        schedule: Optional[dict[str, Any] | str] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            workflow_id = str(id or _slugify(fn.__name__.replace("_", "-"))).strip()
            if not workflow_id:
                raise ValueError("@app.task requires a non-empty id")
            trigger_cfg, schedule_expr = _normalize_trigger(trigger, schedule)
            item: dict[str, Any] = {
                "id": workflow_id,
                "mode": "task",
                "task": str(task or fn.__doc__ or "").strip() or f"Execute workflow {workflow_id}",
                "trigger": trigger_cfg,
                "run": {},
                "pipeline": [],
            }
            if agent:
                item["agent_id"] = str(agent).strip()
            if schedule_expr:
                item["schedule"] = schedule_expr
            self._upsert(self._workflows, item)
            setattr(fn, "__ara_workflow__", item)
            return fn

        return decorator

    def hook(
        self,
        *,
        id: Optional[str] = None,
        event: str = "hook.tick",
        agent: Optional[str] = None,
        task: Optional[str] = None,
        command: Optional[str] = None,
        trigger: Optional[dict[str, Any]] = None,
        schedule: Optional[dict[str, Any] | str] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            workflow_id = str(id or _slugify(fn.__name__.replace("_", "-"))).strip()
            if not workflow_id:
                raise ValueError("@app.hook requires a non-empty id")
            event_name = str(event or "hook.tick").strip() or "hook.tick"
            trigger_cfg = dict(trigger or {})
            trigger_cfg.setdefault("type", "api")
            trigger_cfg.setdefault("event", event_name)
            if command:
                self._upsert(
                    self._workflows,
                    {
                        "id": workflow_id,
                        "mode": "run",
                        "task": "",
                        "run": {"command": str(command).strip()},
                        "pipeline": [],
                        "trigger": trigger_cfg,
                        "schedule": str(schedule or "").strip() if isinstance(schedule, str) else "",
                    },
                )
            else:
                self.task(
                    id=workflow_id,
                    agent=agent,
                    task=str(task or fn.__doc__ or "").strip() or f"Handle hook '{event_name}'",
                    trigger=trigger_cfg,
                    schedule=schedule,
                )(fn)
            setattr(fn, "__ara_hook__", {"id": workflow_id, "event": event_name})
            return fn

        return decorator

    def subagent(
        self,
        id: Optional[str] = None,
        *,
        workflow_id: Optional[str] = None,
        instructions: str = "",
        handoff_to: Optional[list[str]] = None,
        always_on: bool = True,
        task: Optional[str] = None,
        trigger: Optional[dict[str, Any]] = None,
        schedule: Optional[dict[str, Any] | str] = None,
        runtime: Optional[dict[str, Any]] = None,
        sandbox: Optional[dict[str, Any]] = None,
        channels: Optional[list[str]] = None,
        hooks: Optional[list[dict[str, Any]]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            profile_id = str(id or _slugify(fn.__name__.replace("_", "-"))).strip()
            wf_id = str(workflow_id or profile_id).strip()
            if not profile_id or not wf_id:
                raise ValueError("@app.subagent requires non-empty id/workflow_id")
            self.agent(
                profile_id,
                instructions=instructions,
                handoff_to=handoff_to,
                always_on=always_on,
            )(fn)
            self.task(
                id=wf_id,
                agent=profile_id,
                task=str(task or fn.__doc__ or "").strip() or f"Execute subagent {profile_id}",
                trigger=trigger,
                schedule=schedule,
            )(fn)
            sub = {
                "id": profile_id,
                "workflow_id": wf_id,
                "channels": sorted({str(c).strip().lower() for c in (channels or []) if str(c).strip()}),
                "runtime": dict(runtime or {}),
                "sandbox": dict(sandbox or {"policy": "shared", "max_concurrency": DEFAULT_SUBAGENT_MAX_CONCURRENCY}),
                "hooks": [dict(h) for h in (hooks or []) if isinstance(h, dict)],
            }
            self._upsert(self._subagents, sub)
            setattr(fn, "__ara_subagent__", sub)
            return fn

        return decorator

    def local_entrypoint(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._local_entrypoint = fn
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
        if self._profiles:
            agent["profiles"] = list(self._profiles)
            agent.setdefault("default_profile_id", str(self._profiles[0].get("id") or "default"))
        if self._subagents:
            agent["subagents"] = list(self._subagents)
        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "agent": agent,
            "workflows": list(self._workflows),
            "interfaces": dict(self._interfaces),
            "runtime_profile": dict(self._runtime_profile),
        }


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


def _require_env(*keys: str) -> dict[str, str]:
    out: dict[str, str] = {}
    missing: list[str] = []
    for key in keys:
        value = os.getenv(key, "").strip()
        if not value:
            missing.append(key)
        else:
            out[key] = value
    if missing:
        raise RuntimeError(
            "Missing required env vars: " + ", ".join(missing) + ". "
            "Create .env or export variables before running this command."
        )
    return out


class _Http:
    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

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
        req_headers = {
            "Content-Type": "application/json",
            "Authorization": auth_header or f"Bearer {self.access_token}",
        }
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

    def run_app(self, app_id: str, *, runtime_key: str, workflow_id: Optional[str], input_payload: dict[str, Any], warmup: bool = False):
        return self._request(
            f"/v1/apps/{app_id}/run",
            method="POST",
            body={"workflow_id": workflow_id, "warmup": bool(warmup), "input": input_payload},
            auth_header=f"Bearer {runtime_key}",
        )

    def send_event(
        self,
        app_id: str,
        *,
        runtime_key: str,
        workflow_id: Optional[str],
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
        return self._request(
            f"/v1/apps/{app_id}/events",
            method="POST",
            headers=headers,
            body={
                "workflow_id": workflow_id,
                "event_type": event_type,
                "channel": channel,
                "source": source,
                "message": message,
                "payload": payload,
                "metadata": metadata,
            },
            auth_header=f"Bearer {runtime_key}",
        )

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

    def __init__(self, *, manifest: dict[str, Any], api_base_url: str, access_token: str, cwd: pathlib.Path):
        self.manifest = dict(manifest)
        self.cwd = cwd
        self.http = _Http(api_base_url, access_token)

    @classmethod
    def from_env(cls, *, manifest: dict[str, Any], cwd: Optional[str] = None) -> "AraClient":
        base = pathlib.Path(cwd or os.getcwd())
        _read_dotenv(base / ".env")
        if not os.getenv("ARA_API_BASE_URL", "").strip():
            os.environ["ARA_API_BASE_URL"] = DEFAULT_API_BASE_URL
        env = _require_env("ARA_ACCESS_TOKEN")
        return cls(
            manifest=manifest,
            api_base_url=os.getenv("ARA_API_BASE_URL", DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL,
            access_token=env["ARA_ACCESS_TOKEN"],
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
        path = self.cwd / ".runtime-key.local"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def deploy(
        self,
        *,
        activate: bool = True,
        key_name: Optional[str] = None,
        key_rpm: int = 60,
        warm: bool = False,
        warm_workflow_id: Optional[str] = None,
        on_existing: Optional[str] = None,
    ) -> dict[str, Any]:
        if on_existing not in (None, "update", "error"):
            raise ValueError("on_existing must be one of: update, error")

        existing = self._find_app_by_slug()
        app_id = str(existing.get("id")) if existing else ""
        if app_id and on_existing == "error":
            raise RuntimeError(
                f"Project '{self.manifest.get('slug')}' already exists for this account (app_id={app_id})."
            )

        payload = {
            "name": self.manifest.get("name"),
            "description": self.manifest.get("description") or "",
            "agent": self.manifest.get("agent") or {},
            "workflows": self.manifest.get("workflows") or [],
            "interfaces": self.manifest.get("interfaces") or {},
            "runtime_profile": self.manifest.get("runtime_profile") or {},
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

        key_out = self.http.create_key(
            app_id,
            name=(key_name or f"{self.manifest.get('slug')}-py-local"),
            requests_per_minute=int(key_rpm),
        )
        runtime_key = str(key_out.get("key") or "").strip()
        if not runtime_key:
            raise RuntimeError("deploy failed: runtime key missing")
        key_path = self.cwd / ".runtime-key.local"
        key_path.write_text(runtime_key + "\n", encoding="utf-8")

        warmup = None
        if warm:
            warmup = self.http.run_app(
                app_id,
                runtime_key=runtime_key,
                workflow_id=warm_workflow_id,
                input_payload={},
                warmup=True,
            )

        return {
            "app_id": app_id,
            "slug": self.manifest.get("slug"),
            "runtime_key_written": True,
            "runtime_key_path": str(key_path),
            "warmup": warmup,
        }

    def run(self, *, workflow_id: Optional[str], input_payload: Optional[dict[str, Any]] = None, runtime_key: Optional[str] = None):
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        key = self._resolve_runtime_key(runtime_key)
        if not key:
            raise RuntimeError("Missing runtime key. Set ARA_RUNTIME_KEY or run deploy first.")
        return self.http.run_app(str(app["id"]), runtime_key=key, workflow_id=workflow_id, input_payload=input_payload or {})

    def events(
        self,
        *,
        workflow_id: Optional[str],
        event_type: str,
        channel: str,
        source: str,
        message: str,
        payload: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        runtime_key: Optional[str] = None,
    ) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        key = self._resolve_runtime_key(runtime_key)
        if not key:
            raise RuntimeError("Missing runtime key. Set ARA_RUNTIME_KEY or run deploy first.")
        return self.http.send_event(
            str(app["id"]),
            runtime_key=key,
            workflow_id=workflow_id,
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

    def invite(self, *, email: str, role: str = "viewer", expires_in_hours: int = 24 * 7) -> dict[str, Any]:
        app = self._find_app_by_slug()
        if not app:
            raise RuntimeError(f"App '{self.manifest.get('slug')}' not found. Deploy first.")
        return self.http.invite(str(app["id"]), email=email, role=role, expires_in_hours=expires_in_hours)


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


def run_cli(app: App | dict[str, Any], argv: Optional[list[str]] = None, *, default_command: str = "deploy") -> None:
    app_obj = app if isinstance(app, App) else None
    manifest = app_obj.manifest if app_obj is not None else dict(app)

    parser = argparse.ArgumentParser(description="Ara Python SDK CLI")
    sub = parser.add_subparsers(dest="command")

    p_deploy = sub.add_parser("deploy")
    p_deploy.add_argument("--activate", default="true")
    p_deploy.add_argument("--key-name", default="")
    p_deploy.add_argument("--rpm", type=int, default=60)
    p_deploy.add_argument("--warm", default="false")
    p_deploy.add_argument("--warm-workflow", default="")
    p_deploy.add_argument("--on-existing", choices=["update", "error"])

    p_run = sub.add_parser("run")
    p_run.add_argument("--workflow", default="")
    p_run.add_argument("--message", default="")
    p_run.add_argument("--input", action="append", default=[])

    p_events = sub.add_parser("events")
    p_events.add_argument("--workflow", default="")
    p_events.add_argument("--event-type", default="webhook.message.received")
    p_events.add_argument("--channel", default="webhook")
    p_events.add_argument("--source", default="webhook")
    p_events.add_argument("--message", default="")
    p_events.add_argument("--input", action="append", default=[])
    p_events.add_argument("--metadata", action="append", default=[])
    p_events.add_argument("--idempotency-key", default="")

    p_invite = sub.add_parser("invite")
    p_invite.add_argument("--email", default="")
    p_invite.add_argument("--role", default="viewer")
    p_invite.add_argument("--expires-hours", type=int, default=24 * 7)

    p_local = sub.add_parser("local")
    p_local.add_argument("--input", action="append", default=[])

    sub.add_parser("setup")

    args = parser.parse_args(argv)
    command = args.command or default_command
    client = AraClient.from_env(manifest=manifest, cwd=os.getcwd())

    if command == "deploy":
        deploy_kwargs: dict[str, Any] = {
            "activate": str(args.activate).lower() != "false",
            "key_name": args.key_name or None,
            "key_rpm": int(args.rpm),
            "warm": str(args.warm).lower() == "true",
            "warm_workflow_id": args.warm_workflow or None,
        }
        if args.on_existing:
            deploy_kwargs["on_existing"] = args.on_existing
        print(json.dumps(client.deploy(**deploy_kwargs), indent=2))
        return

    if command == "run":
        payload = _parse_pairs(args.input)
        if args.message:
            payload["message"] = args.message
        run_id = str(payload.get("run_id") or "").strip() or _new_run_id()
        payload.setdefault("run_id", run_id)
        payload.setdefault("idempotency_key", f"{_slugify(args.workflow or 'default')}-{_slugify(run_id)}")
        print(json.dumps(client.run(workflow_id=args.workflow or None, input_payload=payload), indent=2))
        return

    if command == "events":
        payload = _parse_pairs(args.input)
        metadata = _parse_pairs(args.metadata)
        idem = str(args.idempotency_key or "").strip() or f"{_slugify(args.event_type)}-{_slugify(_new_run_id())}"
        print(
            json.dumps(
                client.events(
                    workflow_id=args.workflow or None,
                    event_type=args.event_type,
                    channel=args.channel,
                    source=args.source,
                    message=args.message,
                    payload=payload,
                    metadata=metadata,
                    idempotency_key=idem,
                ),
                indent=2,
            )
        )
        return

    if command == "invite":
        email = str(args.email or "").strip()
        if not email:
            raise RuntimeError("invite requires --email")
        print(json.dumps(client.invite(email=email, role=args.role, expires_in_hours=args.expires_hours), indent=2))
        return

    if command == "local":
        if app_obj is None:
            raise RuntimeError("local command requires an App(...) instance")
        print(json.dumps({"ok": True, "result": app_obj.call_local_entrypoint(_parse_pairs(args.input))}, indent=2))
        return

    if command == "setup":
        print(json.dumps(client.setup(), indent=2))
        return

    parser.print_help()
