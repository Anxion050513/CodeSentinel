"""MCP server entry point — exposes SAST tools as an MCP server.

This allows the code review system to be used as an MCP tool server,
e.g., integrated with Claude Code or other MCP clients.
"""
import json
import logging
import sys

logger = logging.getLogger(__name__)


def serve():
    """Run a minimal MCP server over stdin/stdout (JSON-RPC).

    This allows the sandbox tools to be called from MCP-compatible clients.
    """
    from server.mcp.tools import sast

    tools = {
        "bandit": sast.run_bandit,
        "semgrep": sast.run_semgrep,
        "run_tests": sast.run_tests,
    }

    logger.info("MCP server starting with tools: %s", list(tools.keys()))

    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method", "")
            req_id = request.get("id")

            if method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": name,
                                "description": f"Run {name} security/code analysis",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "code": {"type": "string", "description": "Code to analyze"},
                                    },
                                    "required": ["code"],
                                },
                            }
                            for name in tools
                        ]
                    },
                }
            elif method == "tools/call":
                tool_name = request.get("params", {}).get("name", "")
                arguments = request.get("params", {}).get("arguments", {})

                tool = tools.get(tool_name)
                if not tool:
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                    }
                else:
                    import asyncio
                    code = arguments.get("code", "")
                    result = asyncio.run(tool(code))
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{
                                "type": "text",
                                "text": json.dumps({
                                    "success": result.success,
                                    "stdout": result.stdout,
                                    "stderr": result.stderr,
                                    "exit_code": result.exit_code,
                                }),
                            }],
                        },
                    }
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                }

            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error("MCP server error: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()
