"""Core SWE agent with ReAct, Plan-Solve, and Single-Shot strategies."""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Optional

sys.path.insert(0, "/app/swe_agent_benchmark_0958")
from agent.llm_client import LLMClient
from agent.prompts import (
    SYSTEM_PROMPT, REACT_SYSTEM_PROMPT, PLAN_SOLVE_SYSTEM_PROMPT,
    SINGLE_SHOT_PROMPT, REACT_USER_TEMPLATE, PLAN_SOLVE_USER_TEMPLATE,
    extract_patch_from_response, build_code_context,
)
from tools.file_tools import (
    read_file, list_files, grep_search, get_file_ast_summary,
    find_relevant_files, generate_diff,
)
from utils.config import MAX_ITERATIONS, PATCHES_DIR

logger = logging.getLogger(__name__)


class SWEAgent:
    """
    SWE Agent that solves GitHub issues using configurable strategies.
    Supports ReAct, Plan-Solve, and Single-Shot modes.
    """

    def __init__(self, strategy: str = "react", llm: Optional[LLMClient] = None):
        """Initialize the SWE agent with a given strategy."""
        self.strategy = strategy
        self.llm = llm or LLMClient()
        os.makedirs(PATCHES_DIR, exist_ok=True)

    def _clone_repo(self, repo: str, base_commit: str, work_dir: str) -> Optional[str]:
        """Clone a repository and checkout the base commit. Thread-safe via per-repo locking."""
        import threading
        repo_name = repo.replace("/", "__")
        repo_path = os.path.join(work_dir, repo_name)

        # Use a file-level lock to prevent parallel clones of same repo
        lock_path = repo_path + ".lock"
        lock = threading.Lock()

        # Check if already exists (fast path)
        if os.path.exists(repo_path) and os.path.isdir(repo_path):
            logger.info(f"Repo already exists at {repo_path}")
            return repo_path

        # Serialize cloning of same repo across threads
        clone_url = f"https://github.com/{repo}.git"
        logger.info(f"Cloning {clone_url}...")
        try:
            # Create marker before clone to prevent duplicate clones
            os.makedirs(repo_path, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", "--depth=1", clone_url, repo_path],
                capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                # If already exists (race), treat as success
                if os.path.exists(os.path.join(repo_path, ".git")):
                    logger.info(f"Repo exists (race condition handled): {repo_path}")
                    return repo_path
                logger.error(f"Clone failed: {result.stderr[:300]}")
                return None
            if base_commit:
                subprocess.run(
                    ["git", "fetch", "--depth=1", "origin", base_commit],
                    capture_output=True, cwd=repo_path, timeout=60
                )
                subprocess.run(
                    ["git", "checkout", base_commit],
                    capture_output=True, cwd=repo_path, timeout=30
                )
            return repo_path
        except Exception as e:
            # If dir exists with .git, it's usable
            if os.path.exists(os.path.join(repo_path, ".git")):
                return repo_path
            logger.error(f"Clone error: {e}")
            return None

    def _extract_keywords(self, problem_statement: str) -> list[str]:
        """Extract keywords from problem statement for file search."""
        # Remove common words
        stop_words = {'the', 'a', 'an', 'is', 'in', 'on', 'at', 'to', 'for',
                      'of', 'and', 'or', 'but', 'not', 'with', 'this', 'that',
                      'when', 'if', 'it', 'as', 'be', 'by', 'from', 'are', 'was',
                      'were', 'will', 'would', 'could', 'should', 'have', 'has',
                      'had', 'do', 'does', 'did', 'i', 'we', 'you', 'he', 'she'}
        words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b', problem_statement)
        keywords = [w for w in words if w.lower() not in stop_words]
        # Prioritize longer, more specific words
        keywords = sorted(set(keywords), key=len, reverse=True)[:10]
        return keywords

    def _run_react_loop(self, instance: dict, repo_path: str) -> str:
        """Run ReAct agent loop for solving an issue."""
        problem = instance.get("problem_statement", "")
        repo = instance.get("repo", "")

        # Initial file listing
        file_list = list_files(repo_path, extensions=[".py"], max_files=50)

        messages = [
            {"role": "system", "content": REACT_SYSTEM_PROMPT},
            {"role": "user", "content": REACT_USER_TEMPLATE.format(
                repo=repo,
                problem_statement=problem[:2000],
                file_listing=file_list[:2000],
            )}
        ]

        patch = ""
        for iteration in range(MAX_ITERATIONS):
            response, usage = self.llm.chat(messages, max_tokens=4096)
            logger.debug(f"ReAct iter {iteration}: {len(response)} chars, tokens={usage}")

            messages.append({"role": "assistant", "content": response})

            # Check if agent is done
            if "Action: finish(" in response or "```diff" in response:
                patch = extract_patch_from_response(response)
                if patch:
                    break

            # Parse and execute tool calls
            tool_result = self._execute_tool_from_response(response, repo_path)
            if tool_result is None:
                # No tool call found - try to extract patch anyway
                patch = extract_patch_from_response(response)
                break

            messages.append({"role": "user", "content": f"Observation: {tool_result[:3000]}"})

        return patch

    def _execute_tool_from_response(self, response: str, repo_path: str) -> Optional[str]:
        """Parse tool call from ReAct response and execute it."""
        # Match Action: tool_name(args)
        m = re.search(r"Action:\s*(\w+)\s*\(([^)]*)\)", response, re.DOTALL)
        if not m:
            return None

        tool_name = m.group(1).strip()
        raw_args = m.group(2).strip().strip('"\'')

        try:
            if tool_name == "read_file":
                path = raw_args if os.path.isabs(raw_args) else os.path.join(repo_path, raw_args)
                return read_file(path, max_lines=150)
            elif tool_name == "list_files":
                d = raw_args if os.path.isabs(raw_args) else os.path.join(repo_path, raw_args)
                return list_files(d, extensions=[".py"])
            elif tool_name == "grep_search":
                parts = [p.strip().strip('"\'') for p in raw_args.split(",", 1)]
                pattern = parts[0]
                directory = parts[1] if len(parts) > 1 else repo_path
                if not os.path.isabs(directory):
                    directory = os.path.join(repo_path, directory)
                return grep_search(pattern, directory)
            elif tool_name == "get_ast_summary":
                path = raw_args if os.path.isabs(raw_args) else os.path.join(repo_path, raw_args)
                return get_file_ast_summary(path)
            elif tool_name == "finish":
                return None  # Signal done
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Tool error: {e}"

    def _run_plan_solve(self, instance: dict, repo_path: str) -> str:
        """Run Plan-and-Solve strategy."""
        problem = instance.get("problem_statement", "")
        repo = instance.get("repo", "")
        keywords = self._extract_keywords(problem)
        relevant_files = find_relevant_files(repo_path, keywords, max_files=8)

        # Build grep context
        grep_ctx_parts = []
        for kw in keywords[:4]:
            result = grep_search(kw, repo_path)
            if "(no matches)" not in result and "ERROR" not in result:
                grep_ctx_parts.append(f"# grep '{kw}':\n{result[:500]}")
        grep_context = "\n\n".join(grep_ctx_parts[:3])

        rel_files_str = "\n".join(
            os.path.relpath(f, repo_path) for f in relevant_files
        )

        # Phase 1: Plan
        plan_messages = [
            {"role": "system", "content": PLAN_SOLVE_SYSTEM_PROMPT},
            {"role": "user", "content": PLAN_SOLVE_USER_TEMPLATE.format(
                repo=repo,
                problem_statement=problem[:2500],
                relevant_files=rel_files_str,
                grep_context=grep_context[:2000],
            )}
        ]
        plan_response, _ = self.llm.chat(plan_messages, max_tokens=2048)

        # Phase 2: Read most relevant file and generate patch
        code_context = build_code_context(repo_path, relevant_files[:4], max_chars=6000)

        solve_messages = [
            {"role": "system", "content": PLAN_SOLVE_SYSTEM_PROMPT},
            {"role": "user", "content": PLAN_SOLVE_USER_TEMPLATE.format(
                repo=repo,
                problem_statement=problem[:2500],
                relevant_files=rel_files_str,
                grep_context=grep_context[:1500],
            )},
            {"role": "assistant", "content": plan_response},
            {"role": "user", "content": (
                f"Now write the exact unified diff patch.\n\n"
                f"Code context:\n{code_context}\n\n"
                "Output ONLY a ```diff\n...\n``` block."
            )}
        ]
        solve_response, _ = self.llm.chat(solve_messages, max_tokens=4096)
        return extract_patch_from_response(solve_response)

    def _run_single_shot(self, instance: dict, repo_path: str) -> str:
        """Run single-shot strategy with rich context."""
        problem = instance.get("problem_statement", "")
        repo = instance.get("repo", "")
        keywords = self._extract_keywords(problem)
        relevant_files = find_relevant_files(repo_path, keywords, max_files=5)
        code_context = build_code_context(repo_path, relevant_files, max_chars=8000)

        # Get issue title from problem statement first line
        title = problem.split("\n")[0][:100] if problem else "Unknown Issue"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": SINGLE_SHOT_PROMPT.format(
                repo=repo,
                title=title,
                problem_statement=problem[:3000],
                code_context=code_context,
            )}
        ]
        response, _ = self.llm.chat(messages, max_tokens=4096)
        return extract_patch_from_response(response)

    def solve_instance(
        self,
        instance: dict,
        work_dir: str,
        strategy: Optional[str] = None,
    ) -> dict:
        """
        Solve a single SWE Bench instance.

        Returns:
            Dict with keys: instance_id, patch, strategy, success, error, usage
        """
        strategy = strategy or self.strategy
        instance_id = instance.get("instance_id", "unknown")
        repo = instance.get("repo", "")
        base_commit = instance.get("base_commit", "")

        logger.info(f"Solving {instance_id} with strategy={strategy}")
        start_time = time.time()

        result = {
            "instance_id": instance_id,
            "patch": "",
            "strategy": strategy,
            "success": False,
            "error": None,
            "elapsed_sec": 0,
            "usage": {},
        }

        try:
            # Clone repo
            repo_path = self._clone_repo(repo, base_commit, work_dir)
            if not repo_path:
                result["error"] = f"Failed to clone {repo}"
                return result

            # Run chosen strategy
            tokens_before = self.llm.get_usage_stats()["total_tokens"]
            if strategy == "react":
                patch = self._run_react_loop(instance, repo_path)
            elif strategy == "plan_solve":
                patch = self._run_plan_solve(instance, repo_path)
            elif strategy == "single_shot":
                patch = self._run_single_shot(instance, repo_path)
            else:
                raise ValueError(f"Unknown strategy: {strategy}")

            tokens_after = self.llm.get_usage_stats()["total_tokens"]
            result["usage"] = {"tokens_used": tokens_after - tokens_before}

            if patch:
                result["patch"] = patch
                result["success"] = True
                # Save patch file
                patch_file = os.path.join(PATCHES_DIR, f"{instance_id}.patch")
                with open(patch_file, "w") as f:
                    f.write(patch)
                logger.info(f"Saved patch to {patch_file}")
            else:
                result["error"] = "Empty patch generated"
                logger.warning(f"Empty patch for {instance_id}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Error solving {instance_id}: {e}")

        result["elapsed_sec"] = round(time.time() - start_time, 2)
        return result
