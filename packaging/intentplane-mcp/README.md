# intentplane-mcp

<!-- mcp-name: io.github.HamdiM99/intentplane -->

**An MCP proxy that puts a deterministic gateway in front of the tools your agent uses.**

`intentplane-mcp` sits between an MCP client (Claude Desktop, Claude Code, Cursor, ...) and the MCP servers it
uses, without changing either. Every call to a configured tool is decided first: its critical arguments
(payee, amount, recipients) are resolved against a signed mandate and the user's own registries, a Cedar policy
decides, a step-up is confirmed by the user through MCP elicitation, and only then is the downstream tool
called, with the trusted values. Unlisted tools are hidden from the agent and refused.

```bash
uvx intentplane-mcp --config intentplane.toml
# or: pip install intentplane-mcp && intentplane-mcp --config intentplane.toml
```

A tool is declared once in the TOML config:

```toml
[[tools]]
server = "bank"
tool = "send_money"
budget_param = "amount"
[tools.params.recipient]
critical = true
sources = ["registry:beneficiaries"]   # or "mandate:<claim>"
normalize = "iban"
[tools.params.amount]
type = "number"
scale = 100                            # the Cedar policy and the budget see cents
```

Register it in a client, for example:

```bash
claude mcp add intentplane -- uvx intentplane-mcp --config /path/to/intentplane.toml
```

A complete demo (a bank-and-mail server whose inbox holds a "we changed banks" email), the configuration
reference and the evaluation are in the repository: https://github.com/HamdiM99/intentplane

Research prototype, MIT licence.
