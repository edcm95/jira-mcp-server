# jira-mcp

A local MCP (Model Context Protocol) server that exposes Jira as a set of tools for Claude Code and other MCP-compatible clients. Runs in Docker and authenticates via API token — no browser OAuth required.

Built to match the conventions of the `oracle-mcp` server in this repo: same middleware, same logging format, same deploy pattern.

---

## Features

- **12 Jira tools** covering read and write operations across issues, projects, comments, transitions, and users
- **Dual auth support**: Jira Cloud (email + API token via Basic auth) and Jira Server / Data Center (Personal Access Token via Bearer auth)
- **API v2 and v3**: defaults to v3 (ADF descriptions) for Cloud; set `JIRA_API_VERSION=2` for older Server instances
- **Optional MCP API key** to restrict access to the server
- **Accept-header middleware** that fixes the 406 response Claude Code triggers on FastMCP's streamable-HTTP transport
- Structured ASCII-table output for list operations; formatted text for detail views

---

## Tools

### Read

| Tool | Description |
|---|---|
| `get_issue(issue_key)` | Full detail for a single issue — summary, status, type, priority, assignee, reporter, dates, labels, description |
| `search_issues(jql, max_results)` | JQL search returning a table of matching issues. Max 100 per call. |
| `get_projects(max_results)` | Lists all projects accessible to the authenticated user |
| `get_transitions(issue_key)` | Lists available workflow transitions and the IDs needed by `transition_issue` |
| `get_comments(issue_key)` | All comments on an issue, oldest first, with author and timestamp |
| `get_myself()` | Returns the authenticated user's name, email, accountId, and timezone |
| `find_users(query, max_results)` | Searches users by display name or email; returns accountIds for use in assign/create |

### Write

| Tool | Description |
|---|---|
| `create_issue(project_key, summary, ...)` | Creates an issue with optional type, description, priority, assignee, and labels |
| `update_issue(issue_key, ...)` | Updates any subset of fields on an existing issue; omitted fields are untouched |
| `add_comment(issue_key, comment)` | Adds a plain-text comment |
| `transition_issue(issue_key, transition_id, comment)` | Moves an issue through a workflow transition; use `get_transitions` to find valid IDs |
| `assign_issue(issue_key, account_id)` | Assigns or unassigns an issue; use `find_users` to resolve accountIds |

---

## Setup

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# Jira Cloud
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@yourcompany.com
JIRA_API_TOKEN=your_api_token_here   # https://id.atlassian.com/manage-profile/security/api-tokens

# — or — Jira Server / Data Center
JIRA_BASE_URL=https://jira.yourcompany.com
JIRA_EMAIL=                          # leave empty to use Bearer auth
JIRA_API_TOKEN=your_personal_access_token

# Optional
JIRA_API_VERSION=3                   # 2 for older Server instances
MCP_PORT=8766
MCP_API_KEY=                         # restrict access; omit to disable
```

### 2. Deploy

```bash
./deploy.sh
```

The script builds the Docker image, starts the container, waits for the server to be ready, and prints the Claude Code configuration snippet.

### 3. Add to Claude Code

In `.claude/settings.json` (or the global settings):

```json
"mcpServers": {
  "jira": {
    "type": "http",
    "url": "http://host.docker.internal:8766/mcp"
  }
}
```

If `MCP_API_KEY` is set:

```json
"mcpServers": {
  "jira": {
    "type": "http",
    "url": "http://host.docker.internal:8766/mcp",
    "headers": {
      "X-API-Key": "your_key_here"
    }
  }
}
```

Restart Claude Code after editing settings.

---

## Useful commands

```bash
# View live logs
docker compose logs -f jira-mcp

# Stop
docker compose down

# Rebuild after code changes
docker compose up --build -d
```

---

## JQL examples

```
# My open issues, most recently updated first
assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC

# Bugs created in the last 7 days in a specific project
project = MYPROJ AND issuetype = Bug AND created >= -7d

# Everything in a sprint
sprint in openSprints() AND project = MYPROJ

# Full-text search
text ~ "payment gateway" AND status != Done
```

---

## Architecture

```
Claude Code
    │  HTTP (streamable-HTTP / SSE)
    ▼
_ApiKeyMiddleware   ← patches Accept header, enforces MCP_API_KEY
    │
    ▼
FastMCP (mcp.server.fastmcp)
    │
    ▼
Jira REST API  (/rest/api/3/...)
    │  Basic auth (Cloud) or Bearer PAT (Server/DC)
    ▼
Jira instance
```

The pure-ASGI middleware is used instead of Starlette's `BaseHTTPMiddleware` to avoid response buffering that breaks SSE streams.


Built with [Claude Code](https://claude.ai/code).
