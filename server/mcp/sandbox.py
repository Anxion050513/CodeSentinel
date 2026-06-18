"""Docker-based secure code execution sandbox.

Copied and adapted from the interview system's server/mcp/sandbox.py.
Used here to run SAST tools (bandit, semgrep) and tests to verify
LLM findings — reducing false positives in code review.
"""
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass


@dataclass
class ExecutionResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: float


class CodeSandbox:
    """Executes analysis tools in an isolated Docker container."""

    def __init__(
        self,
        image: str = "python:3.12-slim",
        memory_limit: str = "256m",
        cpu_limit: str = "1",
        timeout: int = 30,
    ):
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.timeout = timeout

    async def execute(
        self, code: str, language: str = "python", timeout: int | None = None
    ) -> ExecutionResult:
        """Execute a command in a Docker sandbox.

        Args:
            code: Source code or shell command to execute.
            language: 'python' or 'shell' — determines the execution mode.
            timeout: Max execution time in seconds.

        Returns:
            ExecutionResult with stdout, stderr, exit_code.
        """
        timeout = timeout or self.timeout
        exec_id = str(uuid.uuid4())[:8]

        # Write code to temp file
        suffix = ".py" if language == "python" else ".sh"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, prefix=f"code_{exec_id}_", delete=False
        ) as f:
            f.write(code)
            code_path = f.name

        try:
            import time
            start = time.time()

            # Run in Docker with security constraints
            if language == "python":
                cmd = [
                    "docker", "run", "--rm",
                    "--network", "none",
                    "--memory", self.memory_limit,
                    "--cpus", self.cpu_limit,
                    "--read-only",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                    "-v", f"{code_path}:/code/user.py:ro",
                    self.image,
                    "python", "/code/user.py",
                ]
            else:  # shell
                cmd = [
                    "docker", "run", "--rm",
                    "--network", "none",
                    "--memory", self.memory_limit,
                    "--cpus", self.cpu_limit,
                    "--read-only",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                    "-v", f"{code_path}:/code/run.sh:ro",
                    self.image,
                    "bash", "/code/run.sh",
                ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            elapsed = (time.time() - start) * 1000

            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout[:10000],  # Truncate
                stderr=result.stderr[:5000],
                exit_code=result.returncode,
                execution_time_ms=round(elapsed, 1),
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Execution timed out after {timeout}s",
                exit_code=-1,
                execution_time_ms=timeout * 1000,
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Docker is not available. Please ensure Docker is installed and running.",
                exit_code=-2,
                execution_time_ms=0,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Sandbox error: {e}",
                exit_code=-3,
                execution_time_ms=0,
            )
        finally:
            # Cleanup temp file
            try:
                os.unlink(code_path)
            except OSError:
                pass


# Singleton
sandbox = CodeSandbox()
