"""Experimentation module to compare agent strategies on SWE Bench lite subset."""
import json
import logging
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

sys.path.insert(0, "/app/swe_agent_benchmark_0958")
from agent.llm_client import LLMClient
from agent.core_agent import SWEAgent
from data.pipeline import load_cached_instances
from utils.config import PATCHES_DIR, ANALYSIS_DIR, DATA_DIR

logger = logging.getLogger(__name__)


def run_strategy_experiment(
    instances: list[dict],
    strategies: list[str],
    work_dir: str,
    llm: LLMClient,
    max_workers: int = 2,
) -> dict:
    """
    Run multiple strategies on a subset and return comparative results.

    Args:
        instances: List of SWE Bench instances
        strategies: List of strategy names to test
        work_dir: Working directory for cloned repos
        llm: Shared LLM client
        max_workers: Parallel workers for API calls

    Returns:
        Dict mapping strategy -> list of results
    """
    all_results = {s: [] for s in strategies}

    for strategy in strategies:
        logger.info(f"\n{'='*50}\nTesting strategy: {strategy}\n{'='*50}")
        agent = SWEAgent(strategy=strategy, llm=llm)

        # Run instances (parallel for API efficiency)
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(agent.solve_instance, inst, work_dir, strategy): inst
                for inst in instances
            }
            for future in as_completed(futures):
                inst = futures[future]
                try:
                    res = future.result()
                    results.append(res)
                    status = "✓" if res["success"] else "✗"
                    logger.info(f"  {status} {res['instance_id']} "
                                f"({res['elapsed_sec']}s, {res['usage'].get('tokens_used',0)} tokens)")
                except Exception as e:
                    logger.error(f"  ✗ {inst.get('instance_id','?')}: {e}")
                    results.append({
                        "instance_id": inst.get("instance_id", "?"),
                        "patch": "", "strategy": strategy,
                        "success": False, "error": str(e),
                        "elapsed_sec": 0, "usage": {}
                    })

        all_results[strategy] = results

    return all_results


def evaluate_patch_validity(patch: str, instance: dict) -> dict:
    """
    Evaluate if a generated patch is valid (syntactically well-formed).

    Returns dict with validity assessment.
    """
    if not patch or len(patch.strip()) < 10:
        return {"valid": False, "reason": "empty_patch"}

    checks = {
        "has_diff_header": "---" in patch and "+++" in patch,
        "has_hunk": "@@" in patch,
        "has_changes": any(line.startswith("+") or line.startswith("-")
                          for line in patch.split("\n")
                          if not line.startswith("---") and not line.startswith("+++")),
        "reasonable_length": 10 < len(patch) < 50000,
    }
    valid = all(checks.values())
    return {"valid": valid, "checks": checks}


def compute_strategy_metrics(results: list[dict]) -> dict:
    """Compute summary metrics for a strategy's results."""
    total = len(results)
    if total == 0:
        return {"total": 0, "patches_generated": 0, "patch_rate": 0.0,
                "avg_tokens": 0, "avg_time": 0}

    patches_generated = sum(1 for r in results if r.get("success") and r.get("patch"))
    valid_patches = sum(
        1 for r in results
        if r.get("patch") and evaluate_patch_validity(r["patch"], {})["valid"]
    )
    tokens = [r["usage"].get("tokens_used", 0) for r in results if r.get("usage")]
    times = [r["elapsed_sec"] for r in results]

    return {
        "total": total,
        "patches_generated": patches_generated,
        "valid_patches": valid_patches,
        "patch_rate": round(patches_generated / total, 3),
        "valid_patch_rate": round(valid_patches / total, 3),
        "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else 0,
        "avg_time_sec": round(sum(times) / len(times), 2) if times else 0,
        "errors": [r["error"] for r in results if r.get("error")],
    }


def select_best_strategy(experiment_results: dict) -> str:
    """Select the best strategy based on valid patch rate and token efficiency."""
    best = None
    best_score = -1
    for strategy, results in experiment_results.items():
        metrics = compute_strategy_metrics(results)
        # Score = valid_patch_rate * 0.7 + (1 - normalized_tokens) * 0.3
        token_score = max(0, 1 - metrics["avg_tokens"] / 10000) if metrics["avg_tokens"] > 0 else 0.5
        score = metrics["valid_patch_rate"] * 0.7 + token_score * 0.3
        logger.info(f"Strategy {strategy}: valid_rate={metrics['valid_patch_rate']:.2%}, "
                    f"score={score:.3f}")
        if score > best_score:
            best_score = score
            best = strategy
    return best or "plan_solve"


def run_full_evaluation(
    instances: list[dict],
    strategy: str,
    work_dir: str,
    llm: LLMClient,
    max_workers: int = 3,
    checkpoint_every: int = 10,
) -> list[dict]:
    """
    Run the best strategy across all instances with checkpointing.

    Args:
        instances: All SWE Bench lite instances
        strategy: Best strategy to use
        work_dir: Working directory
        llm: LLM client
        max_workers: Parallel API workers
        checkpoint_every: Save checkpoint every N instances

    Returns:
        List of result dicts
    """
    checkpoint_path = os.path.join(DATA_DIR, f"checkpoint_{strategy}.json")

    # Load checkpoint if exists
    completed = {}
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path) as f:
            completed = {r["instance_id"]: r for r in json.load(f)}
        logger.info(f"Resuming from checkpoint: {len(completed)} done")

    remaining = [i for i in instances if i.get("instance_id") not in completed]
    logger.info(f"Running {len(remaining)} remaining instances (strategy={strategy})")

    agent = SWEAgent(strategy=strategy, llm=llm)
    all_results = list(completed.values())

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(agent.solve_instance, inst, work_dir, strategy): inst
            for inst in remaining
        }
        done_count = 0
        for future in as_completed(futures):
            inst = futures[future]
            try:
                res = future.result()
                all_results.append(res)
                done_count += 1
                status = "✓" if res["success"] else "✗"
                logger.info(f"[{done_count}/{len(remaining)}] {status} "
                            f"{res['instance_id']} ({res['elapsed_sec']}s)")

                # Periodic validity check
                if res.get("patch"):
                    validity = evaluate_patch_validity(res["patch"], inst)
                    if not validity["valid"]:
                        logger.warning(f"  Invalid patch: {validity.get('reason', validity.get('checks'))}")
            except Exception as e:
                logger.error(f"Error on {inst.get('instance_id')}: {e}")
                all_results.append({
                    "instance_id": inst.get("instance_id", "?"),
                    "patch": "", "strategy": strategy,
                    "success": False, "error": str(e),
                    "elapsed_sec": 0, "usage": {}
                })
                done_count += 1

            # Save checkpoint
            if done_count % checkpoint_every == 0:
                with open(checkpoint_path, "w") as f:
                    json.dump(all_results, f, indent=2, default=str)
                logger.info(f"Checkpoint saved ({len(all_results)} total)")

    # Final checkpoint
    with open(checkpoint_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    return all_results
