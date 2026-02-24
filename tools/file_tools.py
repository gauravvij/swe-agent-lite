"""File and code manipulation tools for the SWE agent."""
import ast
import difflib
import json
import os
import re
import subprocess
import tempfile
from typing import Optional


def read_file(path: str, max_lines: int = 200) -> str:
    """Read a file and return its contents (truncated if needed)."""
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            half = max_lines // 2
            content = "".join(lines[:half])
            content += f"\n... [truncated {len(lines) - max_lines} lines] ...\n"
            content += "".join(lines[-half:])
        else:
            content = "".join(lines)
        return content
    except Exception as e:
        return f"ERROR reading {path}: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"OK: wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"ERROR writing {path}: {e}"


def list_files(directory: str, extensions: Optional[list] = None, max_files: int = 100) -> str:
    """List files in a directory, optionally filtered by extension."""
    try:
        results = []
        for root, dirs, files in os.walk(directory):
            # Skip hidden dirs and common ignore dirs
            dirs[:] = [d for d in dirs if not d.startswith('.') and
                       d not in ('__pycache__', 'node_modules', '.git', 'venv', 'env',
                                 '.tox', 'dist', 'build', 'egg-info')]
            for f in files:
                if extensions is None or any(f.endswith(e) for e in extensions):
                    rel = os.path.relpath(os.path.join(root, f), directory)
                    results.append(rel)
                    if len(results) >= max_files:
                        break
            if len(results) >= max_files:
                results.append(f"... (truncated at {max_files} files)")
                break
        return "\n".join(results) if results else "(no files found)"
    except Exception as e:
        return f"ERROR listing {directory}: {e}"


def grep_search(pattern: str, directory: str, extensions: Optional[list] = None,
                max_results: int = 30) -> str:
    """Search for a pattern in files under a directory."""
    try:
        cmd = ["grep", "-rn", "--include=*.py" if extensions is None else "",
               "-m", "5", pattern, directory]
        if extensions:
            include_args = []
            for ext in extensions:
                include_args += [f"--include=*{ext}"]
            cmd = ["grep", "-rn"] + include_args + ["-m", "5", pattern, directory]
        else:
            cmd = ["grep", "-rn", "--include=*.py", "-m", "5", pattern, directory]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        lines = result.stdout.strip().split("\n")[:max_results]
        return "\n".join(lines) if lines else "(no matches)"
    except subprocess.TimeoutExpired:
        return "ERROR: grep timed out"
    except Exception as e:
        return f"ERROR grepping: {e}"


def get_file_ast_summary(path: str) -> str:
    """Return a structural summary of a Python file using AST."""
    try:
        with open(path, "r", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source)
        summary_lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)]
                summary_lines.append(f"class {node.name} (line {node.lineno}): {', '.join(methods[:8])}")
            elif isinstance(node, ast.FunctionDef) and not any(
                    isinstance(p, ast.ClassDef) for p in ast.walk(tree) if hasattr(p, 'body') and node in getattr(p, 'body', [])):
                summary_lines.append(f"def {node.name} (line {node.lineno})")
        return "\n".join(summary_lines[:40]) if summary_lines else "(empty or no classes/functions)"
    except SyntaxError as e:
        return f"SYNTAX ERROR: {e}"
    except Exception as e:
        return f"ERROR: {e}"


def apply_patch(repo_dir: str, patch_content: str) -> tuple[bool, str]:
    """Apply a unified diff patch to a repository directory."""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as tf:
            tf.write(patch_content)
            patch_file = tf.name
        result = subprocess.run(
            ["patch", "-p1", "--dry-run", "-i", patch_file],
            capture_output=True, text=True, cwd=repo_dir, timeout=30
        )
        if result.returncode != 0:
            os.unlink(patch_file)
            return False, f"Patch dry-run failed:\n{result.stderr}"
        result = subprocess.run(
            ["patch", "-p1", "-i", patch_file],
            capture_output=True, text=True, cwd=repo_dir, timeout=30
        )
        os.unlink(patch_file)
        if result.returncode == 0:
            return True, result.stdout
        return False, f"Patch apply failed:\n{result.stderr}"
    except Exception as e:
        return False, f"ERROR applying patch: {e}"


def generate_diff(original: str, modified: str, filename: str) -> str:
    """Generate a unified diff between original and modified content."""
    orig_lines = original.splitlines(keepends=True)
    mod_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines, mod_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm=""
    )
    return "".join(diff)


def find_relevant_files(repo_dir: str, keywords: list[str], max_files: int = 10) -> list[str]:
    """Find Python files relevant to given keywords using grep."""
    relevant = {}
    for kw in keywords[:5]:
        try:
            cmd = ["grep", "-rl", "--include=*.py", kw, repo_dir]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            for path in result.stdout.strip().split("\n"):
                if path and os.path.isfile(path):
                    relevant[path] = relevant.get(path, 0) + 1
        except Exception:
            pass
    # Sort by relevance score descending
    sorted_files = sorted(relevant.items(), key=lambda x: x[1], reverse=True)
    return [f for f, _ in sorted_files[:max_files]]
