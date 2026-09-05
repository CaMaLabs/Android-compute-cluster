#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from fnmatch import fnmatch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


HOST = "127.0.0.1"
PORT = 8766
TOKEN = os.environ.get("SWARM_AGENT_TOKEN", "")
ADMIN_TOKEN = os.environ.get("SWARM_ADMIN_TOKEN", "")
OLLAMA_URL = os.environ.get("SWARM_OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("SWARM_OLLAMA_MODEL", "qwen2.5:7b")
OPENAI_BASE_URL = os.environ.get("SWARM_OPENAI_BASE_URL", "")
OPENAI_API_KEY = os.environ.get("SWARM_OPENAI_API_KEY", "")
CONTROLLER_URL = os.environ.get("SWARM_CONTROLLER_URL", "http://127.0.0.1:8765")
MAX_TOOL_CALLS = int(os.environ.get("SWARM_AGENT_MAX_TOOL_CALLS", "24"))
COMPOSIO_TOOLKITS = [
    item.strip()
    for item in os.environ.get(
        "SWARM_COMPOSIO_TOOLKITS",
        "gmail,github,google_calendar,supabase,google_drive,google_docs,firecrawl,elevenlabs,google_chrome,facebook,openai",
    ).split(",")
    if item.strip()
]
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


SYSTEM = """You are a Codex-like root operator for an Ubuntu Compute Swarm controller.
You may use tools to inspect and change this host and to interact with the swarm controller.

When a tool is needed, respond with exactly one JSON object and no markdown:
{"tool":"shell","args":{"cmd":"systemctl status ollama --no-pager","cwd":"/root"}}
{"tool":"fs_list_dir","args":{"path":"/home/ubuntu/work/openmc/sweep","limit":200}}
{"tool":"fs_find","args":{"root":"/home/ubuntu/work/openmc/sweep","query":"native_feedback_summary.json","limit":50}}
{"tool":"fs_read_file","args":{"path":"/home/ubuntu/work/openmc/sweep/README.md","max_bytes":20000}}
{"tool":"swarm_get","args":{"path":"/health"}}
{"tool":"swarm_post","args":{"path":"/experiments","body":{...}}}
{"tool":"swarm_delete","args":{"path":"/jobs/ID"}}
{"tool":"swarm_test_job","args":{}}
{"tool":"swarm_create_job","args":{"kind":"prime_count","units":[{"start":2,"end":1000}],"requirements":{"capabilities":["cpu"]},"metadata":{"purpose":"custom job"},"poll":true,"cleanup":false}}
{"tool":"swarm_poll_job","args":{"job_id":"ID","timeout_seconds":120}}
{"tool":"github","args":{"cmd":"api /user"}}
{"tool":"gmail","args":{"slug":"GMAIL_FETCH_EMAILS","data":{"max_results":5}}}
{"tool":"google_drive","args":{"slug":"GOOGLEDRIVE_SEARCH_FILES","data":{"query":"name contains 'report'"}}}
{"tool":"elevenlabs","args":{"slug":"ELEVENLABS_TEXT_TO_SPEECH","data":{"text":"Hello from the swarm agent"}}}
{"tool":"composio_search","args":{"query":"create calendar event","toolkits":["google_calendar"]}}
{"tool":"composio_list_tools","args":{"toolkit":"elevenlabs","query":"speech","limit":20}}
{"tool":"composio_execute","args":{"slug":"TOOL_SLUG","data":{...}}}
{"tool":"composio_dev","args":{"cmd":"auth-configs list"}}
{"tool":"composio","args":{"cmd":"search \"send email\" --toolkits gmail --limit 5"}}

When finished, respond with exactly one JSON object:
{"final":"concise answer for the user"}

Use shell carefully. Prefer read-only inspection before changes. You are authenticated and running with root permissions.
Be concise by default: final answers should normally be 1-5 lines with the outcome, exact path/URL/ID, and pass/fail status. Do not paste long logs unless the user asks.
Do not use markdown code fences in final answers; the dashboard displays plain text.
If git reports dubious ownership for a requested repository, add that repository to root's safe.directory and retry.
Use fs_list_dir, fs_find, and fs_read_file for filesystem navigation, search, and file reading instead of guessing shell paths.
If a requested script or file is not found at the current path, use fs_find for the basename or meaningful suffix before saying it does not exist.
Do not claim a requested command is impossible until you have inspected the filesystem for the referenced file.
Use github for GitHub API operations and shell/git for local repository operations.
Composio plugin layer:
- The user has made external tools available through Composio. Treat Composio as the general plugin layer, not just Gmail/Drive.
- Known Composio toolkits on this machine include: gmail, github, google_calendar, supabase, google_drive, google_docs, firecrawl, elevenlabs, google_chrome, facebook, and openai.
- For any external service request, first choose the likely toolkit, then use composio_search or composio_list_tools to discover the exact slug when unsure.
- Use composio_execute only with an exact slug and JSON data that matches the requested operation.
- If the user is referring to Auth Configs, configured connectors, playground users, or tools they made available in the Composio dashboard, use composio_dev with auth-configs/connected-accounts/toolkits/playground-execute commands.
- If Composio says a toolkit is not connected, tell the user the exact command to run, e.g. /home/ubuntu/.local/bin/composio link elevenlabs.
- Voice chat: use ElevenLabs/elevenlabs for text-to-speech voice replies. Use OpenAI/openai through Composio for speech-to-text or audio transcription when an audio file is provided and a matching tool is available. If a dashboard microphone feature is requested, explain whether the current web UI has an upload/record endpoint before claiming live voice chat is active.

Use gmail, google_drive, elevenlabs, composio_search, composio_list_tools, and composio_execute for provider operations.

Agent operating rules:
- Start by identifying the user's concrete objective, current working directory, and missing facts.
- Prefer deterministic tools over shell when a specific tool exists.
- For filesystem work, list before reading when the location is ambiguous, and find before declaring a file missing.
- For multi-step work, gather evidence, make one focused change, then verify with the narrowest command/API call that proves the behavior.
- If a tool call fails, read the error and retry with a corrected call once. Do not repeat the same failed call.
- When using a third-party toolkit through Composio, discover the exact slug with composio search or tools list before execution unless the user supplied a known slug.
- Keep final answers concise and include exact paths, command outputs, or IDs that matter.

Compute Swarm API guide:
- Health: swarm_get /health.
- Full status: swarm_get /status. This returns workers, jobs, artifact counts, and online flags.
- Prefer swarm_create_job over raw swarm_post /jobs for user-requested custom jobs. It validates the job shape, submits it, and can poll for completion.
- Create a general job: swarm_create_job with {"kind":"task_name","units":[{...payload...}],"requirements":{"capabilities":["cpu"]},"priority":0,"metadata":{},"poll":true,"cleanup":false}.
- Built-in worker tasks include prime_count with payload {"start":2,"end":100000}, monte_carlo_pi with payload {"start":0,"end":1000000}, and text_artifact with payload {"text":"hello","name":"output.txt"}.
- Poll a job: swarm_poll_job with {"job_id":"..."} or swarm_get /jobs/JOB_ID until units show status done or failed.
- Delete one job: swarm_delete /jobs/JOB_ID. Clear all jobs: swarm_delete /jobs.
- Remove a worker/device: swarm_delete /workers/WORKER_ID. Leased work from that worker is returned to queued.
- For parameter sweeps, use swarm_post /experiments with {"name":"...","task":"prime_count","parameters":{"start":{"values":[2,200002]},"end":{"values":[1000000]}},"objective":{"path":"count","direction":"maximize"},"requirements":{"capabilities":["cpu"]}}.
- Use swarm_test_job for a quick end-to-end worker lease/result/cleanup smoke test.
"""


def http_json(url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 600) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_chat(model: str, messages: list[dict[str, str]], timeout: int = 600) -> str:
    if OPENAI_BASE_URL:
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"} if OPENAI_API_KEY else {}
        body = http_json(
            OPENAI_BASE_URL.rstrip("/") + "/chat/completions",
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.1,
            },
            headers=headers,
            timeout=timeout,
        )
        choices = body.get("choices") or []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        content = str(message.get("content") or "").strip()
        if content:
            return content
        reasoning = str(message.get("reasoning_content") or message.get("thinking") or "").strip()
        if reasoning:
            return json.dumps({"final": "The routed model returned only reasoning and no user-visible answer. Try the request again or use a deterministic tool command."})
        return ""
    body = http_json(
        OLLAMA_URL + "/api/chat",
        {"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.1}},
        timeout=timeout,
    )
    message = body.get("message", {})
    content = str(message.get("content") or "").strip()
    if content:
        return content
    thinking = str(message.get("thinking") or "").strip()
    if thinking:
        return json.dumps({"final": "The model returned only hidden reasoning and no user-visible answer. Try the request again or use a deterministic tool command."})
    return ""


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {"final": text}


def first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


PRUNE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".cache",
    ".venv",
    "venv",
    "build",
    "dist",
}


def resolve_path(path: str, cwd: str) -> str:
    path = os.path.expanduser(str(path or "."))
    if not os.path.isabs(path):
        path = os.path.join(cwd if os.path.isdir(cwd) else "/root", path)
    return os.path.abspath(path)


def candidate_names(name: str) -> list[str]:
    names = [name]
    if "_" in name:
        parts = name.split("_")
        for index in range(1, len(parts) - 1):
            suffix = "_".join(parts[index:])
            if suffix not in names:
                names.append(suffix)
    return names


def nearest_existing_parent(path: str, cwd: str) -> str:
    path = resolve_path(path, cwd)
    probe = path if os.path.isdir(path) else os.path.dirname(path)
    while probe and probe != "/" and not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    return probe or cwd or "/"


def fs_find_data(root: str, query: str, cwd: str, *, limit: int = 50, max_depth: int = 12) -> dict[str, Any]:
    root_path = resolve_path(root or cwd or ".", cwd)
    query = str(query or "").strip()
    if not os.path.isdir(root_path):
        root_path = nearest_existing_parent(root_path, cwd)
    results: list[dict[str, Any]] = []
    lowered = query.lower()
    patterns = [query] if any(ch in query for ch in "*?[]") else []
    if query and not patterns:
        patterns = [query, f"*{query}*"]
        for name in candidate_names(query):
            if name not in patterns:
                patterns.append(name)
            wildcard = f"*{name}*"
            if wildcard not in patterns:
                patterns.append(wildcard)

    for current, dirs, files in os.walk(root_path):
        rel = os.path.relpath(current, root_path)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if d not in PRUNE_DIRS and not d.startswith(".cache")]
        entries = [(name, True) for name in dirs] + [(name, False) for name in files]
        for name, is_dir in entries:
            full = os.path.join(current, name)
            rel_full = os.path.relpath(full, root_path)
            haystack = f"{name}\n{rel_full}".lower()
            matched = not query or lowered in haystack or any(fnmatch(name, pat) or fnmatch(rel_full, pat) for pat in patterns)
            if matched:
                try:
                    stat = os.stat(full)
                    size = stat.st_size
                    mtime = stat.st_mtime
                except OSError:
                    size = None
                    mtime = None
                results.append({"path": full, "relative": rel_full, "type": "dir" if is_dir else "file", "size": size, "mtime": mtime})
                if len(results) >= limit:
                    return {"root": root_path, "query": query, "truncated": True, "results": results}
    return {"root": root_path, "query": query, "truncated": False, "results": results}


def resolve_file_with_search(path: str, cwd: str) -> tuple[str | None, dict[str, Any] | None]:
    exact = resolve_path(path, cwd)
    if os.path.isfile(exact):
        return exact, None
    name = os.path.basename(exact)
    root = nearest_existing_parent(exact, cwd)
    result = fs_find_data(root, name, cwd, limit=20, max_depth=12)
    files = [item for item in result["results"] if item["type"] == "file"]
    if files:
        return str(files[0]["path"]), result
    parent = os.path.dirname(root)
    if parent and parent != root:
        result = fs_find_data(parent, name, cwd, limit=20, max_depth=12)
        files = [item for item in result["results"] if item["type"] == "file"]
        if files:
            return str(files[0]["path"]), result
    return None, result


def fs_list_dir(path: str, cwd: str, *, limit: int = 200) -> dict[str, Any]:
    target = resolve_path(path or ".", cwd)
    if not os.path.isdir(target):
        return {"ok": False, "path": target, "error": "not a directory", "nearest_existing_parent": nearest_existing_parent(target, cwd)}
    entries: list[dict[str, Any]] = []
    with os.scandir(target) as iterator:
        for entry in iterator:
            try:
                stat = entry.stat(follow_symlinks=False)
                item = {
                    "name": entry.name,
                    "path": os.path.join(target, entry.name),
                    "type": "dir" if entry.is_dir(follow_symlinks=False) else "file",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            except OSError as exc:
                item = {"name": entry.name, "path": os.path.join(target, entry.name), "error": str(exc)}
            entries.append(item)
    entries.sort(key=lambda item: (item.get("type") != "dir", str(item.get("name", "")).lower()))
    return {"ok": True, "path": target, "count": len(entries), "truncated": len(entries) > limit, "entries": entries[:limit]}


def fs_read_file(path: str, cwd: str, *, max_bytes: int = 120000) -> dict[str, Any]:
    requested = resolve_path(path, cwd)
    resolved, search = resolve_file_with_search(requested, cwd)
    if not resolved:
        return {"ok": False, "requested": requested, "error": "file not found", "search": search}
    max_bytes = max(1, min(int(max_bytes), 2_000_000))
    with open(resolved, "rb") as handle:
        data = handle.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    try:
        content = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = data.decode("utf-8", errors="replace")
        encoding = "utf-8-replacement"
    return {
        "ok": True,
        "requested": requested,
        "path": resolved,
        "search_used": search is not None,
        "search": search if search is not None else None,
        "bytes_read": len(data),
        "truncated": truncated,
        "encoding": encoding,
        "content": content,
    }


def json_text(value: Any, limit: int = 12000) -> str:
    return json.dumps(value, indent=2, sort_keys=True)[:limit]


def shell(cmd: str, cwd: str) -> str:
    cwd = cwd if cwd and os.path.isdir(cwd) else "/root"
    run_as_ubuntu = cwd.startswith("/home/ubuntu") or bool(re.search(r"\bcd\s+/home/ubuntu(?:/|\s|$)", cmd))
    runner = ["/usr/bin/timeout", "180"]
    if run_as_ubuntu:
        runner += ["/usr/bin/sudo", "-H", "-u", "ubuntu"]
    runner += ["/bin/bash", "-lc", cmd]
    result = subprocess.run(
        runner,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=190,
    )
    out = (result.stdout or "") + (result.stderr or "")
    missing = re.search(r"bash: ([A-Za-z0-9_./-]+\.sh): No such file or directory", out)
    if result.returncode == 127 and missing:
        requested = os.path.basename(missing.group(1))
        finder = subprocess.run(
            ["/usr/bin/find", ".", "-name", requested, "-type", "f", "-print", "-quit"],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=30,
        )
        found = finder.stdout.strip()
        if found:
            retried_cmd = cmd.replace(f"bash {missing.group(1)}", f"bash {found}", 1)
            retry_runner = ["/usr/bin/timeout", "300"]
            if run_as_ubuntu:
                retry_runner += ["/usr/bin/sudo", "-H", "-u", "ubuntu"]
            retry_runner += ["/bin/bash", "-lc", retried_cmd]
            retry = subprocess.run(
                retry_runner,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=310,
            )
            retry_out = (retry.stdout or "") + (retry.stderr or "")
            return (
                f"exit_code={result.returncode}\n{out[-4000:]}\n"
                f"[agent retry] found {requested} at {found}; retrying command\n"
                f"exit_code={retry.returncode}\n{retry_out[-12000:]}"
            )
    cat_missing = re.search(r"cat:\s+([^:\n]+): No such file or directory", out)
    if result.returncode == 1 and cat_missing:
        requested_path = cat_missing.group(1).strip()
        requested_name = os.path.basename(requested_path)
        search_root = cwd
        if requested_path.startswith("/"):
            search_root = os.path.dirname(requested_path)
            while search_root and search_root != "/" and not os.path.isdir(search_root):
                search_root = os.path.dirname(search_root)
        finder = subprocess.run(
            ["/usr/bin/find", search_root or cwd, "-name", requested_name, "-type", "f", "-print", "-quit"],
            text=True,
            capture_output=True,
            timeout=60,
        )
        found = finder.stdout.strip()
        if not found and "_" in requested_name:
            parts = requested_name.split("_")
            for index in range(1, len(parts) - 1):
                suffix_name = "_".join(parts[index:])
                finder = subprocess.run(
                    ["/usr/bin/find", search_root or cwd, "-name", suffix_name, "-type", "f", "-print", "-quit"],
                    text=True,
                    capture_output=True,
                    timeout=60,
                )
                found = finder.stdout.strip()
                if found:
                    break
        if found:
            retried_cmd = cmd.replace(requested_path, found, 1)
            retry_runner = ["/usr/bin/timeout", "300"]
            if run_as_ubuntu:
                retry_runner += ["/usr/bin/sudo", "-H", "-u", "ubuntu"]
            retry_runner += ["/bin/bash", "-lc", retried_cmd]
            retry = subprocess.run(
                retry_runner,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=310,
            )
            retry_out = (retry.stdout or "") + (retry.stderr or "")
            return (
                f"exit_code={result.returncode}\n{out[-4000:]}\n"
                f"[agent retry] found {requested_name} at {found}; retrying command\n"
                f"exit_code={retry.returncode}\n{retry_out[-12000:]}"
            )
    return f"exit_code={result.returncode}\n{out[-12000:]}"


def run_ubuntu_command(args: list[str], *, timeout: int = 120) -> str:
    result = subprocess.run(
        ["/usr/bin/sudo", "-H", "-u", "ubuntu", *args],
        cwd="/home/ubuntu",
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return f"exit_code={result.returncode}\n{out[-12000:]}"


TOOLKIT_ALIASES = {
    "googlecalendar": "google_calendar",
    "google-calendar": "google_calendar",
    "calendar": "google_calendar",
    "googledrive": "google_drive",
    "google-drive": "google_drive",
    "drive": "google_drive",
    "googlechrome": "google_chrome",
    "google-chrome": "google_chrome",
    "chrome": "google_chrome",
    "eleven_labs": "elevenlabs",
    "eleven-labs": "elevenlabs",
}


def normalize_toolkit(toolkit: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", toolkit or "").lower()
    return TOOLKIT_ALIASES.get(safe, safe)


def composio_auth_state() -> dict[str, Any]:
    path = "/home/ubuntu/.composio/user_data.json"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return {"ok": False, "reason": f"cannot read {path}: {exc}"}
    api_key = data.get("api_key")
    return {
        "ok": bool(api_key),
        "reason": "logged in" if api_key else "Composio CLI is not logged in for user ubuntu",
        "base_url": data.get("base_url"),
        "org_id": data.get("org_id"),
        "test_user_id": data.get("test_user_id"),
    }


def composio_unavailable(command: str) -> str:
    state = composio_auth_state()
    if not state.get("ok"):
        return (
            "exit_code=1\n"
            f"{state.get('reason')}.\n"
            "Run: sudo -H -u ubuntu /home/ubuntu/.local/bin/composio login\n"
            "Then link a toolkit if needed, for example: sudo -H -u ubuntu /home/ubuntu/.local/bin/composio link gmail\n"
            f"Skipped command: composio {command}"
        )
    return ""


def composio_login_start() -> str:
    return run_ubuntu_command(
        ["/home/ubuntu/.local/bin/composio", "login", "--no-browser", "--no-wait", "--no-skill-install"],
        timeout=60,
    )


def composio_login_poll() -> str:
    return run_ubuntu_command(["/home/ubuntu/.local/bin/composio", "login", "--poll", "--no-skill-install"], timeout=660)


def composio_link_start(toolkit: str) -> str:
    toolkit = normalize_toolkit(toolkit)
    return run_ubuntu_command(
        ["/home/ubuntu/.local/bin/composio", "link", toolkit, "--no-browser", "--no-wait"],
        timeout=60,
    )


def composio_connected_accounts(toolkit: str) -> dict[str, Any]:
    toolkit = normalize_toolkit(toolkit)
    output = run_ubuntu_command(["/home/ubuntu/.local/bin/composio", "link", toolkit, "--list"], timeout=60)
    raw = output.split("\n", 1)[1] if output.startswith("exit_code=") and "\n" in output else output
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {"ok": True, "toolkit": toolkit, "raw": output, "data": data, "count": int(data.get("total") or len(data.get("items") or []))}
    except Exception:
        pass
    return {"ok": False, "toolkit": toolkit, "raw": output, "count": 0}


def summarize_composio_login_start(output: str) -> str:
    match = re.search(r"https://dashboard\.composio\.dev/\?cliKey=[A-Za-z0-9-]+", output)
    if match:
        return f"Open this Composio login URL, then reply: done\n{match.group(0)}"
    tail = " ".join(line.strip() for line in output.splitlines()[-4:] if line.strip())
    return f"Could not start Composio login. {tail[:500]}"


def summarize_composio_login_poll(output: str) -> str:
    state = composio_auth_state()
    if state.get("ok"):
        gmail = composio_connected_accounts("gmail")
        if gmail.get("count", 0) > 0:
            return "Composio login complete. Gmail is linked."
        return "Composio login complete. Gmail still needs linking; say: link Gmail."
    tail = " ".join(line.strip() for line in output.splitlines()[-4:] if line.strip())
    return f"Composio login not complete yet. Open the login URL first, then reply: done. {tail[:300]}"


def summarize_done_state() -> str:
    state = composio_auth_state()
    if not state.get("ok"):
        return summarize_composio_login_start(composio_login_start())
    gmail = composio_connected_accounts("gmail")
    if gmail.get("count", 0) > 0:
        return "Gmail is linked. Ask me to check yesterday's email."
    return "Gmail is not linked yet. Say: link Gmail."


def summarize_composio_link_start(toolkit: str, output: str) -> str:
    url_match = re.search(r"https://connect\.composio\.dev/link/[A-Za-z0-9_-]+", output)
    if url_match:
        return f"Open this {toolkit} link URL, then reply: done\n{url_match.group(0)}"
    if "already" in output.lower() or '"items": [' in output:
        accounts = composio_connected_accounts(toolkit)
        if accounts.get("count", 0) > 0:
            return f"{toolkit} is already linked."
    tail = " ".join(line.strip() for line in output.splitlines()[-5:] if line.strip())
    return f"Could not start {toolkit} link. {tail[:500]}"


def gmail_date_query_for_yesterday() -> str:
    # Use the server's local calendar day for natural language like "yesterday".
    yesterday = time.strftime("%Y/%m/%d", time.localtime(time.time() - 86400))
    today = time.strftime("%Y/%m/%d", time.localtime(time.time()))
    return f"after:{yesterday} before:{today}"


def parse_composio_json_output(output: str) -> dict[str, Any] | None:
    raw = output.split("\n", 1)[1] if output.startswith("exit_code=") and "\n" in output else output
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def header_value(message: dict[str, Any], name: str) -> str:
    for header in (message.get("payload") or {}).get("headers") or []:
        if str(header.get("name", "")).lower() == name.lower():
            return str(header.get("value") or "")
    return ""


def load_composio_stored_json(data: dict[str, Any]) -> dict[str, Any] | None:
    path = data.get("outputFilePath") or data.get("filePath")
    if not isinstance(path, str) or not path.startswith("/tmp/composio/"):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def summarize_gmail_messages_from_data(data: dict[str, Any], query: str) -> str:
    if data.get("storedInFile"):
        loaded = load_composio_stored_json(data)
        if loaded:
            data = loaded
    if data.get("successful") is False:
        return "Gmail fetch failed. " + str(data.get("error") or "unknown error")[:500]
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not messages:
        return f"No Gmail messages found for {query}."
    lines = [f"Fetched {len(messages)} Gmail message(s) for {query}:"]
    for message in messages[:8]:
        if not isinstance(message, dict):
            continue
        subject = header_value(message, "Subject") or str(message.get("subject") or "(no subject)")
        sender = header_value(message, "From") or str(message.get("from") or "(unknown sender)")
        timestamp = str(message.get("messageTimestamp") or message.get("internalDate") or "")
        text = re.sub(r"\s+", " ", str(message.get("messageText") or message.get("snippet") or "")).strip()
        preview = (text[:180] + "...") if len(text) > 180 else text
        lines.append(f"- {timestamp} | {sender} | {subject}" + (f" | {preview}" if preview else ""))
    if len(messages) > 8:
        lines.append(f"- {len(messages) - 8} more not shown.")
    return "\n".join(lines)[:3000]


def summarize_gmail_execute_output(output: str, query: str = "") -> str:
    data = parse_composio_json_output(output)
    if not data:
        return "Gmail fetch returned non-JSON output. I did not summarize it."
    return summarize_gmail_messages_from_data(data, query or "the requested query")


def gmail_fetch_yesterday_summary() -> str:
    accounts = composio_connected_accounts("gmail")
    if accounts.get("count", 0) <= 0:
        return summarize_composio_link_start("gmail", composio_link_start("gmail"))
    query = gmail_date_query_for_yesterday()
    output = composio_execute(
        "GMAIL_FETCH_EMAILS",
        {
            "user_id": "me",
            "query": query,
            "max_results": 20,
            "ids_only": False,
            "include_payload": False,
            "include_spam_trash": False,
        },
    )
    if not output.startswith("exit_code=0"):
        return "Gmail fetch failed. " + " ".join(output.splitlines()[-4:])[:500]
    return summarize_gmail_execute_output(output, query)


def recent_email_context(incoming: list[Any]) -> bool:
    text = "\n".join(str(item.get("content", "")) for item in incoming[-6:] if isinstance(item, dict))
    return bool(re.search(r"\b(email|gmail|inbox)\b", text, re.I))


def summarize_repo_setup(output: str, target: str) -> str:
    exit_match = re.search(r"exit_code=(\d+)", output)
    exit_code = int(exit_match.group(1)) if exit_match else 1
    short_hash = ""
    for line in output.splitlines():
        if re.fullmatch(r"[0-9a-f]{7,12}", line.strip()):
            short_hash = line.strip()
    if "TOTAL:" in output and "FAIL" in output:
        summary = [line.strip() for line in output.splitlines() if "TOTAL:" in line][-1:]
    elif re.search(r"\b\d+ passed,\s+0 failed\b", output, re.I):
        summary = [re.findall(r"\b\d+ passed,\s+0 failed\b", output, re.I)[-1]]
    else:
        summary = []
    if exit_code == 0:
        test_status = summary[0] if summary else "setup command completed"
        return f"Done. Repo is at {target}. HEAD {short_hash or 'unknown'}. Tests: {test_status}."
    tail = "\n".join(output.splitlines()[-8:])
    return f"Repo setup failed at {target}. exit_code={exit_code}\n{tail}"


def plugin_status() -> dict[str, Any]:
    def check(args: list[str], timeout: int = 20) -> str:
        try:
            return run_ubuntu_command(args, timeout=timeout)
        except Exception as exc:
            return f"error: {exc}"

    auth_state = composio_auth_state()
    status = {
        "github": check(["/usr/bin/gh", "auth", "status"]),
        "composio_auth": auth_state,
        "configured_toolkits": COMPOSIO_TOOLKITS,
    }
    if auth_state.get("ok"):
        status["composio"] = check(["/home/ubuntu/.local/bin/composio", "whoami"])
        status["composio_auth_configs"] = check(["/home/ubuntu/.local/bin/composio", "dev", "auth-configs", "list"], timeout=60)
        status["composio_connected_accounts"] = check(["/home/ubuntu/.local/bin/composio", "dev", "connected-accounts", "list"], timeout=60)
    return status


def github(cmd: str) -> str:
    if not cmd.strip():
        cmd = "auth status"
    return run_ubuntu_command(["/bin/bash", "-lc", "gh " + cmd], timeout=180)


def composio(cmd: str) -> str:
    if not cmd.strip():
        cmd = "whoami"
    unavailable = composio_unavailable(cmd)
    if unavailable:
        return unavailable
    return run_ubuntu_command(["/bin/bash", "-lc", "/home/ubuntu/.local/bin/composio " + cmd], timeout=300)


def composio_dev(cmd: str) -> str:
    if not cmd.strip():
        cmd = "auth-configs list"
    unavailable = composio_unavailable("dev " + cmd)
    if unavailable:
        return unavailable
    return run_ubuntu_command(["/bin/bash", "-lc", "/home/ubuntu/.local/bin/composio dev " + cmd], timeout=300)


def composio_execute(slug: str, data: dict[str, Any] | None = None) -> str:
    unavailable = composio_unavailable("execute " + (slug or ""))
    if unavailable:
        return unavailable
    payload = json.dumps(data or {})
    return run_ubuntu_command(
        ["/home/ubuntu/.local/bin/composio", "execute", slug, "-d", payload],
        timeout=300,
    )


def composio_search(query: str, toolkits: list[str] | None = None, *, limit: int = 10) -> str:
    cmd = "search " + shlex.quote(query or "tools")
    if toolkits:
        cmd += " --toolkits " + shlex.quote(",".join(normalize_toolkit(item) for item in toolkits))
    cmd += " --limit " + shlex.quote(str(max(1, min(int(limit), 100))))
    output = composio(cmd)
    if output.strip() == "exit_code=0":
        return output + f"\nComposio returned no visible output for: composio {cmd}"
    return output


def composio_list_tools(toolkit: str, query: str = "", *, limit: int = 20) -> str:
    safe_toolkit = normalize_toolkit(toolkit)
    if not safe_toolkit:
        return "toolkit is required"
    cmd = "tools list " + shlex.quote(safe_toolkit)
    if query:
        cmd += " --query " + shlex.quote(query)
    cmd += " --limit " + shlex.quote(str(max(1, min(int(limit), 1000))))
    output = composio(cmd)
    if output.strip() == "exit_code=0":
        return output + f"\nComposio returned no visible output for: composio {cmd}"
    return output


def composio_toolkit_execute(toolkit: str, slug: str, data: dict[str, Any] | None = None) -> str:
    toolkit = normalize_toolkit(toolkit)
    if not slug.strip():
        return composio(f"search {shlex.quote(toolkit)} --toolkits {shlex.quote(toolkit)} --limit 10")
    return composio_execute(slug, data)


def elevenlabs(slug: str, data: dict[str, Any] | None = None) -> str:
    return composio_toolkit_execute("elevenlabs", slug, data)


def github_repo_url(text: str) -> str | None:
    match = re.search(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", text)
    if not match:
        return None
    url = match.group(0).rstrip(".,)")
    return url if url.endswith(".git") else url + ".git"


def setup_github_repo(url: str, base_dir: str = "/home/ubuntu/work", run_tests: bool = True) -> str:
    repo_name = os.path.basename(url.removesuffix(".git")) or "repo"
    target = os.path.join(base_dir, repo_name)
    q_url = shlex.quote(url)
    q_base = shlex.quote(base_dir)
    q_target = shlex.quote(target)
    test_cmd = (
        "if [ -f package.json ] && command -v npm >/dev/null 2>&1; then "
        "npm test; "
        "elif [ -f package.json ]; then "
        "echo 'package.json found but npm is not installed'; exit 127; "
        "else echo 'No package.json test step found'; fi"
    )
    cmd = f"""
set -e
mkdir -p {q_base}
if [ -d {q_target}/.git ]; then
  cd {q_target}
  remote="$(git config --get remote.origin.url || true)"
  if [ "$remote" != "{url}" ] && [ "$remote" != "{url.removesuffix('.git')}" ]; then
    echo "Existing directory has different origin: $remote"
    exit 2
  fi
  if [ -n "$(git status --porcelain)" ]; then
    echo "Repository exists with local changes; not pulling over them."
    git status --short
  else
    git pull --ff-only
  fi
else
  cd {q_base}
  git clone {q_url}
  cd {q_target}
fi
echo "repo_path={target}"
git rev-parse --short HEAD
{test_cmd if run_tests else "true"}
"""
    return run_ubuntu_command(["/bin/bash", "-lc", cmd], timeout=600)


def likely_requires_tool(text: str) -> bool:
    if github_repo_url(text) and re.search(r"\b(download|clone|set\s*up|setup|install|get|pull)\b", text, re.I):
        return False
    return bool(re.search(
        r"\b(email|gmail|inbox|calendar|supabase|drive|docs|firecrawl|eleven\s*labs|elevenlabs|openai|facebook|chrome|composio|auth config|toolkit|web search|browse)\b",
        text,
        re.I,
    ))


def composio_toolkit_for_text(text: str) -> str | None:
    lowered = text.lower()
    if any(word in lowered for word in ["email", "gmail", "inbox"]):
        return "gmail"
    if "calendar" in lowered:
        return "google_calendar"
    if "github" in lowered:
        return "github"
    if "supabase" in lowered:
        return "supabase"
    if "drive" in lowered:
        return "google_drive"
    if "docs" in lowered or "google doc" in lowered:
        return "google_docs"
    if "firecrawl" in lowered or "scrape" in lowered or "crawl" in lowered:
        return "firecrawl"
    if "elevenlabs" in lowered or "eleven labs" in lowered or "text to speech" in lowered or "tts" in lowered:
        return "elevenlabs"
    if "openai" in lowered or "transcrib" in lowered or "speech to text" in lowered:
        return "openai"
    if "facebook" in lowered:
        return "facebook"
    if "chrome" in lowered or "browser" in lowered:
        return "google_chrome"
    return None


def swarm_get(path: str) -> str:
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    return json.dumps(http_json(CONTROLLER_URL + path, headers=headers, timeout=60), indent=2)[:12000]


def swarm_post(path: str, body: dict[str, Any]) -> str:
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    return json.dumps(http_json(CONTROLLER_URL + path, body, headers=headers, timeout=120), indent=2)[:12000]


def swarm_delete(path: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    req = urllib.request.Request(
        CONTROLLER_URL + path,
        method="DELETE",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def swarm_delete_text(path: str) -> str:
    return json.dumps(swarm_delete(path), indent=2)[:12000]


def swarm_status_data() -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    return http_json(CONTROLLER_URL + "/status", headers=headers, timeout=60)


def advertised_task_names(status_data: dict[str, Any] | None = None) -> set[str]:
    status_data = status_data or swarm_status_data()
    tasks: set[str] = set()
    for worker in status_data.get("workers") or []:
        for cap in worker.get("capabilities") or []:
            if isinstance(cap, str) and cap.startswith("task:"):
                tasks.add(cap[5:])
    return tasks


def validate_job_payload(kind: str, units: Any, requirements: Any) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(kind, str) or not kind.strip():
        errors.append("kind must be a non-empty string")
        kind = ""
    if not isinstance(units, list) or not units:
        errors.append("units must be a non-empty list of objects")
        units = []
    clean_units: list[dict[str, Any]] = []
    for index, unit in enumerate(units[:10000]):
        if not isinstance(unit, dict):
            errors.append(f"unit {index} must be an object")
            continue
        clean_units.append(unit)
    if len(units) > 10000:
        errors.append("units cannot exceed 10000")

    req = requirements if isinstance(requirements, dict) else {}
    caps = req.get("capabilities")
    if caps is None:
        caps = ["cpu"]
    if not isinstance(caps, list) or not all(isinstance(item, str) for item in caps):
        errors.append("requirements.capabilities must be a list of strings")
        caps = ["cpu"]
    clean_req = dict(req)
    clean_req["capabilities"] = caps

    for index, unit in enumerate(clean_units):
        if kind == "prime_count":
            if "start" not in unit or "end" not in unit:
                errors.append(f"prime_count unit {index} requires start and end")
            else:
                try:
                    start, end = int(unit["start"]), int(unit["end"])
                    if end <= start:
                        errors.append(f"prime_count unit {index} requires end > start")
                    unit["start"], unit["end"] = start, end
                except (TypeError, ValueError):
                    errors.append(f"prime_count unit {index} start/end must be integers")
        elif kind == "monte_carlo_pi":
            if "start" not in unit or "end" not in unit:
                errors.append(f"monte_carlo_pi unit {index} requires start and end")
            else:
                try:
                    start, end = int(unit["start"]), int(unit["end"])
                    if end <= start:
                        errors.append(f"monte_carlo_pi unit {index} requires end > start")
                    unit["start"], unit["end"] = start, end
                except (TypeError, ValueError):
                    errors.append(f"monte_carlo_pi unit {index} start/end must be integers")
        elif kind == "text_artifact":
            if "text" not in unit:
                errors.append(f"text_artifact unit {index} requires text")
            unit["name"] = os.path.basename(str(unit.get("name", "output.txt"))) or "output.txt"
    return clean_units, clean_req, errors


def poll_swarm_job(job_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    deadline = time.time() + max(1, min(timeout_seconds, 1800))
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = http_json(CONTROLLER_URL + f"/jobs/{job_id}", headers=headers, timeout=60)
        units = last.get("units") or []
        if units and all(unit.get("status") in {"done", "failed"} for unit in units):
            break
        time.sleep(2)
    return last


def create_custom_swarm_job(args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("kind") or "")
    units, requirements, errors = validate_job_payload(kind, args.get("units"), args.get("requirements"))
    status_data = swarm_status_data()
    available_tasks = sorted(advertised_task_names(status_data))
    if kind and kind not in available_tasks:
        errors.append(f"no online/registered worker advertises task:{kind}; available tasks: {available_tasks}")
    if errors:
        return {"ok": False, "errors": errors, "available_tasks": available_tasks}

    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    body = {
        "kind": kind,
        "units": units,
        "requirements": requirements,
        "priority": int(args.get("priority") or 0),
        "metadata": args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
    }
    created = http_json(CONTROLLER_URL + "/jobs", body, headers=headers, timeout=60)
    result: dict[str, Any] = {"ok": True, "created": created, "submitted": body, "available_tasks": available_tasks}
    if bool(args.get("poll", False)):
        job_id = str(created.get("job_id"))
        result["job"] = poll_swarm_job(job_id, int(args.get("timeout_seconds") or 120))
    if bool(args.get("cleanup", False)) and created.get("job_id"):
        result["cleanup"] = swarm_delete(f"/jobs/{created['job_id']}")
    return result


def run_swarm_test_job() -> dict[str, Any]:
    marker = f"swarm-test-{uuid.uuid4()}"
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    created = http_json(
        CONTROLLER_URL + "/jobs",
        {
            "kind": "text_artifact",
            "units": [{"text": marker, "name": "swarm-test.txt"}],
            "requirements": {"capabilities": ["cpu"]},
            "metadata": {"temporary": True, "purpose": "agent swarm smoke test", "marker": marker},
        },
        headers=headers,
        timeout=60,
    )
    job_id = str(created["job_id"])
    last = None
    deadline = time.time() + 120
    while time.time() < deadline:
        last = http_json(CONTROLLER_URL + f"/jobs/{job_id}", headers=headers, timeout=60)
        units = last.get("units") or []
        if units and units[0].get("status") in {"done", "failed"}:
            break
        time.sleep(2)
    cleanup = swarm_delete(f"/jobs/{job_id}")
    unit = (last.get("units") or [{}])[0] if isinstance(last, dict) else {}
    return {
        "created": created,
        "job_id": job_id,
        "marker": marker,
        "unit_status": unit.get("status"),
        "worker_id": unit.get("worker_id"),
        "elapsed_ms": unit.get("elapsed_ms"),
        "result": unit.get("result"),
        "cleanup": cleanup,
    }


def run_prime_count_job(start: int, end: int) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    created = http_json(
        CONTROLLER_URL + "/jobs",
        {
            "kind": "prime_count",
            "units": [{"start": start, "end": end}],
            "requirements": {"capabilities": ["cpu"]},
            "metadata": {"temporary": True, "purpose": "agent prime_count job"},
        },
        headers=headers,
        timeout=60,
    )
    job_id = str(created["job_id"])
    last = None
    deadline = time.time() + 120
    while time.time() < deadline:
        last = http_json(CONTROLLER_URL + f"/jobs/{job_id}", headers=headers, timeout=60)
        units = last.get("units") or []
        if units and units[0].get("status") in {"done", "failed"}:
            break
        time.sleep(2)
    cleanup = swarm_delete(f"/jobs/{job_id}")
    unit = (last.get("units") or [{}])[0] if isinstance(last, dict) else {}
    return {
        "created": created,
        "job_id": job_id,
        "range": {"start": start, "end": end},
        "unit_status": unit.get("status"),
        "worker_id": unit.get("worker_id"),
        "elapsed_ms": unit.get("elapsed_ms"),
        "result": unit.get("result"),
        "error": unit.get("error"),
        "cleanup": cleanup,
    }


def run_agent(payload: dict[str, Any]) -> dict[str, Any]:
    model = str(payload.get("model") or DEFAULT_MODEL)
    cwd = str(payload.get("cwd") or "/root")
    user_system = str(payload.get("system") or "")
    incoming = payload.get("messages") or []
    if incoming and isinstance(incoming[-1], dict):
        last_text = str(incoming[-1].get("content", ""))
        email_re = r"\b(emails?|gmail|inbox)\b"
        if re.fullmatch(r"\s*(are\s+you\s+online|online\??|status\??|ping)\s*", last_text, re.I):
            final = f"Online. Root agent is running as uid {os.getuid()} with model {DEFAULT_MODEL}."
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 0,
                "trace": "",
            }
        if re.search(r"\b(composio\s+)?(login|log\s+in|sign\s+in|authenticate|auth)\b", last_text, re.I) or re.search(r"\bmy\s+email\s+is\s+[^@\s]+@[^@\s]+\.[^@\s]+", last_text, re.I):
            output = composio_login_start()
            final = summarize_composio_login_start(output)
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "composio_login_start\n" + output[:12000],
            }
        if re.fullmatch(r"\s*(done|i'?m done|i logged in|logged in|finished|complete|completed)\s*[.!]?\s*", last_text, re.I):
            final = summarize_done_state()
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "summarize_done_state",
            }
        if re.search(r"\blink\b", last_text, re.I) and re.search(r"\b(email|gmail)\b", last_text, re.I):
            output = composio_link_start("gmail")
            final = summarize_composio_link_start("gmail", output)
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "composio_link_start gmail\n" + output[:12000],
            }
        if (
            re.search(email_re, last_text, re.I)
            and re.search(r"\b(yesterday|summarize|summary|check|fetch|search|look)\b", last_text, re.I)
        ) or (
            recent_email_context(incoming)
            and re.search(r"\b(check\s+now|check|now|try\s+again|summarize|summary)\b", last_text, re.I)
        ):
            final = gmail_fetch_yesterday_summary()
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "gmail_fetch_yesterday_summary",
            }
        repo_url = github_repo_url(last_text)
        if repo_url and re.search(r"\b(download|clone|set\s*up|setup|install|get|pull)\b", last_text, re.I):
            run_tests = not re.search(r"\b(no\s+tests?|skip\s+tests?|do\s+not\s+test)\b", last_text, re.I)
            output = setup_github_repo(repo_url, run_tests=run_tests)
            final = summarize_repo_setup(output, os.path.join("/home/ubuntu/work", os.path.basename(repo_url.removesuffix(".git"))))
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": f"setup_github_repo {repo_url}\n{output[:12000]}",
            }
        if re.search(email_re, last_text, re.I) and re.search(r"\b(search|find|check|look|responses?|messages?)\b", last_text, re.I):
            query_parts: list[str] = []
            quoted = re.findall(r'"([^"]+)"|' + r"'([^']+)'", last_text)
            query_parts.extend([a or b for a, b in quoted if (a or b)])
            for keyword in ["fusion", "invoice", "receipt", "experiment", "response", "responded"]:
                if re.search(rf"\b{keyword}\b", last_text, re.I):
                    query_parts.append(keyword)
            query = " ".join(dict.fromkeys(query_parts)).strip() or last_text[:160]
            output = composio_list_tools("gmail", "search email fetch messages", limit=30)
            final = f"Cannot search Gmail yet. Requested query: {query}. {output.splitlines()[1] if len(output.splitlines()) > 1 else output}"
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "composio_list_tools gmail\n" + output[:12000],
            }
        toolkit = composio_toolkit_for_text(last_text)
        if toolkit and re.search(r"\b(composio|tool|tools|available|auth config|plugin|connected)\b", last_text, re.I):
            output = composio_list_tools(toolkit, "", limit=50)
            lines = [line for line in output.splitlines() if line.strip()]
            final = f"Composio {toolkit}: " + (lines[1] if len(lines) > 1 else (lines[0] if lines else "no output"))
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": f"composio_list_tools {toolkit}\n" + output[:12000],
            }
        if re.search(r"\b(voice\s+chat|voice\s+reply|speak|text\s*to\s*speech|tts)\b", last_text, re.I):
            query = "text to speech voice audio"
            output = composio_list_tools("elevenlabs", query, limit=20)
            lines = [line for line in output.splitlines() if line.strip()]
            final = "Voice/TTS tooling: " + (lines[1] if len(lines) > 1 else (lines[0] if lines else "Composio returned no output"))
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "composio_list_tools elevenlabs\n" + output[:12000],
            }
        if re.search(r"\b(eleven\s*labs|elevenlabs)\b", last_text, re.I):
            direct_spec = first_json_object(last_text)
            if direct_spec and isinstance(direct_spec.get("slug"), str):
                output = elevenlabs(direct_spec.get("slug") or "", direct_spec.get("data") if isinstance(direct_spec.get("data"), dict) else {})
                final = "ElevenLabs Composio result:\n\n" + output
                return {
                    "final": final,
                    "messages": incoming + [{"role": "assistant", "content": final}],
                    "tool_calls": 1,
                    "trace": "elevenlabs\n" + output[:12000],
                }
            search_text = re.sub(r"\b(eleven\s*labs|elevenlabs)\b", "", last_text, flags=re.I).strip() or "text to speech"
            output = composio(f"search {shlex.quote(search_text)} --toolkits elevenlabs --limit 10")
            final = (
                "I found the ElevenLabs tool surface through Composio. "
                "If it says the toolkit is not connected, run `/home/ubuntu/.local/bin/composio link elevenlabs` on the server.\n\n"
                + output
            )
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "composio search elevenlabs\n" + output[:12000],
            }
        list_match = re.search(r"\b(?:list|show)\s+(?:files|directory|dir)(?:\s+(?:in|at|under))?\s+(`?)(/\S+|\S+)\1", last_text, re.I)
        if list_match:
            path = list_match.group(2).rstrip("`.,")
            result = fs_list_dir(path, cwd)
            output = json_text(result, 50000)
            return {
                "final": output,
                "messages": incoming + [{"role": "assistant", "content": output}],
                "tool_calls": 1,
                "trace": "fs_list_dir\n" + output[:12000],
            }
        find_match = re.search(r"\b(?:find|locate|where\s+is|search\s+for)\s+(`?)([A-Za-z0-9_./*?\\-]+)\1(?:\s+(?:in|under)\s+(`?)(/\S+|\S+)\3)?", last_text, re.I)
        if find_match:
            query = find_match.group(2).rstrip("`.,")
            root = (find_match.group(4) or cwd or "/root").rstrip("`.,")
            result = fs_find_data(root, query, cwd)
            output = json_text(result, 50000)
            return {
                "final": output,
                "messages": incoming + [{"role": "assistant", "content": output}],
                "tool_calls": 1,
                "trace": "fs_find\n" + output[:12000],
            }
        if re.search(r"\b(run\s+a\s+)?swarm\s+test\s+job\b", last_text, re.I):
            result = run_swarm_test_job()
            output = json.dumps(result, indent=2)
            final = (
                f"Swarm test job {result['unit_status']} on worker {result.get('worker_id') or 'unknown'}.\n\n"
                f"{output}"
            )
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "swarm_test_job\n" + output[:12000],
            }
        if re.search(r"\b(custom\s+)?swarm\s+.*\bjob\b", last_text, re.I):
            direct_spec = first_json_object(last_text)
            if direct_spec and "kind" in direct_spec and "units" in direct_spec:
                result = create_custom_swarm_job(direct_spec)
                output = json.dumps(result, indent=2)
                final = f"Custom swarm job {'created' if result.get('ok') else 'rejected'}.\n\n{output}"
                return {
                    "final": final,
                    "messages": incoming + [{"role": "assistant", "content": final}],
                    "tool_calls": 1,
                    "trace": "swarm_create_job\n" + output[:12000],
                }
            text_artifact = re.search(r"\btext_artifact\b", last_text, re.I)
            if text_artifact:
                text_match = re.search(r"\btext\s+([^,.;\n]+)", last_text, re.I)
                name_match = re.search(r"\bname\s+([A-Za-z0-9_.-]+)", last_text, re.I)
                body = {
                    "kind": "text_artifact",
                    "units": [{
                        "text": (text_match.group(1).strip() if text_match else "custom swarm job"),
                        "name": (name_match.group(1).strip() if name_match else "output.txt"),
                    }],
                    "requirements": {"capabilities": ["cpu"]},
                    "metadata": {"purpose": "agent custom text_artifact job"},
                    "poll": bool(re.search(r"\bpoll\b|\bcomplete\b|\bresult\b", last_text, re.I)),
                    "cleanup": bool(re.search(r"\bdelete\b|\bcleanup\b|\bclean up\b", last_text, re.I)),
                }
                result = create_custom_swarm_job(body)
                output = json.dumps(result, indent=2)
                final = f"Custom text_artifact swarm job {'created' if result.get('ok') else 'rejected'}.\n\n{output}"
                return {
                    "final": final,
                    "messages": incoming + [{"role": "assistant", "content": final}],
                    "tool_calls": 1,
                    "trace": "swarm_create_job\n" + output[:12000],
                }
        prime_match = re.search(r"(?:count\s+)?primes?\s+(?:from|between)\s+(\d+)\s+(?:to|and)\s+(\d+)", last_text, re.I)
        if prime_match:
            start, end = int(prime_match.group(1)), int(prime_match.group(2))
            result = run_prime_count_job(start, end)
            output = json.dumps(result, indent=2)
            count = None
            if isinstance(result.get("result"), dict):
                count = result["result"].get("count")
            final = f"Prime-count swarm job {result['unit_status']} for {start}..{end}; count={count}.\n\n{output}"
            return {
                "final": final,
                "messages": incoming + [{"role": "assistant", "content": final}],
                "tool_calls": 1,
                "trace": "prime_count_job\n" + output[:12000],
            }
        file_match = re.search(r"print\s+(?:the\s+)?content(?:s)?(?:\s+of)?(?:\s+the\s+following\s+file\s+here:)?\s*(`?)(/\S+)\1", last_text, re.I)
        if file_match:
            path = file_match.group(2).rstrip("`.,")
            output = json_text(fs_read_file(path, cwd), 200000)
            return {
                "final": output,
                "messages": incoming + [{"role": "assistant", "content": output}],
                "tool_calls": 1,
                "trace": f"fs_read_file {json.dumps({'path': path, 'cwd': cwd})}\n{output[:12000]}",
            }
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM + "\n" + user_system}]
    for item in incoming[-20:]:
        if isinstance(item, dict) and item.get("role") in {"user", "assistant", "system"}:
            messages.append({"role": str(item["role"]), "content": str(item.get("content", ""))[:20000]})

    trace: list[str] = []
    max_tool_calls = max(1, min(int(payload.get("max_tool_calls") or MAX_TOOL_CALLS), 100))
    for _ in range(max_tool_calls):
        reply = ollama_chat(model, messages)
        action = extract_json(reply)
        if "final" in action:
            final = str(action.get("final") or "")
            if not final.strip():
                final = "I am online, but the local model returned an empty response. I did not fabricate an answer. Try a more specific request, or ask for status, files, Gmail, GitHub, or swarm work."
            if recent_email_context(incoming) and re.search(r"example\.com|\[Yesterday|Here are your emails|/tmp/composio/", final, re.I):
                final = "Blocked a fabricated email summary. I will only summarize Gmail after a real GMAIL_FETCH_EMAILS result is parsed."
                return {"final": final, "messages": incoming + [{"role": "assistant", "content": final}], "tool_calls": len(trace), "trace": "\n\n".join(trace)}
            if not trace and likely_requires_tool(str(incoming[-1].get("content", "")) if incoming and isinstance(incoming[-1], dict) else ""):
                toolkit = composio_toolkit_for_text(str(incoming[-1].get("content", ""))) if incoming and isinstance(incoming[-1], dict) else None
                if toolkit:
                    output = composio_list_tools(toolkit, "", limit=50)
                    lines = [line for line in output.splitlines() if line.strip()]
                    final = f"Composio {toolkit}: " + (lines[1] if len(lines) > 1 else (lines[0] if lines else "no output"))
                    return {
                        "final": final,
                        "messages": incoming + [{"role": "assistant", "content": final}],
                        "tool_calls": 1,
                        "trace": f"guarded_composio_list_tools {toolkit}\n{output[:12000]}",
                    }
                final = "This request requires a real tool call, and the model tried to answer without one. Ask again with the target toolkit or exact action."
            return {"final": final, "messages": incoming + [{"role": "assistant", "content": final}], "tool_calls": len(trace), "trace": "\n\n".join(trace)}
        tool = action.get("tool")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        try:
            if tool == "shell":
                output = shell(str(args.get("cmd") or ""), str(args.get("cwd") or cwd))
            elif tool == "fs_list_dir":
                output = json_text(fs_list_dir(str(args.get("path") or "."), str(args.get("cwd") or cwd), limit=int(args.get("limit") or 200)), 50000)
            elif tool == "fs_find":
                output = json_text(
                    fs_find_data(
                        str(args.get("root") or args.get("path") or cwd),
                        str(args.get("query") or args.get("name") or ""),
                        str(args.get("cwd") or cwd),
                        limit=int(args.get("limit") or 50),
                        max_depth=int(args.get("max_depth") or 12),
                    ),
                    50000,
                )
            elif tool == "fs_read_file":
                output = json_text(fs_read_file(str(args.get("path") or ""), str(args.get("cwd") or cwd), max_bytes=int(args.get("max_bytes") or 120000)), 200000)
            elif tool == "swarm_get":
                output = swarm_get(str(args.get("path") or "/health"))
            elif tool == "swarm_post":
                body = args.get("body") if isinstance(args.get("body"), dict) else {}
                output = swarm_post(str(args.get("path") or "/"), body)
            elif tool == "swarm_delete":
                output = swarm_delete_text(str(args.get("path") or "/"))
            elif tool == "swarm_test_job":
                output = json.dumps(run_swarm_test_job(), indent=2)[:12000]
            elif tool == "swarm_create_job":
                output = json.dumps(create_custom_swarm_job(args), indent=2)[:12000]
            elif tool == "swarm_poll_job":
                output = json.dumps(poll_swarm_job(str(args.get("job_id") or ""), int(args.get("timeout_seconds") or 120)), indent=2)[:12000]
            elif tool == "github":
                output = github(str(args.get("cmd") or "auth status"))
            elif tool == "gmail":
                slug = str(args.get("slug") or "GMAIL_FETCH_EMAILS")
                output = composio_execute(slug, args.get("data") if isinstance(args.get("data"), dict) else {})
                if slug == "GMAIL_FETCH_EMAILS" and output.startswith("exit_code=0"):
                    output = summarize_gmail_execute_output(output, "the requested Gmail search")
            elif tool == "google_drive":
                output = composio_execute(str(args.get("slug") or "GOOGLEDRIVE_SEARCH_FILES"), args.get("data") if isinstance(args.get("data"), dict) else {})
            elif tool == "elevenlabs":
                output = elevenlabs(str(args.get("slug") or ""), args.get("data") if isinstance(args.get("data"), dict) else {})
            elif tool == "composio_search":
                raw_toolkits = args.get("toolkits")
                toolkits = [str(item) for item in raw_toolkits] if isinstance(raw_toolkits, list) else None
                output = composio_search(str(args.get("query") or "tools"), toolkits, limit=int(args.get("limit") or 10))
            elif tool == "composio_list_tools":
                output = composio_list_tools(str(args.get("toolkit") or ""), str(args.get("query") or ""), limit=int(args.get("limit") or 20))
            elif tool == "composio_execute":
                slug = str(args.get("slug") or "")
                output = composio_execute(slug, args.get("data") if isinstance(args.get("data"), dict) else {})
                if slug == "GMAIL_FETCH_EMAILS" and output.startswith("exit_code=0"):
                    output = summarize_gmail_execute_output(output, "the requested Gmail search")
            elif tool == "composio_dev":
                output = composio_dev(str(args.get("cmd") or "auth-configs list"))
            elif tool == "composio":
                output = composio(str(args.get("cmd") or "whoami"))
            else:
                output = f"unknown tool: {tool}"
        except Exception as exc:
            output = f"tool error: {exc}"
        trace.append(f"{tool} {json.dumps(args, sort_keys=True)[:1000]}\n{output[:3000]}")
        messages.append({"role": "assistant", "content": json.dumps(action)})
        messages.append({"role": "user", "content": "Tool result:\n" + output[:12000]})
    final = f"I hit the tool-call limit ({max_tool_calls}) before producing a final answer. Review the trace and ask me to continue."
    return {"final": final, "messages": incoming + [{"role": "assistant", "content": final}], "tool_calls": len(trace), "trace": "\n\n".join(trace)}


def start_agent_job(payload: dict[str, Any]) -> dict[str, str]:
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "status": "running", "started_at": time.time(), "updated_at": time.time()}

    def worker() -> None:
        try:
            result = run_agent(payload)
            with JOBS_LOCK:
                JOBS[job_id].update({"status": "done", "updated_at": time.time(), "result": result})
        except Exception as exc:
            with JOBS_LOCK:
                JOBS[job_id].update({"status": "error", "updated_at": time.time(), "error": str(exc)})

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


def get_agent_job(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return {"status": "missing", "error": "job not found"}
    job["elapsed_seconds"] = int(time.time() - float(job.get("started_at") or time.time()))
    return job


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def authorized(self) -> bool:
        return bool(TOKEN) and self.headers.get("X-Agent-Token") == TOKEN

    def send_json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        if self.path.startswith("/chat/jobs/"):
            self.send_json(200, get_agent_job(self.path.rsplit("/", 1)[-1]))
            return
        if self.path != "/status":
            self.send_json(404, {"error": "not found"})
            return
        self.send_json(200, {
            "ok": True,
            "uid": os.getuid(),
            "root": os.getuid() == 0,
            "ollama": OLLAMA_URL,
            "openai_base_url": OPENAI_BASE_URL,
            "model": DEFAULT_MODEL,
            "controller": CONTROLLER_URL,
            "max_tool_calls": MAX_TOOL_CALLS,
            "plugins": plugin_status(),
        })

    def do_POST(self) -> None:
        try:
            if not self.authorized():
                self.send_json(401, {"error": "unauthorized"})
                return
            if self.path not in {"/chat", "/chat/start"}:
                self.send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/chat/start":
                self.send_json(202, start_agent_job(payload))
            else:
                self.send_json(200, run_agent(payload))
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
