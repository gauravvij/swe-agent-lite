"""Run strategy experiments on SWE Bench lite subset, then full evaluation."""
import json
import logging
import os
import sys
import tempfile
import time

sys.path.insert(0, "/app/swe_agent_benchmark_0958")
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    handlers=[logging.StreamHandler(sys.stdout)],
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

from agent.llm_client import LLMClient
from agent.core_agent import SWEAgent
from agent.experiment import (
    run_strategy_experiment, compute_strategy_metrics,
    select_best_strategy, run_full_evaluation, evaluate_patch_validity
)
from agent.evaluator import compute_pass_at_1, generate_evaluation_report
from data.pipeline import load_cached_instances
from utils.config import PATCHES_DIR, ANALYSIS_DIR, DATA_DIR


def test_llm_connection():
    """Quick sanity check that the LLM API is reachable."""
    logger.info("Testing LLM connection...")
    llm = LLMClient()
    response, usage = llm.chat([
        {"role": "user", "content": "Say 'OK' and nothing else."}
    ], max_tokens=10)
    logger.info(f"LLM response: '{response}' | tokens: {usage}")
    return llm, response


def run_pilot(instances, llm, work_dir, n=6):
    """Run pilot experiment comparing strategies on N instances."""
    subset = instances[:n]
    logger.info(f"\n{'='*60}")
    logger.info(f"PILOT: Testing strategies on {n} instances")
    logger.info(f"{'='*60}")

    strategies = ["single_shot", "plan_solve"]
    results = run_strategy_experiment(
        subset, strategies, work_dir, llm, max_workers=2
    )

    metrics = {}
    for strategy, res in results.items():
        m = compute_strategy_metrics(res)
        metrics[strategy] = m
        logger.info(f"\nStrategy: {strategy}")
        logger.info(f"  Patches generated: {m['patches_generated']}/{m['total']}")
        logger.info(f"  Valid patches: {m['valid_patches']}/{m['total']} ({m['valid_patch_rate']:.1%})")
        logger.info(f"  Avg tokens: {m['avg_tokens']:.0f}")
        logger.info(f"  Avg time: {m['avg_time_sec']:.1f}s")

    best = select_best_strategy(results)
    logger.info(f"\nBest strategy selected: {best}")

    # Save pilot results
    os.makedirs(ANALYSIS_DIR, exist_ok=True)
    pilot_path = os.path.join(ANALYSIS_DIR, "pilot_results.json")
    with open(pilot_path, "w") as f:
        json.dump({
            "metrics": metrics,
            "best_strategy": best,
            "results": {s: r for s, r in results.items()}
        }, f, indent=2, default=str)

    return metrics, best, results


def run_full_bench(instances, strategy, llm, work_dir, limit=None):
    """Run full evaluation on all instances."""
    if limit:
        instances = instances[:limit]
    logger.info(f"\n{'='*60}")
    logger.info(f"FULL EVALUATION: {len(instances)} instances, strategy={strategy}")
    logger.info(f"{'='*60}")

    results = run_full_evaluation(
        instances, strategy, work_dir, llm, max_workers=3
    )

    eval_metrics = compute_pass_at_1(results, instances)
    strategy_metrics = {strategy: compute_strategy_metrics(results)}
    llm_stats = llm.get_usage_stats()

    # Generate report
    report_path = os.path.join(ANALYSIS_DIR, "evaluation_report.md")
    report = generate_evaluation_report(eval_metrics, strategy_metrics, llm_stats, report_path)

    # Save full results
    results_path = os.path.join(ANALYSIS_DIR, "full_results.json")
    with open(results_path, "w") as f:
        json.dump({
            "eval_metrics": eval_metrics,
            "llm_stats": llm_stats,
            "results": results
        }, f, indent=2, default=str)

    logger.info(f"\n{'='*60}")
    logger.info(f"RESULTS:")
    logger.info(f"  Total instances: {eval_metrics['total_instances']}")
    logger.info(f"  Patches generated: {eval_metrics['patches_generated']}")
    logger.info(f"  Valid patches: {eval_metrics['patches_valid_syntax']}")
    logger.info(f"  Pass@1 (proxy): {eval_metrics['pass_at_1_pct']:.2f}%")
    logger.info(f"  Total tokens: {llm_stats['total_tokens']:,}")
    logger.info(f"  Est. cost: ${llm.estimate_cost():.4f}")
    logger.info(f"  Report: {report_path}")
    logger.info(f"{'='*60}")

    return eval_metrics, strategy_metrics, llm_stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["pilot", "full", "all"], default="all")
    parser.add_argument("--pilot-n", type=int, default=6)
    parser.add_argument("--full-limit", type=int, default=None,
                        help="Limit full eval instances (None=all 300)")
    parser.add_argument("--strategy", default=None,
                        help="Force strategy for full eval")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    # Step 1: Test connection
    llm, _ = test_llm_connection()

    # Load dataset
    instances = load_cached_instances()
    logger.info(f"Dataset loaded: {len(instances)} instances")

    # Use a persistent work dir for repos (to avoid re-cloning)
    work_dir = os.path.join("/tmp", "swe_repos")
    os.makedirs(work_dir, exist_ok=True)

    if args.mode in ("pilot", "all"):
        pilot_metrics, best_strategy, pilot_results = run_pilot(
            instances, llm, work_dir, n=args.pilot_n
        )
    else:
        best_strategy = args.strategy or "plan_solve"

    if args.strategy:
        best_strategy = args.strategy

    if args.mode in ("full", "all"):
        eval_metrics, strategy_metrics, llm_stats = run_full_bench(
            instances, best_strategy, llm, work_dir, limit=args.full_limit
        )
