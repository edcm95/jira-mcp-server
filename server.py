#!/usr/bin/env python3

import os
import sys
import time
import base64
import logging
import uvicorn
import httpx
from typing import Any
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from md_to_adf import markdown_to_adf

# ── logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)-5s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("jira-mcp")

def _log(prefix: str, msg: str, level: int = logging.INFO):
    log.log(level, f"[{prefix}] {msg}")

def _tool_ok(tool: str, detail: str, elapsed: float):
    _log("CALL ", f"{tool} | {detail} | {elapsed:.3f}s")

def _tool_err(tool: str, detail: str, error: Exception):
    _log("CALL ", f"{tool} | {detail} | ERROR: {error}", logging.ERROR)

# ── config ─────────────────────────────────────────────────────────────────────

JIRA_BASE_URL  = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL     = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_API_VER   = os.getenv("JIRA_API_VERSION", "3")
MCP_PORT       = int(os.getenv("MCP_PORT", "8766"))
MCP_API_KEY    = os.getenv("MCP_API_KEY", "")

if not JIRA_BASE_URL or not JIRA_API_TOKEN:
    log.critical("JIRA_BASE_URL and JIRA_API_TOKEN must be set in the environment.")
    sys.exit(1)

_AUTH_MODE = "cloud" if JIRA_EMAIL else "server"
_log("CONN ", f"Jira URL    : {JIRA_BASE_URL}")
_log("CONN ", f"Auth mode   : {_AUTH_MODE} ({'email+token' if _AUTH_MODE == 'cloud' else 'Bearer PAT'})")
_log("CONN ", f"API version : {JIRA_API_VER}")
_log("CONN ", f"MCP port    : {MCP_PORT}")
_log("CONN ", f"MCP auth    : {'API key required' if MCP_API_KEY else 'DISABLED — no MCP_API_KEY set'}")

# ── Jira HTTP helpers ──────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    if JIRA_EMAIL:
        creds = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
        auth  = f"Basic {creds}"
    else:
        auth = f"Bearer {JIRA_API_TOKEN}"
    return {
        "Authorization": auth,
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

def _client() -> httpx.Client:
    return httpx.Client(base_url=JIRA_BASE_URL, headers=_headers(), timeout=30)

def _api(path: str) -> str:
    return f"/rest/api/{JIRA_API_VER}/{path}"

def _get(path: str, **params) -> Any:
    with _client() as c:
        r = c.get(_api(path), params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

def _post(path: str, body: dict) -> Any:
    with _client() as c:
        r = c.post(_api(path), json=body)
        r.raise_for_status()
        return r.json() if r.content else {}

def _put(path: str, body: dict) -> Any:
    with _client() as c:
        r = c.put(_api(path), json=body)
        r.raise_for_status()
        return r.json() if r.content else {}

# ── formatting helpers ─────────────────────────────────────────────────────────

def _table(rows: list[dict], cols: list[str]) -> str:
    """Render a list of dicts as an ASCII table.
    cols format: "HEADER:key"  (header label : dict key)
    """
    if not rows:
        return "(no rows)"
    labels = [c.split(":")[0] for c in cols]
    keys   = [c.split(":")[-1] for c in cols]
    widths = [max(len(h), max(len(str(r.get(k, ""))) for r in rows)) for h, k in zip(labels, keys)]
    sep    = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(labels)) + " |"
    lines  = [sep, header, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(k, "")).ljust(widths[i]) for i, k in enumerate(keys)) + " |")
    lines.append(sep)
    lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)

def _adf_to_text(node: Any) -> str:
    """Recursively extract plain text from an Atlassian Document Format node."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    ntype   = node.get("type", "")
    content = node.get("content", [])
    if ntype == "text":
        return node.get("text", "")
    block_types = {"paragraph", "heading", "bulletList", "orderedList",
                   "listItem", "blockquote", "codeBlock", "rule"}
    sep   = "\n" if ntype in block_types else ""
    parts = [_adf_to_text(child) for child in content]
    return sep.join(parts) + (sep if sep and parts else "")

def _text_to_adf(text: str) -> dict:
    """Wrap plain text in a minimal ADF document (required for API v3)."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }

def _description_field(text: str) -> Any:
    return _text_to_adf(text) if JIRA_API_VER == "3" else text

def _format_issue(issue: dict) -> str:
    f      = issue.get("fields", {})
    sep    = "─" * 60
    status = (f.get("status")    or {}).get("name", "")
    itype  = (f.get("issuetype") or {}).get("name", "")
    prio   = (f.get("priority")  or {}).get("name", "")
    asn    = (f.get("assignee")  or {}).get("displayName", "—")
    rep    = (f.get("reporter")  or {}).get("displayName", "—")
    labels = ", ".join(f.get("labels") or []) or "—"
    desc   = _adf_to_text(f.get("description")).strip()
    lines  = [
        f"ISSUE: {issue['key']}",
        sep,
        f"Summary   : {f.get('summary', '')}",
        f"Status    : {status}",
        f"Type      : {itype}",
        f"Priority  : {prio}",
        f"Assignee  : {asn}",
        f"Reporter  : {rep}",
        f"Created   : {(f.get('created') or '')[:19].replace('T', ' ')}",
        f"Updated   : {(f.get('updated') or '')[:19].replace('T', ' ')}",
        f"Labels    : {labels}",
        sep,
    ]
    if desc:
        lines += ["Description:", desc, sep]
    return "\n".join(lines)

# ── MCP server ─────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "Jira",
    host="0.0.0.0",
    port=MCP_PORT,
    instructions=(
        f"Connected to Jira at {JIRA_BASE_URL}. "
        "Use these tools to read and manage Jira issues, projects, and workflows. "
        "Prefer search_issues with JQL for bulk lookups; use get_issue for full detail. "
        "Use get_transitions before transition_issue to find valid transition IDs. "
        "Use find_users to resolve accountIds before assigning issues. "
        "For comments: use add_comment for plain text; use add_comment_markdown when the "
        "comment contains Markdown formatting (headers, bold, lists, code blocks, tables, etc.)."
    ),
)

# ── read tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_issue(issue_key: str) -> str:
    """Get full details of a Jira issue by its key (e.g. PROJECT-123)."""
    start = time.perf_counter()
    try:
        issue   = _get(f"issue/{issue_key}")
        elapsed = time.perf_counter() - start
        _tool_ok("get_issue", issue_key, elapsed)
        return _format_issue(issue)
    except Exception as e:
        _tool_err("get_issue", issue_key, e)
        raise


@mcp.tool()
def search_issues(jql: str, max_results: int = 50) -> str:
    """
    Search Jira issues using JQL (Jira Query Language).
    Examples:
      project = MYPROJ AND status = "In Progress"
      assignee = currentUser() ORDER BY updated DESC
      text ~ "login bug" AND created >= -7d
    """
    start = time.perf_counter()
    try:
        data = _post("search/jql", {
            "jql":        jql,
            "maxResults": min(max_results, 100),
            "fields":     ["key", "summary", "status", "assignee",
                           "priority", "issuetype", "created", "updated"],
        })
        elapsed = time.perf_counter() - start
        issues  = data.get("issues", [])
        rows    = [{
            "key":      i["key"],
            "summary":  (i["fields"].get("summary") or "")[:60],
            "type":     (i["fields"].get("issuetype") or {}).get("name", ""),
            "status":   (i["fields"].get("status")    or {}).get("name", ""),
            "priority": (i["fields"].get("priority")  or {}).get("name", ""),
            "assignee": ((i["fields"].get("assignee") or {}).get("displayName") or "—")[:25],
        } for i in issues]
        total  = data.get("total", len(issues))
        _tool_ok("search_issues", f"jql={jql!r} | {len(issues)}/{total}", elapsed)
        result = _table(rows, ["KEY:key", "SUMMARY:summary", "TYPE:type",
                                "STATUS:status", "PRIORITY:priority", "ASSIGNEE:assignee"])
        if total > len(issues):
            result += f"\n(showing {len(issues)} of {total} — narrow JQL or increase max_results)"
        return result
    except Exception as e:
        _tool_err("search_issues", f"jql={jql!r}", e)
        raise


@mcp.tool()
def get_projects(max_results: int = 50) -> str:
    """List all Jira projects accessible to the authenticated user."""
    start = time.perf_counter()
    try:
        data     = _get("project/search", maxResults=max_results)
        projects = data.get("values", data) if isinstance(data, dict) else data
        elapsed  = time.perf_counter() - start
        rows     = [{
            "key":   p.get("key", ""),
            "name":  p.get("name", ""),
            "type":  p.get("projectTypeKey", ""),
            "style": p.get("style", ""),
            "lead":  (p.get("lead") or {}).get("displayName", "—"),
        } for p in projects]
        _tool_ok("get_projects", f"{len(rows)} project(s)", elapsed)
        return _table(rows, ["KEY:key", "NAME:name", "TYPE:type", "STYLE:style", "LEAD:lead"])
    except Exception as e:
        _tool_err("get_projects", "list", e)
        raise


@mcp.tool()
def get_transitions(issue_key: str) -> str:
    """List all available workflow transitions for a Jira issue.
    Returns the transition IDs needed by transition_issue().
    """
    start = time.perf_counter()
    try:
        data    = _get(f"issue/{issue_key}/transitions")
        trans   = data.get("transitions", [])
        elapsed = time.perf_counter() - start
        rows    = [{
            "id":       t.get("id", ""),
            "name":     t.get("name", ""),
            "to":       (t.get("to") or {}).get("name", ""),
            "category": (t.get("to") or {}).get("statusCategory", {}).get("name", ""),
        } for t in trans]
        _tool_ok("get_transitions", f"{issue_key} | {len(rows)} transition(s)", elapsed)
        return _table(rows, ["ID:id", "TRANSITION NAME:name", "TO STATUS:to", "CATEGORY:category"])
    except Exception as e:
        _tool_err("get_transitions", issue_key, e)
        raise


@mcp.tool()
def get_comments(issue_key: str) -> str:
    """Get all comments on a Jira issue, oldest first."""
    start = time.perf_counter()
    try:
        data     = _get(f"issue/{issue_key}/comment", orderBy="created")
        comments = data.get("comments", [])
        elapsed  = time.perf_counter() - start
        _tool_ok("get_comments", f"{issue_key} | {len(comments)} comment(s)", elapsed)
        if not comments:
            return f"No comments on {issue_key}."
        sep   = "─" * 60
        lines = []
        for c in comments:
            author = (c.get("author") or {}).get("displayName", "Unknown")
            date   = (c.get("created") or "")[:19].replace("T", " ")
            body   = _adf_to_text(c.get("body")).strip()
            lines += [f"[{author}]  {date}", sep, body, ""]
        return "\n".join(lines).strip()
    except Exception as e:
        _tool_err("get_comments", issue_key, e)
        raise


@mcp.tool()
def get_myself() -> str:
    """Return details of the currently authenticated Jira user."""
    start = time.perf_counter()
    try:
        me      = _get("myself")
        elapsed = time.perf_counter() - start
        _tool_ok("get_myself", me.get("accountId", ""), elapsed)
        sep = "─" * 40
        return "\n".join([
            "Current Jira user",
            sep,
            f"Name       : {me.get('displayName', '')}",
            f"Email      : {me.get('emailAddress', '')}",
            f"Account ID : {me.get('accountId', '')}",
            f"Active     : {me.get('active', '')}",
            f"Timezone   : {me.get('timeZone', '')}",
            sep,
        ])
    except Exception as e:
        _tool_err("get_myself", "", e)
        raise


@mcp.tool()
def find_users(query: str, max_results: int = 10) -> str:
    """Find Jira users by display name or email address.
    Returns accountId values needed for create_issue() and assign_issue().
    """
    start = time.perf_counter()
    try:
        users   = _get("user/search", query=query, maxResults=max_results)
        elapsed = time.perf_counter() - start
        rows    = [{
            "account_id":   u.get("accountId", ""),
            "display_name": u.get("displayName", ""),
            "email":        u.get("emailAddress", ""),
            "active":       str(u.get("active", "")),
        } for u in users]
        _tool_ok("find_users", f"query={query!r} | {len(rows)} result(s)", elapsed)
        return _table(rows, ["ACCOUNT_ID:account_id", "DISPLAY_NAME:display_name",
                              "EMAIL:email", "ACTIVE:active"])
    except Exception as e:
        _tool_err("find_users", f"query={query!r}", e)
        raise

# ── write tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def create_issue(
    project_key: str,
    summary: str,
    issue_type: str = "Task",
    description: str = "",
    priority: str = "",
    assignee_account_id: str = "",
    labels: list[str] | None = None,
) -> str:
    """
    Create a new Jira issue.
    issue_type : Bug | Task | Story | Epic | Sub-task  (project-dependent)
    priority   : Highest | High | Medium | Low | Lowest
    assignee_account_id : use find_users() to resolve the accountId
    """
    start: float = time.perf_counter()
    fields: dict[str, Any] = {
        "project":   {"key": project_key},
        "summary":   summary,
        "issuetype": {"name": issue_type},
    }
    if description:
        fields["description"] = _description_field(description)
    if priority:
        fields["priority"] = {"name": priority}
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if labels:
        fields["labels"] = labels
    try:
        result  = _post("issue", {"fields": fields})
        elapsed = time.perf_counter() - start
        key     = result.get("key", "?")
        _tool_ok("create_issue", f"{project_key} → {key}", elapsed)
        return f"Created: {key}  ({JIRA_BASE_URL}/browse/{key})"
    except Exception as e:
        _tool_err("create_issue", project_key, e)
        raise


@mcp.tool()
def update_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    priority: str = "",
    assignee_account_id: str = "",
    labels: list[str] | None = None,
) -> str:
    """
    Update one or more fields on an existing Jira issue.
    Only pass fields you want to change; omitted fields are left untouched.
    To clear assignee pass assignee_account_id="unassigned".
    """
    start: float = time.perf_counter()
    fields: dict[str, Any] = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = _description_field(description)
    if priority:
        fields["priority"] = {"name": priority}
    if assignee_account_id:
        account_id = None if assignee_account_id == "unassigned" else assignee_account_id
        fields["assignee"] = {"accountId": account_id}
    if labels is not None:
        fields["labels"] = labels
    if not fields:
        return "Nothing to update — pass at least one field."
    try:
        _put(f"issue/{issue_key}", {"fields": fields})
        elapsed = time.perf_counter() - start
        changed = ", ".join(fields.keys())
        _tool_ok("update_issue", f"{issue_key} | changed={changed}", elapsed)
        return f"Updated {issue_key}: {changed}"
    except Exception as e:
        _tool_err("update_issue", issue_key, e)
        raise


@mcp.tool()
def add_comment(issue_key: str, comment: str) -> str:
    """
    Add a plain-text comment to a Jira issue.

    Use this tool when the comment is plain prose with no Markdown formatting.
    Use add_comment_markdown instead when the input contains formatting such as
    headers, bold/italic text, bullet lists, code blocks, or tables.
    """
    start = time.perf_counter()
    try:
        result  = _post(f"issue/{issue_key}/comment", {"body": _description_field(comment)})
        elapsed = time.perf_counter() - start
        cid     = result.get("id", "?")
        _tool_ok("add_comment", f"{issue_key} → comment {cid}", elapsed)
        return f"Comment {cid} added to {issue_key}."
    except Exception as e:
        _tool_err("add_comment", issue_key, e)
        raise


@mcp.tool()
def add_comment_markdown(issue_key: str, comment: str) -> str:
    """
    Add a richly formatted comment to a Jira issue using Markdown syntax.

    Use this tool whenever the comment contains any Markdown formatting.
    Use add_comment for plain prose that has no formatting at all.

    Supported Markdown:
      Headings      : # H1  ##  H2  …  ###### H6
      Emphasis      : **bold**  *italic*  ***bold italic***  ~~strikethrough~~
      Inline code   : `code`
      Code blocks   : ```language\\ncode\\n```  (language tag is optional)
      Lists         : - bullet  /  1. ordered  (nested lists supported)
      Task lists    : - [ ] todo  /  - [x] done
      Blockquotes   : > quoted text  (may be nested)
      Tables        : | Col A | Col B |\\n|---|---|\\n| val | val |
      Links         : [label](https://url "optional title")
      Images        : ![alt](https://url)  → rendered as a hyperlinked label
      Horizontal rule: ---  or  ***
      Hard line break: two trailing spaces or \\\\n

    Note: requires Jira API v3 (Jira Cloud). Has no effect on Server/DC (v2).
    """
    start = time.perf_counter()
    try:
        if JIRA_API_VER != "3":
            # API v2 doesn't accept ADF; fall back to plain text
            body: Any = comment
        else:
            body = markdown_to_adf(comment)
        result  = _post(f"issue/{issue_key}/comment", {"body": body})
        elapsed = time.perf_counter() - start
        cid     = result.get("id", "?")
        _tool_ok("add_comment_markdown", f"{issue_key} → comment {cid}", elapsed)
        return f"Comment {cid} added to {issue_key}."
    except Exception as e:
        _tool_err("add_comment_markdown", issue_key, e)
        raise


@mcp.tool()
def transition_issue(issue_key: str, transition_id: str, comment: str = "") -> str:
    """
    Move a Jira issue to a new status via a workflow transition.
    Use get_transitions(issue_key) first to list available transition IDs.
    """
    start: float = time.perf_counter()
    body: dict[str, Any] = {"transition": {"id": transition_id}}
    if comment:
        body["update"] = {
            "comment": [{"add": {"body": _description_field(comment)}}]
        }
    try:
        _post(f"issue/{issue_key}/transitions", body)
        elapsed = time.perf_counter() - start
        _tool_ok("transition_issue", f"{issue_key} → transition {transition_id}", elapsed)
        return f"Transitioned {issue_key} via transition {transition_id}."
    except Exception as e:
        _tool_err("transition_issue", issue_key, e)
        raise


@mcp.tool()
def assign_issue(issue_key: str, account_id: str) -> str:
    """
    Assign a Jira issue to a user.
    Use find_users() to look up the accountId.
    Pass account_id="" to unassign (sets to default assignee on some projects).
    """
    start = time.perf_counter()
    try:
        _put(f"issue/{issue_key}/assignee", {"accountId": account_id or None})
        elapsed = time.perf_counter() - start
        _tool_ok("assign_issue", f"{issue_key} → {account_id or 'unassigned'}", elapsed)
        return f"Assigned {issue_key} to {account_id or 'unassigned'}."
    except Exception as e:
        _tool_err("assign_issue", issue_key, e)
        raise

# ── middleware ─────────────────────────────────────────────────────────────────
# Pure ASGI middleware — avoids BaseHTTPMiddleware which buffers the response
# body and breaks the SSE stream used by MCP Streamable HTTP.
#
# Normalises the Accept header: FastMCP's streamable-HTTP handler requires
# "text/event-stream" and "application/json" and returns 406 without them.
# Claude Code omits these, so we inject them before the request reaches FastMCP.

class _ApiKeyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            header_list = list(scope.get("headers", []))
            headers     = {k.lower(): v for k, v in header_list}
            client      = (scope.get("client") or ("unknown",))[0]
            method      = scope.get("method", "")
            path        = scope.get("path", "")

            if MCP_API_KEY:
                key = (
                    headers.get(b"x-api-key", b"").decode()
                    or headers.get(b"authorization", b"").decode().removeprefix("Bearer ").strip()
                )
                if key != MCP_API_KEY:
                    _log("AUTH ", f"REJECTED | client={client} | {method} {path}", logging.WARNING)
                    await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)
                    return

            accept_raw = headers.get(b"accept", b"").decode()
            _log("AUTH ", f"accepted | client={client} | {method} {path} | accept={accept_raw!r}")

            required = {"application/json", "text/event-stream"}
            existing = {p.split(";")[0].strip() for p in accept_raw.split(",") if p.strip()}
            if not required.issubset(existing):
                merged      = ", ".join(sorted(existing | required))
                header_list = [(k, v) for k, v in header_list if k.lower() != b"accept"]
                header_list.append((b"accept", merged.encode()))
                scope = {**scope, "headers": header_list}
                _log("AUTH ", f"Accept header patched → {merged!r}")

        await self.app(scope, receive, send)

# ── entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = _ApiKeyMiddleware(mcp.streamable_http_app())
    _log("CONN ", f"Starting jira-mcp on 0.0.0.0:{MCP_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
