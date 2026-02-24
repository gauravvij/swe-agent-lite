"""Data pipeline for loading SWE Bench lite instances."""
import json
import logging
import os
import sys
from typing import Optional

logging.basicConfig(handlers=[logging.StreamHandler(sys.stdout)], level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, "/app/swe_agent_benchmark_0958")
from utils.config import DATA_DIR


def load_swebench_lite(split: str = "test", max_instances: Optional[int] = None) -> list[dict]:
    """Load SWE Bench lite instances from HuggingFace datasets."""
    from datasets import load_dataset
    logger.info(f"Loading SWE Bench lite dataset (split={split})...")
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split=split)
    instances = list(ds)
    if max_instances:
        instances = instances[:max_instances]
    logger.info(f"Loaded {len(instances)} instances")
    # Cache locally
    os.makedirs(DATA_DIR, exist_ok=True)
    cache_path = os.path.join(DATA_DIR, f"swebench_lite_{split}.json")
    with open(cache_path, "w") as f:
        json.dump(instances, f, indent=2, default=str)
    logger.info(f"Cached to {cache_path}")
    return instances


def load_cached_instances(split: str = "test") -> list[dict]:
    """Load from local cache if available, else download."""
    cache_path = os.path.join(DATA_DIR, f"swebench_lite_{split}.json")
    if os.path.exists(cache_path):
        logger.info(f"Loading from cache: {cache_path}")
        with open(cache_path) as f:
            return json.load(f)
    return load_swebench_lite(split)


def get_instance_summary(instance: dict) -> str:
    """Return a brief summary string for an instance."""
    return (f"[{instance.get('instance_id', '?')}] "
            f"repo={instance.get('repo', '?')} "
            f"issue_len={len(instance.get('problem_statement', ''))} chars")


if __name__ == "__main__":
    instances = load_swebench_lite(split="test")
    print(f"\nTotal instances: {len(instances)}")
    print(f"Keys: {list(instances[0].keys())}")
    print(f"\nFirst instance summary: {get_instance_summary(instances[0])}")
    print(f"Problem statement snippet:\n{instances[0].get('problem_statement', '')[:300]}")
