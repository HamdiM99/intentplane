# intentplane-mcp with the demo configuration (examples/mcp): the proxy starts, launches the demo bank-and-mail
# MCP server, and answers MCP introspection (tools/list) over stdio. Used by MCP directories (e.g. Glama) to check
# that the server runs; for real use, mount your own configuration and run with --config.
#
#   docker build -t intentplane-mcp .
#   docker run -i --rm intentplane-mcp
#   docker run -i --rm -v /path/to/config:/config intentplane-mcp --config /config/intentplane.toml
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN pip install "intentplane-mcp==0.5.1"
COPY examples/mcp /app/demo

ENTRYPOINT ["intentplane-mcp"]
CMD ["--config", "/app/demo/intentplane.toml"]
