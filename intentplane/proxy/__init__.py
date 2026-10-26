"""intentplane as an MCP proxy: put the gateway between any MCP client and the MCP servers it uses.

    intentplane-mcp --config intentplane.toml

The client (Claude Desktop, Claude Code, Cursor, ...) sees the downstream servers' tools. Every call to a
configured tool becomes a proposal: its critical arguments are resolved against the mandate and the
registries of the config, the Cedar policy decides, a step-up is confirmed by the user through MCP
elicitation, and only then does the proxy call the downstream tool, with the resolved values.
"""
from .config import ProxyConfig, load_config
from .gate import GateResult, ToolGate

__all__ = ["GateResult", "ProxyConfig", "ToolGate", "load_config"]
