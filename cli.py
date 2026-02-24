"""CLI for the SWE Agent - evaluate single GitHub issues or run full benchmark."""
import argparse
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, "/app/swe_agent_benchmark_0958")
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    handlers=[logging.StreamHandler(sys.stdout)],
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


def cmd_solve(args):
    """Solve a single GitHub issue."""
    from agent.llm_client import LLMClient
    from agent.core_agent import SWEAgent

    instance = {
        "instance_id": args.instance_id or "custom_issue",
        "repo": args.repo,
        "base_commit": args.commit or "",
        "problem_statement": args.problem,
    }

    llm = LLMClient()
    agent = SWEAgent(strategy=args.strategy, llm=llm)

    with tempfile.TemporaryDirectory() as work_dir:
        result = agent.solve_instance(instance, work_dir, strategy=args.strategy)

    print(f"\n{'='*60}")
    print(f"Instance: {result['instance_id']}")
    print(f"Strategy: {result['strategy']}")
    print(f"Success: {result['success']}")
    print(f"Elapsed: {result['elapsed_sec']}s")
    print(f"Tokens used: {result['usage'].get('tokens_used', 0)}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    if result.get("patch"):
        print(f"\nPatch ({len(result['patch'])} chars):\n{result['patch'][:2000]}")
    print(f"{'='*60}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Result saved to {args.output}")


def cmd_experiment(args):
    """Run strategy experiment on a small subset."""
    from agent.llm_client import LLMClient
    from agent.experiment import run_strategy_experiment, compute_strategy_metrics, select_best_strategy
    from data.pipeline import load_cached_instances

    instances = load_cached_instances()
    subset = instances[:args.n]
    logger.info(f"Running experiment on {len(subset)} instances")

    llm = LLMClient()
    strategies = args.strategies.split(",") if args.strategies else ["single_shot", "plan_solve", "react"]

    with tempfile.TemporaryDirectory() as work_dir:
        results = run_strategy_experiment(subset, strategies, work_dir, llm, max_workers=args.workers)

    # Print metrics
    all_metrics = {}
    for strategy, res in results.items():
        metrics = compute_strategy_metrics(res)
        all_metrics[strategy] = metrics
        print(f"\n{strategy}: valid_patches={metrics['valid_patches']}/{metrics['total']} "
              f"({metrics['valid_patch_rate']:.1%}), avg_tokens={metrics['avg_tokens']:.0f}")

    best = select_best_strategy(results)
    print(f"\nBest strategy: {best}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump({"results": {s: r for s, r in results.items()},
                       "metrics": all_metrics, "best_strategy": best}, f, indent=2, default=str)
        print(f"Experiment results saved to {args.output}")


def cmd_evaluate(args):
    """Run full evaluation on SWE Bench lite."""
    from agent.llm_client import LLMClient
    from agent.experiment import run_full_evaluation, compute_strategy_metrics
    from agent.evaluator import compute_pass_at_1, generate_evaluation_report
    from data.pipeline import load_cached_instances
    from utils.config import ANALYSIS_DIR, PATCHES_DIR

    instances = load_cached_instances()
    if args.limit:
        instances = instances[:args.limit]
    logger.info(f"Evaluating {len(instances)} instances with strategy={args.strategy}")

    llm = LLMClient()

    with tempfile.TemporaryDirectory() as work_dir:
        results = run_full_evaluation(instances, args.strategy, work_dir, llm,
                                      max_workers=args.workers)

    # Compute metrics
    eval_metrics = compute_pass_at_1(results, instances)
    strategy_metrics = {args.strategy: compute_strategy_metrics(results)}
    llm_stats = llm.get_usage_stats()

    # Generate report
    os.makedirs(ANALYSIS_DIR, exist_ok=True)
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

    print(f"\n{'='*60}")
    print(f"EVALUATION COMPLETE")
    print(f"Total instances: {eval_metrics['total_instances']}")
    print(f"Patches generated: {eval_metrics['patches_generated']}")
    print(f"Valid patches: {eval_metrics['patches_valid_syntax']}")
    print(f"Pass@1 (proxy): {eval_metrics['pass_at_1_pct']:.2f}%")
    print(f"Total tokens: {llm_stats['total_tokens']:,}")
    print(f"Est. cost: ${llm.estimate_cost():.4f}")
    print(f"\nReport: {report_path}")
    print(f"Results: {results_path}")
    print(f"{'='*60}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="SWE Agent - Solve GitHub Issues with moonshotai/kimi-k2.5"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # solve command
    solve_parser = subparsers.add_parser("solve", help="Solve a single GitHub issue")
    solve_parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    solve_parser.add_argument("--problem", required=True, help="Problem statement text")
    solve_parser.add_argument("--commit", help="Base commit hash")
    solve_parser.add_argument("--instance-id", help="Instance ID")
    solve_parser.add_argument("--strategy", default="plan_solve",
                              choices=["react", "plan_solve", "single_shot"])
    solve_parser.add_argument("--output", help="Save result JSON to file")
    solve_parser.set_defaults(func=cmd_solve)

    # experiment command
    exp_parser = subparsers.add_parser("experiment", help="Run strategy experiments on subset")
    exp_parser.add_argument("--n", type=int, default=5, help="Number of instances to test")
    exp_parser.add_argument("--strategies", default="single_shot,plan_solve",
                            help="Comma-separated strategies to test")
    exp_parser.add_argument("--workers", type=int, default=2, help="Parallel workers")
    exp_parser.add_argument("--output", help="Save results JSON to file")
    exp_parser.set_defaults(func=cmd_experiment)

    # evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Run full SWE Bench lite evaluation")
    eval_parser.add_argument("--strategy", default="plan_solve",
                             choices=["react", "plan_solve", "single_shot"])
    eval_parser.add_argument("--workers", type=int, default=3, help="Parallel workers")
    eval_parser.add_argument("--limit", type=int, help="Limit number of instances")
    eval_parser.set_defaults(func=cmd_evaluate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
