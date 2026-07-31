from __future__ import annotations

import json
import os
import ipaddress
import io
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies

from .model_client import ModelClientError, ModelSession, ToolCall, resolve_model_route
from .models import AgentFarmConfig, CommandResult, RunPaths
from .review import normalize_path, path_matches
from .util import ensure_inside, run_command


class NativeAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeAgentResult:
    ok: bool
    final_text: str
    terminal_payload: dict[str, Any] | None = None
    error: str | None = None


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required if required is not None else list(properties),
    }


READ_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "List repository files below a relative directory. Use this to learn project structure.",
        "parameters": _object_schema(
            {
                "path": {"type": "string", "description": "Repository-relative directory, or '.' for root."},
                "pattern": {"type": "string", "description": "Filename glob such as '*.py' or '*' for all files."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            }
        ),
    },
    {
        "name": "search_text",
        "description": "Search UTF-8 repository files for a literal text fragment and return matching lines.",
        "parameters": _object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "path": {"type": "string", "description": "Repository-relative file or directory."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            }
        ),
    },
    {
        "name": "read_file",
        "description": "Read a bounded line range from a UTF-8 repository file with line numbers.",
        "parameters": _object_schema(
            {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            }
        ),
    },
]


WRITE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "write_file",
        "description": "Create or fully replace a UTF-8 file inside the Worker's allowed path set.",
        "parameters": _object_schema(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            }
        ),
    },
    {
        "name": "replace_text",
        "description": "Replace exact text in a UTF-8 file. By default the old text must occur exactly once.",
        "parameters": _object_schema(
            {
                "path": {"type": "string"},
                "old_text": {"type": "string", "minLength": 1},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
            }
        ),
    },
    {
        "name": "run_command",
        "description": "Run one bounded build, test, lint, search, or read-only Git command without a shell.",
        "parameters": _object_schema(
            {
                "argv": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "cwd": {"type": "string", "description": "Repository-relative working directory or '.'."},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
            }
        ),
    },
]


WEB_TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": (
            "Search the public web. Returns source titles, URLs, snippets, and publication "
            "dates when the search engine provides them. Use precise queries and verify "
            "important claims by fetching primary sources."
        ),
        "parameters": _object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            }
        ),
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch a public HTTP or HTTPS page and extract bounded readable text. "
            "Local, private-network, credential-bearing, and non-web URLs are blocked."
        ),
        "parameters": _object_schema(
            {
                "url": {"type": "string", "minLength": 1},
                "max_characters": {"type": "integer", "minimum": 1000, "maximum": 50000},
            }
        ),
    },
]

WEB_TOOL_NAMES = frozenset({"web_search", "fetch_url"})
WEB_RESEARCH_CALL_BUDGET = 10
WEB_RESEARCH_TURN_DEADLINE = 12
WEB_RESEARCH_BUDGET_MESSAGE = (
    "The web-research budget is exhausted. Do not perform more web searches or fetches. "
    "Use the evidence already collected, write the requested artifact now, read it back "
    "to verify it, and then call finish. Clearly disclose any remaining evidence limits."
)


class _ReadableHTMLParser(HTMLParser):
    """Small dependency-free HTML-to-text extractor for bounded agent context."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "svg"}:
            self._hidden_depth += 1
        if lowered == "title":
            self._in_title = True
        if lowered in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1
        if lowered == "title":
            self._in_title = False
        if lowered in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self._title_parts).split())

    @property
    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _plain_text(raw_html: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(raw_html)
    return parser.text


FINISH_TOOL = {
    "name": "finish",
    "description": "Finish the Worker only after implementation and verification are complete.",
    "parameters": _object_schema(
        {
            "summary": {"type": "string", "minLength": 1},
            "tests": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
        }
    ),
}


class ToolRuntime:
    def __init__(
        self,
        *,
        worktree: Path,
        config: AgentFarmConfig,
        writable: bool,
    ) -> None:
        self.worktree = worktree.resolve()
        self.config = config
        self.writable = writable

    def specs(self) -> list[dict[str, Any]]:
        tools = list(READ_TOOLS)
        if self._network_enabled():
            tools.extend(WEB_TOOLS)
        if self.writable:
            tools.extend(WRITE_TOOLS)
        return tools

    def execute(self, call: ToolCall) -> dict[str, Any]:
        handlers = {
            "list_files": self._list_files,
            "search_text": self._search_text,
            "read_file": self._read_file,
        }
        if self._network_enabled():
            handlers.update(
                {
                    "web_search": self._web_search,
                    "fetch_url": self._fetch_url,
                }
            )
        if self.writable:
            handlers.update(
                {
                    "write_file": self._write_file,
                    "replace_text": self._replace_text,
                    "run_command": self._run_command,
                }
            )
        handler = handlers.get(call.name)
        if handler is None:
            raise NativeAgentError(f"Unknown or unavailable tool: {call.name}")
        result = handler(call.arguments)
        return result if isinstance(result, dict) else {"result": result}

    def _network_enabled(self) -> bool:
        return self.config.codex_config_overrides.get(
            "sandbox_workspace_write.network_access"
        ) is True

    def _resolve(self, raw_path: Any, *, file_required: bool = False) -> tuple[Path, str]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise NativeAgentError("path must be a non-empty string")
        candidate = Path(raw_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise NativeAgentError("path must stay inside the worktree")
        resolved = (self.worktree / candidate).resolve()
        ensure_inside(self.worktree, resolved)
        relative = normalize_path(resolved.relative_to(self.worktree).as_posix()) or "."
        if relative != "." and self._is_forbidden(relative):
            raise NativeAgentError(f"Access denied by forbidden path rule: {relative}")
        if file_required and not resolved.is_file():
            raise NativeAgentError(f"File does not exist: {relative}")
        return resolved, relative

    def _is_forbidden(self, relative: str) -> bool:
        return any(path_matches(pattern, relative) for pattern in self.config.forbidden_paths)

    def _check_write(self, relative: str) -> None:
        if not self.writable:
            raise NativeAgentError("This Agent is read-only.")
        if self._is_forbidden(relative):
            raise NativeAgentError(f"Write denied by forbidden path rule: {relative}")
        if self.config.allowed_paths and not any(
            path_matches(pattern, relative) for pattern in self.config.allowed_paths
        ):
            raise NativeAgentError(f"Write is outside allowed paths: {relative}")

    def _iter_files(self, root: Path):
        if root.is_file():
            yield root
            return
        for current, directories, filenames in os.walk(root):
            directories[:] = [name for name in directories if name not in {".git", ".agent-farm"}]
            for filename in filenames:
                path = Path(current) / filename
                try:
                    relative = path.resolve().relative_to(self.worktree).as_posix()
                except ValueError:
                    continue
                if not self._is_forbidden(relative):
                    yield path

    def _list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        root, _ = self._resolve(args.get("path", "."))
        if not root.exists():
            raise NativeAgentError("The requested path does not exist.")
        pattern = str(args.get("pattern") or "*")
        maximum = max(1, min(int(args.get("max_results", 200)), 500))
        results: list[str] = []
        for path in self._iter_files(root):
            relative = path.resolve().relative_to(self.worktree).as_posix()
            if PurePosixPath(relative).match(pattern) or path.match(pattern):
                results.append(relative)
                if len(results) >= maximum:
                    break
        return {"files": sorted(results), "truncated": len(results) >= maximum}

    def _search_text(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        if not isinstance(query, str) or not query:
            raise NativeAgentError("query must be a non-empty string")
        root, _ = self._resolve(args.get("path", "."))
        if not root.exists():
            raise NativeAgentError("The requested path does not exist.")
        maximum = max(1, min(int(args.get("max_results", 100)), 200))
        matches: list[dict[str, Any]] = []
        for path in self._iter_files(root):
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.resolve().relative_to(self.worktree).as_posix()
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    matches.append(
                        {"path": relative, "line": line_number, "text": line[:500]}
                    )
                    if len(matches) >= maximum:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def _read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path, relative = self._resolve(args.get("path"), file_required=True)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise NativeAgentError(f"File is not UTF-8 text: {relative}") from exc
        start = max(1, int(args.get("start_line", 1)))
        requested_end = max(start, int(args.get("end_line", start + 299)))
        end = min(requested_end, start + 499, len(lines))
        content = "\n".join(f"{index}: {lines[index - 1]}" for index in range(start, end + 1))
        return {
            "path": relative,
            "start_line": start,
            "end_line": end,
            "total_lines": len(lines),
            "content": content[: self.config.native_max_output_chars],
            "truncated": requested_end > end or len(content) > self.config.native_max_output_chars,
        }

    @staticmethod
    def _validate_public_url(raw_url: Any) -> str:
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise NativeAgentError("url must be a non-empty string")
        parsed = urlsplit(raw_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise NativeAgentError("Only public HTTP and HTTPS URLs are available")
        if parsed.username is not None or parsed.password is not None:
            raise NativeAgentError("Credential-bearing URLs are not available")
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith(
            (".localhost", ".local", ".internal", ".home", ".lan")
        ):
            raise NativeAgentError("Local and private-network URLs are blocked")
        try:
            literal_address = ipaddress.ip_address(hostname.strip("[]"))
        except ValueError:
            literal_address = None
        if literal_address is not None:
            if not literal_address.is_global:
                raise NativeAgentError("Local and private-network URLs are blocked")
            return raw_url.strip()

        # Corporate/VPN proxy clients often return RFC 2544 fake-IP addresses
        # (198.18.0.0/15) for every public hostname. In that mode the HTTP proxy,
        # not this process, resolves the origin, so local DNS cannot classify it.
        # Hostname and literal-IP blocks above still prevent common SSRF targets.
        proxies = getproxies()
        if proxies.get(parsed.scheme):
            return raw_url.strip()
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except socket.gaierror as exc:
            raise NativeAgentError(f"Could not resolve public host: {hostname}") from exc
        if not addresses:
            raise NativeAgentError(f"Could not resolve public host: {hostname}")
        for address in addresses:
            parsed_address = ipaddress.ip_address(address)
            if not parsed_address.is_global:
                raise NativeAgentError("Local and private-network URLs are blocked")
        return raw_url.strip()

    @staticmethod
    def _http_get(raw_url: str, *, max_bytes: int = 2_000_000) -> tuple[str, str, str]:
        class _NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
                return None

        current = raw_url
        opener = build_opener(_NoRedirect())
        for _ in range(6):
            ToolRuntime._validate_public_url(current)
            request = Request(
                current,
                headers={
                    "User-Agent": "AgentFarm/0.4",
                    "Accept": "text/html,application/xhtml+xml,application/json,text/plain,application/xml,text/xml;q=0.9,*/*;q=0.2",
                },
                method="GET",
            )
            try:
                with opener.open(request, timeout=30) as response:
                    content_type = response.headers.get_content_type()
                    charset = response.headers.get_content_charset() or "utf-8"
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        body = body[:max_bytes]
                    if content_type == "application/pdf":
                        return body.decode("latin-1"), content_type, current
                    return body.decode(charset, errors="replace"), content_type, current
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location:
                        raise NativeAgentError("The page returned an empty redirect") from exc
                    current = urljoin(current, location)
                    continue
                raise NativeAgentError(f"The page returned HTTP {exc.code}") from exc
            except URLError as exc:
                raise NativeAgentError(f"Could not fetch the page: {exc.reason}") from exc
            except TimeoutError as exc:
                raise NativeAgentError("The page request timed out") from exc
        raise NativeAgentError("The page redirected too many times")

    def _web_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise NativeAgentError("query must be a non-empty string")
        maximum = max(1, min(int(args.get("max_results", 8)), 10))
        endpoints = [
            "https://www.bing.com/news/search?"
            + urlencode({"q": query.strip(), "format": "RSS", "setlang": "en-US"}),
            "https://www.bing.com/search?"
            + urlencode({"q": query.strip(), "format": "rss", "setlang": "en-US"}),
        ]
        buckets: list[list[dict[str, str]]] = []
        for endpoint in endpoints:
            raw, _, _ = self._http_get(endpoint)
            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise NativeAgentError("The web search service returned invalid results") from exc
            bucket: list[dict[str, str]] = []
            for item in root.findall("./channel/item")[:maximum]:
                title = (item.findtext("title") or "").strip()
                url = (item.findtext("link") or "").strip()
                parsed_url = urlsplit(url)
                if parsed_url.hostname in {"bing.com", "www.bing.com"}:
                    target = parse_qs(parsed_url.query).get("url", [])
                    if target:
                        url = target[0]
                description = _plain_text(item.findtext("description") or "")
                published = (item.findtext("pubDate") or "").strip()
                if url:
                    bucket.append(
                        {
                            "title": title,
                            "url": url,
                            "snippet": description[:1000],
                            "published": published,
                        }
                    )
            buckets.append(bucket)
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        while len(results) < maximum and any(buckets):
            for bucket in buckets:
                if not bucket or len(results) >= maximum:
                    continue
                item = bucket.pop(0)
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                results.append(item)
        return {"query": query.strip(), "results": results}

    def _fetch_url(self, args: dict[str, Any]) -> dict[str, Any]:
        url = self._validate_public_url(args.get("url"))
        maximum = max(1000, min(int(args.get("max_characters", 30000)), 50000))
        raw, content_type, final_url = self._http_get(url, max_bytes=20_000_000)
        if content_type in {"text/html", "application/xhtml+xml"}:
            parser = _ReadableHTMLParser()
            parser.feed(raw)
            title = parser.title
            content = parser.text
        elif content_type.startswith("text/") or content_type in {
            "application/json",
            "application/xml",
            "application/rss+xml",
        }:
            title = ""
            content = raw
        elif content_type == "application/pdf":
            try:
                from pypdf import PdfReader

                reader = PdfReader(io.BytesIO(raw.encode("latin-1")))
                extracted: list[str] = []
                extracted_length = 0
                for page in reader.pages:
                    text = page.extract_text() or ""
                    if text:
                        extracted.append(text)
                        extracted_length += len(text)
                    if extracted_length >= maximum * 2:
                        break
                content = "\n\n".join(extracted)
                metadata = reader.metadata
                title = str(metadata.title or "") if metadata else ""
            except Exception as exc:
                raise NativeAgentError("The PDF could not be parsed as readable text") from exc
        else:
            raise NativeAgentError(f"Unsupported page content type: {content_type}")
        return {
            "url": final_url,
            "title": title,
            "content_type": content_type,
            "text": content[:maximum],
            "truncated": len(content) > maximum,
        }

    def _write_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path, relative = self._resolve(args.get("path"))
        self._check_write(relative)
        content = args.get("content")
        if not isinstance(content, str):
            raise NativeAgentError("content must be a string")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return {"path": relative, "characters_written": len(content)}

    def _replace_text(self, args: dict[str, Any]) -> dict[str, Any]:
        path, relative = self._resolve(args.get("path"), file_required=True)
        self._check_write(relative)
        old = args.get("old_text")
        new = args.get("new_text")
        replace_all = args.get("replace_all")
        if not isinstance(old, str) or not old:
            raise NativeAgentError("old_text must be a non-empty string")
        if not isinstance(new, str) or type(replace_all) is not bool:
            raise NativeAgentError("new_text must be a string and replace_all must be boolean")
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise NativeAgentError("old_text was not found")
        if not replace_all and count != 1:
            raise NativeAgentError(f"old_text occurs {count} times; make the match unique or use replace_all")
        updated = text.replace(old, new, -1 if replace_all else 1)
        path.write_text(updated, encoding="utf-8", newline="")
        return {"path": relative, "replacements": count if replace_all else 1}

    def _run_command(self, args: dict[str, Any]) -> dict[str, Any]:
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
            raise NativeAgentError("argv must be a non-empty array of strings")
        cwd, relative_cwd = self._resolve(args.get("cwd", "."))
        if not cwd.is_dir():
            raise NativeAgentError("Command cwd must be a directory")
        self._validate_command(argv)
        requested_timeout = int(args.get("timeout_seconds", self.config.native_command_timeout_seconds))
        timeout = min(requested_timeout, self.config.native_command_timeout_seconds)
        result = run_command(argv, cwd, timeout_seconds=timeout)
        stdout = result.stdout[-self.config.native_max_output_chars :]
        stderr = result.stderr[-self.config.native_max_output_chars :]
        return {
            "argv": argv,
            "cwd": relative_cwd,
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": len(result.stdout) > len(stdout) or len(result.stderr) > len(stderr),
        }

    @staticmethod
    def _validate_command(argv: list[str]) -> None:
        executable = Path(argv[0]).name.lower()
        if executable.endswith((".exe", ".cmd", ".bat")):
            executable = executable.rsplit(".", 1)[0]
        rest = argv[1:]
        if executable == "git":
            if not rest or rest[0] not in {"status", "diff", "show", "log", "ls-files", "grep"}:
                raise NativeAgentError("Only read-only Git commands are available to native Workers")
            return
        if executable in {"python", "python3", "py"}:
            if len(rest) < 2 or rest[0] != "-m" or rest[1] not in {
                "unittest", "pytest", "compileall", "ruff", "mypy"
            }:
                raise NativeAgentError("Python is limited to approved test and analysis modules")
            return
        if executable in {"pytest", "ruff", "mypy", "rg"}:
            return
        if executable in {"npm", "pnpm", "yarn", "bun"}:
            if not rest or rest[0] not in {"test", "run", "lint", "check", "build"}:
                raise NativeAgentError("Package managers are limited to repository scripts")
            return
        if executable == "cargo" and rest and rest[0] in {"test", "check", "clippy", "fmt"}:
            return
        if executable == "dotnet" and rest and rest[0] in {"test", "build", "format"}:
            return
        if executable == "go" and rest and rest[0] in {"test", "vet", "fmt"}:
            return
        raise NativeAgentError(f"Command is not in the native Worker allowlist: {argv[0]}")


class EventWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.sequence = 0

    def emit(self, event_type: str, **payload: Any) -> None:
        self.sequence += 1
        event = {
            "sequence": self.sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=True) + "\n")


def _safe_event_arguments(call: ToolCall) -> dict[str, Any]:
    arguments = dict(call.arguments)
    if call.name == "write_file" and isinstance(arguments.get("content"), str):
        arguments["content"] = f"<{len(arguments['content'])} characters>"
    if call.name == "replace_text":
        for key in ("old_text", "new_text"):
            if isinstance(arguments.get(key), str):
                arguments[key] = f"<{len(arguments[key])} characters>"
    return arguments


def _format_finish(payload: dict[str, Any]) -> str:
    lines = [str(payload.get("summary") or "Worker completed the task.")]
    tests = payload.get("tests") or []
    notes = payload.get("notes") or []
    if tests:
        lines.extend(["", "Tests:", *[f"- {item}" for item in tests]])
    if notes:
        lines.extend(["", "Notes:", *[f"- {item}" for item in notes]])
    return "\n".join(lines).strip() + "\n"


def run_native_agent(
    *,
    config: AgentFarmConfig,
    repo_root: Path,
    worktree: Path,
    prompt: str,
    system_prompt: str,
    provider: str | None,
    model: str | None,
    timeout_seconds: int,
    writable: bool,
    events_file: Path,
    terminal_tool: dict[str, Any],
    reasoning_mode: str | None = None,
    reasoning_effort: str | None = None,
    session: ModelSession | None = None,
) -> NativeAgentResult:
    runtime = ToolRuntime(worktree=worktree, config=config, writable=writable)
    events = EventWriter(events_file)
    legacy_reasoning = config.codex_config_overrides.get("model_reasoning_effort")
    if legacy_reasoning is not None and not isinstance(legacy_reasoning, str):
        legacy_reasoning = None
    selected_reasoning = reasoning_effort or legacy_reasoning
    if session is None:
        try:
            route = resolve_model_route(
                config=config,
                repo_root=repo_root,
                provider_id=provider,
                model=model,
            )
            session = ModelSession(
                route=route,
                system_prompt=system_prompt,
                timeout_seconds=timeout_seconds,
                reasoning_effort=selected_reasoning,
                reasoning_mode=reasoning_mode,
            )
        except (ModelClientError, OSError, ValueError) as exc:
            events.emit("agent.failed", error=str(exc))
            return NativeAgentResult(False, "", error=str(exc))

    tools = runtime.specs() + [terminal_tool]
    pending_results: list[dict[str, str]] = []
    terminal_name = terminal_tool["name"]
    web_tool_calls = 0
    web_research_started = False
    web_budget_notice_sent = False
    events.emit(
        "agent.started",
        model=model,
        provider=provider,
        writable=writable,
        max_turns=config.native_max_turns,
    )
    try:
        for turn in range(1, config.native_max_turns + 1):
            events.emit("turn.started", turn=turn)
            research_budget_exhausted = web_tool_calls >= WEB_RESEARCH_CALL_BUDGET or (
                web_research_started and turn >= WEB_RESEARCH_TURN_DEADLINE
            )
            turn_tools = (
                [tool for tool in tools if tool["name"] not in WEB_TOOL_NAMES]
                if research_budget_exhausted
                else tools
            )
            turn_prompt = prompt if turn == 1 else None
            if research_budget_exhausted and not web_budget_notice_sent:
                turn_prompt = WEB_RESEARCH_BUDGET_MESSAGE
                web_budget_notice_sent = True
                events.emit(
                    "research.budget_exhausted",
                    turn=turn,
                    web_tool_calls=web_tool_calls,
                )
            reply = session.send(
                prompt=turn_prompt,
                tool_results=pending_results,
                tools=turn_tools,
            )
            pending_results = []
            if reply.text:
                events.emit("item.completed", turn=turn, item={"type": "agent_message", "text": reply.text})
            terminal_payload: dict[str, Any] | None = None
            for call in reply.tool_calls:
                events.emit(
                    "item.started",
                    turn=turn,
                    item={"type": "tool_call", "call_id": call.call_id, "name": call.name, "arguments": _safe_event_arguments(call)},
                )
                if call.name == terminal_name:
                    terminal_payload = call.arguments
                    events.emit(
                        "item.completed",
                        turn=turn,
                        item={"type": "tool_call", "call_id": call.call_id, "name": call.name, "status": "completed"},
                    )
                    break
                if call.name in WEB_TOOL_NAMES:
                    web_research_started = True
                    if web_tool_calls >= WEB_RESEARCH_CALL_BUDGET:
                        encoded = json.dumps(
                            {"ok": False, "error": WEB_RESEARCH_BUDGET_MESSAGE},
                            ensure_ascii=False,
                        )
                        pending_results.append({"call_id": call.call_id, "output": encoded})
                        events.emit(
                            "item.completed",
                            turn=turn,
                            item={
                                "type": "tool_call",
                                "call_id": call.call_id,
                                "name": call.name,
                                "status": "failed",
                                "output": encoded,
                            },
                        )
                        continue
                    web_tool_calls += 1
                try:
                    output = runtime.execute(call)
                    encoded = json.dumps({"ok": True, **output}, ensure_ascii=False)
                    status = "completed"
                except (NativeAgentError, OSError, ValueError) as exc:
                    encoded = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                    status = "failed"
                pending_results.append({"call_id": call.call_id, "output": encoded})
                events.emit(
                    "item.completed",
                    turn=turn,
                    item={
                        "type": "tool_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "status": status,
                        "output": encoded[:4_000],
                    },
                )
            events.emit("turn.completed", turn=turn, usage=reply.usage)
            if terminal_payload is not None:
                final_text = (
                    _format_finish(terminal_payload)
                    if terminal_name == "finish"
                    else json.dumps(terminal_payload, indent=2, ensure_ascii=False) + "\n"
                )
                events.emit("agent.completed", turn=turn)
                return NativeAgentResult(True, final_text, terminal_payload=terminal_payload)
            if not reply.tool_calls:
                if reply.text:
                    events.emit("agent.completed", turn=turn, completion="message")
                    return NativeAgentResult(True, reply.text.strip() + "\n")
                raise NativeAgentError("The model returned neither a message nor a tool call.")
        raise NativeAgentError(f"Native Agent reached the {config.native_max_turns}-turn limit.")
    except (ModelClientError, NativeAgentError, OSError, ValueError) as exc:
        events.emit("agent.failed", error=str(exc))
        return NativeAgentResult(False, "", error=str(exc))


WORKER_SYSTEM_PROMPT = """You are an autonomous software implementation Worker inside Agent Farm.

Inspect the repository, implement the assigned task, and verify the result without asking the user
for routine decisions. Use the provided file tools for edits and the bounded command tool for tests.
Never access secrets, never change files outside the allowed path set, and never merge, push, deploy,
or modify permissions. Keep changes focused. When the work is complete, call finish with an honest
summary, the checks you ran, and any remaining risks. Do not call finish before inspecting the diff
and running the most relevant available verification.

For web-research tasks, avoid open-ended browsing. Use task source leads first, set a finite search
budget, and treat inaccessible sources as evidence limitations instead of retrying indefinitely.
Write the requested artifact as soon as enough reliable evidence exists; by turn 12 at the latest,
stop discovering new sources, write the artifact, read it back, and finish.
"""


def run_native_worker(
    *,
    config: AgentFarmConfig,
    paths: RunPaths,
    prompt: str,
    model: str | None,
    timeout_seconds: int | None,
) -> CommandResult:
    selected_model = model or config.worker_model
    result = run_native_agent(
        config=config,
        repo_root=paths.repo_root,
        worktree=paths.worktree,
        prompt=prompt,
        system_prompt=WORKER_SYSTEM_PROMPT,
        provider=config.worker_provider,
        model=selected_model,
        timeout_seconds=timeout_seconds or config.timeout_seconds,
        writable=True,
        events_file=paths.worker_events_file,
        terminal_tool=FINISH_TOOL,
        reasoning_mode=config.worker_reasoning_mode,
        reasoning_effort=config.worker_reasoning_effort,
    )
    paths.worker_final_file.write_text(result.final_text, encoding="utf-8")
    paths.worker_stderr_file.write_text(result.error or "", encoding="utf-8")
    return CommandResult(
        args=["agent-farm-native", selected_model or ""],
        cwd=str(paths.worktree),
        returncode=0 if result.ok else 1,
        stdout=paths.worker_events_file.read_text(encoding="utf-8"),
        stderr=result.error or "",
    )
