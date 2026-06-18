"""MCP tools — SAST runners for verifying review findings.

Each tool corresponds to a category of findings that can be
automatically verified in the Docker sandbox.
"""
import logging

from server.mcp.sandbox import sandbox, ExecutionResult

logger = logging.getLogger(__name__)


class SASTTools:
    """Static analysis tools for verifying code review findings."""

    async def run_bandit(self, code: str) -> ExecutionResult:
        """Run bandit (Python security linter) on the given code."""
        bandit_script = f"""#!/bin/bash
pip install -q bandit 2>/dev/null
cat > /tmp/target.py << 'PYEOF'
{code}
PYEOF
bandit -f json /tmp/target.py 2>&1
"""
        return await sandbox.execute(bandit_script, language="shell", timeout=20)

    async def run_semgrep(self, code: str, pattern: str = "") -> ExecutionResult:
        """Run semgrep on the given code with an optional pattern."""
        semgrep_script = f"""#!/bin/bash
pip install -q semgrep 2>/dev/null
cat > /tmp/target.py << 'PYEOF'
{code}
PYEOF
"""
        if pattern:
            semgrep_script += f"""
semgrep --lang python --pattern '{pattern}' /tmp/target.py 2>&1
"""
        else:
            semgrep_script += """
semgrep --config auto /tmp/target.py 2>&1
"""
        return await sandbox.execute(semgrep_script, language="shell", timeout=30)

    async def run_tests(self, test_file: str) -> ExecutionResult:
        """Run a test file in the sandbox."""
        test_script = f"""#!/bin/bash
pip install -q pytest 2>/dev/null
cat > /tmp/test_target.py << 'PYEOF'
{test_file}
PYEOF
pytest /tmp/test_target.py -v 2>&1
"""
        return await sandbox.execute(test_script, language="shell", timeout=30)


# Singleton
sast = SASTTools()
