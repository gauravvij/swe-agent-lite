"""Retry failed instances using improved prompts and patch extraction."""
import json
import logging
import os
import re
import sys
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/app/swe_agent_benchmark_0958")
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(handlers=[logging.StreamHandler(sys.stdout)], level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from agent.llm_client import LLMClient
from agent.core_agent import SWEAgent
from tools.file_tools import find_relevant_files, read_file, grep_search
from utils.config import PATCHES_DIR, ANALYSIS_DIR, DATA_DIR


RETRY_SYSTEM = """You are an expert Python developer. Your ONLY task is to output a unified diff patch.

CRITICAL: You MUST output ONLY a ```diff block. No explanations. No prose. Just the diff.

Format:
```diff
--- a/path/to/file.py
+++ b/path/to/file.py
@@ -LINE,COUNT +LINE,COUNT @@
 context line
-old line to remove
+new line to add
 context line
```
"""

RETRY_USER = """Fix this GitHub issue. Output ONLY a unified diff patch in ```diff format.

Issue in {repo}:
{problem}

Relevant file: {filepath}
```python
{file_content}
```

Output ONLY the ```diff block now:"""


def aggressive_patch_extract(text: str) -> str:
    """Very aggressive patch extraction trying multiple patterns."""
    if not text:
        return ""

    # Pattern 1: ```diff block
    m = re.search(r'```diff\s*\n(.*?)```', text, re.DOTALL)
    if m:
        p = m.group(1).strip()
        if '@@' in p and ('---' in p or '+++' in p):
            return p

    # Pattern 2: Any ``` block with diff markers
    for m in re.finditer(r'```[a-z]*\s*\n(.*?)```', text, re.DOTALL):
        p = m.group(1).strip()
        if '@@' in p and '---' in p and '+++' in p:
            return p

    # Pattern 3: Raw diff in text
    lines = text.split('\n')
    diff_start = -1
    for i, line in enumerate(lines):
        if line.startswith('--- ') or line.startswith('diff --git'):
            diff_start = i
            break

    if diff_start >= 0:
        diff_lines = lines[diff_start:]
        # Find end of diff
        result = []
        for line in diff_lines:
            if line.strip() == '' and result and len(result) > 5:
                # Check if next meaningful line is diff-related
                pass
            result.append(line)
        patch = '\n'.join(result).strip()
        if '@@' in patch:
            return patch

    return ""


def solve_with_retry(instance: dict, repo_path: str, llm: LLMClient) -> str:
    """Solve a single instance with improved prompting for patch output."""
    problem = instance.get("problem_statement", "")
    repo = instance.get("repo", "")

    # Find relevant files
    keywords = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b', problem)
    stop = {'the', 'this', 'that', 'with', 'from', 'when', 'have', 'been', 'will', 'would'}
    keywords = [w for w in keywords if w.lower() not in stop]
    keywords = sorted(set(keywords), key=len, reverse=True)[:8]

    relevant_files = find_relevant_files(repo_path, keywords, max_files=3)
    if not relevant_files:
        # Fallback: list all py files and use first few
        import subprocess
        result = subprocess.run(['find', repo_path, '-name', '*.py', '-not', '-path', '*test*',
                                '-not', '-path', '*__pycache__*'], capture_output=True, text=True)
        all_files = result.stdout.strip().split('\n')[:3]
        relevant_files = [f for f in all_files if f and os.path.isfile(f)]

    for fpath in relevant_files[:2]:
        try:
            rel = os.path.relpath(fpath, repo_path)
            with open(fpath, 'r', errors='replace') as f:
                content = f.read()[:4000]

            messages = [
                {"role": "system", "content": RETRY_SYSTEM},
                {"role": "user", "content": RETRY_USER.format(
                    repo=repo,
                    problem=problem[:2000],
                    filepath=rel,
                    file_content=content,
                )}
            ]
            response, _ = llm.chat(messages, max_tokens=3000, temperature=0.0)
            patch = aggressive_patch_extract(response)
            if patch and '@@' in patch:
                return patch
        except Exception as e:
            logger.debug(f"Retry attempt failed for {fpath}: {e}")

    # Last resort: ask for minimal single-line fix
    messages = [
        {"role": "system", "content": RETRY_SYSTEM},
        {"role": "user", "content": (
            f"Fix this bug in {repo}:\n{problem[:1500]}\n\n"
            "Provide a minimal unified diff. Output ONLY ```diff block:"
        )}
    ]
    try:
        response, _ = llm.chat(messages, max_tokens=2000)
        return aggressive_patch_extract(response)
    except Exception:
        return ""


def run_retry(max_workers: int = 3):
    """Load failed instances and retry them with improved approach."""
    # Load current results
    results_path = os.path.join(ANALYSIS_DIR, "full_results.json")
    with open(results_path) as f:
        data = json.load(f)

    results = data["results"]
    failed = [r for r in results if not r.get("patch") or len(r.get("patch", "")) < 20]
    logger.info(f"Retrying {len(failed)} failed instances...")

    # Load instances for problem statements
    with open(os.path.join(DATA_DIR, "swebench_lite_test.json")) as f:
        all_instances = json.load(f)
    inst_map = {i["instance_id"]: i for i in all_instances}

    work_dir = "/tmp/swe_repos"
    llm = LLMClient()

    # Process in parallel
    retry_results = {}

    def retry_one(failed_result):
        iid = failed_result["instance_id"]
        inst = inst_map.get(iid, {})
        if not inst:
            return iid, ""
        repo = inst.get("repo", "")
        repo_name = repo.replace("/", "__")
        repo_path = os.path.join(work_dir, repo_name)

        # Clone if needed
        if not os.path.exists(os.path.join(repo_path, ".git")):
            import subprocess
            os.makedirs(repo_path, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth=1", f"https://github.com/{repo}.git", repo_path],
                capture_output=True, timeout=120
            )

        if not os.path.exists(os.path.join(repo_path, ".git")):
            return iid, ""

        patch = solve_with_retry(inst, repo_path, llm)
        return iid, patch

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(retry_one, r): r for r in failed}
        done = 0
        for future in as_completed(futures):
            iid, patch = future.result()
            retry_results[iid] = patch
            done += 1
            status = "✓" if patch else "✗"
            logger.info(f"[{done}/{len(failed)}] {status} {iid}")
            if patch:
                patch_file = os.path.join(PATCHES_DIR, f"{iid}.patch")
                with open(patch_file, "w") as f:
                    f.write(patch)

    # Merge retry results into main results
    improved = 0
    for r in results:
        iid = r["instance_id"]
        if iid in retry_results and retry_results[iid]:
            r["patch"] = retry_results[iid]
            r["success"] = True
            r["error"] = None
            improved += 1

    logger.info(f"\nRetry improved: {improved}/{len(failed)} previously failed instances")

    # Save updated results
    data["results"] = results
    data["llm_stats"] = llm.get_usage_stats()
    with open(results_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return results, improved


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()

    results, improved = run_retry(max_workers=args.workers)

    # Recompute metrics
    sys.path.insert(0, "/app/swe_agent_benchmark_0958")
    from agent.evaluator import compute_pass_at_1, generate_evaluation_report
    from agent.experiment import compute_strategy_metrics
    from data.pipeline import load_cached_instances

    instances = load_cached_instances()
    eval_metrics = compute_pass_at_1(results, instances)
    strategy_metrics = {"plan_solve+retry": compute_strategy_metrics(results)}

    with open(os.path.join(DATA_DIR, "swebench_lite_test.json")) as f:
        all_instances = json.load(f)

    llm_stats = {
        "total_calls": 0, "total_prompt_tokens": 0,
        "total_completion_tokens": 0, "total_tokens": 0
    }
    try:
        with open(os.path.join(ANALYSIS_DIR, "full_results.json")) as f:
            d = json.load(f)
            llm_stats = d.get("llm_stats", llm_stats)
    except Exception:
        pass

    report_path = os.path.join(ANALYSIS_DIR, "evaluation_report.md")
    generate_evaluation_report(eval_metrics, strategy_metrics, llm_stats, report_path)

    print(f"\n{'='*60}")
    print(f"POST-RETRY RESULTS:")
    print(f"  Patches generated: {eval_metrics['patches_generated']}/300")
    print(f"  Valid patches: {eval_metrics['patches_valid_syntax']}/300")
    print(f"  Pass@1 (proxy): {eval_metrics['pass_at_1_pct']:.2f}%")
    print(f"{'='*60}")
