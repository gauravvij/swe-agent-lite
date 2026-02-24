"""Prompt templates for the SWE agent."""

SYSTEM_PROMPT = """You are an expert software engineer solving GitHub issues. 
You will be given a problem statement and relevant code context.
Your task is to produce a minimal, correct git patch (unified diff) that fixes the issue.

RULES:
1. Produce ONLY valid unified diff patches - no explanations outside the diff block
2. Make minimal changes - fix only what's needed
3. The patch must be syntactically correct Python
4. Use exact file paths as shown in the repository
5. Always include context lines in the diff (3 lines before/after changes)
"""

REACT_SYSTEM_PROMPT = """You are an expert software engineer solving GitHub issues using a ReAct loop.
You have access to tools and should think step by step.

Available tools:
- read_file(path): Read a file's contents
- list_files(dir): List files in a directory  
- grep_search(pattern, dir): Search for pattern in code
- get_ast_summary(path): Get structural summary of Python file
- finish(patch): Submit final unified diff patch

Format each step as:
Thought: <your reasoning>
Action: <tool_name>(<args>)
Observation: <tool result>
...repeat...
Thought: I now have enough information to write the fix.
Action: finish(<unified_diff_patch>)
"""

PLAN_SOLVE_SYSTEM_PROMPT = """You are an expert software engineer. Solve GitHub issues in two phases:

PHASE 1 - PLAN: Analyze the issue and identify:
1. Root cause of the bug/feature request
2. Which files need modification
3. What changes are needed

PHASE 2 - SOLVE: Write the exact unified diff patch.

Always output your final patch in a ```diff ... ``` code block.
"""

SINGLE_SHOT_PROMPT = """Fix the following GitHub issue by providing a unified diff patch.

Repository: {repo}
Issue Title: {title}
Problem Statement:
{problem_statement}

Relevant Code Context:
{code_context}

Instructions:
- Provide ONLY a unified diff in ```diff format
- Make minimal changes to fix the issue
- Include proper file paths (a/path/to/file b/path/to/file)
- Include 3 lines of context around changes
"""

REACT_USER_TEMPLATE = """Solve this GitHub issue:

Repository: {repo}
Issue: {problem_statement}

Repository structure (key files):
{file_listing}

Start by reading the most relevant files, then provide your fix.
"""

PLAN_SOLVE_USER_TEMPLATE = """Fix this GitHub issue:

Repository: {repo}
Issue: {problem_statement}

Hint - potentially relevant files:
{relevant_files}

Code snippets from grep:
{grep_context}

Now produce a unified diff patch to fix this issue."""


def extract_patch_from_response(response: str) -> str:
    """Extract a unified diff patch from an LLM response."""
    import re

    # Try ```diff ... ``` blocks first
    m = re.search(r"```diff\s*(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Try ``` ... ``` blocks that look like diffs
    m = re.search(r"```\s*(---\s+.*?)\s*```", response, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Try to find raw diff content
    lines = response.split("\n")
    diff_lines = []
    in_diff = False
    for line in lines:
        if line.startswith("--- ") or line.startswith("diff --git"):
            in_diff = True
        if in_diff:
            diff_lines.append(line)
            # Stop at end of diff
            if line.strip() == "" and diff_lines and not diff_lines[-2].startswith("+") and \
               not diff_lines[-2].startswith("-") and len(diff_lines) > 5:
                break

    if diff_lines:
        return "\n".join(diff_lines).strip()

    # Return everything if it contains diff markers
    if "@@" in response and ("---" in response or "+++" in response):
        return response.strip()

    return ""


def build_code_context(repo_dir: str, relevant_files: list[str], max_chars: int = 8000) -> str:
    """Build a code context string from relevant files."""
    import os
    context_parts = []
    total_chars = 0

    for fpath in relevant_files:
        if total_chars >= max_chars:
            break
        try:
            with open(fpath, "r", errors="replace") as f:
                content = f.read()
            rel_path = os.path.relpath(fpath, repo_dir)
            snippet = content[:3000] if len(content) > 3000 else content
            part = f"### File: {rel_path}\n```python\n{snippet}\n```\n"
            context_parts.append(part)
            total_chars += len(part)
        except Exception:
            pass

    return "\n".join(context_parts)
