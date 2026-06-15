#!/bin/bash

script_dir="$(cd "$(dirname "$0")" && pwd)"
env_file="$script_dir/.env"
env_example="$script_dir/.env.example"
port="${MCP_PORT:-8766}"
api_key="$(grep -E '^MCP_API_KEY=' "$script_dir/.env" 2>/dev/null | cut -d= -f2-)"

# ── preflight checks ───────────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
    echo "❌ Docker is not installed or not in PATH."
    exit 1
fi

if [[ ! -f "$env_file" ]]; then
    echo "⚠️  No .env file found. Creating one from .env.example..."
    cp "$env_example" "$env_file"
    echo ""
    echo "  ➜ Fill in JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN in:"
    echo "    $env_file"
    echo ""
    echo "  Then re-run this script."
    exit 1
fi

if grep -qE '^JIRA_(BASE_URL|API_TOKEN)=$' "$env_file"; then
    echo "⚠️  JIRA_BASE_URL or JIRA_API_TOKEN is empty in .env — the server will fail to connect."
    echo "   Edit $env_file before continuing."
    echo ""
fi

# ── build & deploy ─────────────────────────────────────────────────────────────

echo "🔨 Building and starting jira-mcp service..."
cd "$script_dir"
MCP_PORT="$port" docker compose up --build -d

if [[ $? -ne 0 ]]; then
    echo "❌ docker compose up failed."
    exit 1
fi

# ── wait for readiness ─────────────────────────────────────────────────────────

echo "⏳ Waiting for server to be ready..."
auth_header=()
if [[ -n "$api_key" ]]; then
    auth_header=(-H "X-API-Key: $api_key")
fi
for i in $(seq 1 15); do
    status=$(curl -o /dev/null -s -w "%{http_code}" --max-time 2 -X POST "http://localhost:$port/mcp" \
             -H "Content-Type: application/json" "${auth_header[@]}" -d '{}' 2>/dev/null)
    if [[ "$status" == "400" || "$status" == "200" ]]; then
        echo "✅ Server is up."
        break
    fi
    sleep 1
    if [[ $i -eq 15 ]]; then
        echo "⚠️  Server did not respond within 15s. Check logs:"
        echo "   docker compose logs jira-mcp"
    fi
done

echo ""
echo "──────────────────────────────────────────────────────────────────"
echo " Service   : jira-mcp"
echo " MCP URL   : http://localhost:$port/mcp"
echo " Logs      : docker compose -f $script_dir/docker-compose.yml logs -f jira-mcp"
echo " Stop      : docker compose -f $script_dir/docker-compose.yml down"
echo ""
echo " .claude/settings.json (Claude Code on this machine):"
echo ""
echo '  "mcpServers": {'
echo '    "jira": {'
echo '      "type": "http",'
echo "      \"url\": \"http://host.docker.internal:$port/mcp\""
echo '    }'
echo '  }'
echo ""
echo " Restart Claude Code to activate."
echo "──────────────────────────────────────────────────────────────────"
