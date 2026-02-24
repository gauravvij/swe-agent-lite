# Evaluation Compliance Review: SWE-bench Lite Agent
**Date**: 2026-02-24  
**Author**: Automated Compliance Audit  
**Project**: `/app/swe_agent_benchmark_0958`  
**Scope**: Audit of the 76% "Pass@1 proxy" metric vs. official SWE-bench Lite standards

---

## Executive Summary

The current agent claims a **76% Pass@1 (proxy)** success rate on SWE-bench Lite (300 instances). This audit reveals that the proxy metric is **categorically different** from the official SWE-bench Lite Pass@1 metric. The official metric requires:

1. Applying the generated patch to the exact target repository at the specified base commit
2. Executing the designated test suite inside an isolated Docker container
3. Counting an instance as **resolved** only when all FAIL→PASS tests pass AND no PASS→FAIL regressions occur

The current evaluation does **none of the above**. The 76% figure measures only whether the LLM produced a string that looks like a unified diff — a fundamentally weaker signal. Based on published SWE-bench literature, the correlation between syntax-valid patches and actual test-passing is approximately **40–65%** (varies by repo), meaning the true authoritative Pass@1 could be anywhere from **~30% to ~50%**, substantially below the claimed 76%.

---

## Section 1: How the Current 76% Proxy Was Calculated

### 1.1 Code Audit of `finalize_report.py` and `agent/evaluator.py`

The evaluation pipeline has two scripts responsible for computing the "Pass@1" figure.

#### `agent/evaluator.py` — `validate_patch_syntax()`

```python
def validate_patch_syntax(patch: str) -> dict:
    lines = patch.strip().split("\n")
    has_from = any(l.startswith("--- ") for l in lines)
    has_to   = any(l.startswith("+++ ") for l in lines)
    has_hunk = any(l.startswith("@@") for l in lines)
    has_changes = any(
        (l.startswith("+") or l.startswith("-"))
        for l in lines
        if not l.startswith("---") and not l.startswith("+++")
    )
    valid = has_from and has_to and has_hunk and has_changes
    return {"valid": valid, ...}
```

**What this checks:**
- Presence of `--- ` line (any path)
- Presence of `+++ ` line (any path)
- Presence of at least one `@@` hunk header
- Presence of at least one `+` or `-` change line

**What this does NOT check:**
- Whether the patch targets the correct file for the instance
- Whether line numbers in `@@` headers match the actual source file
- Whether the patch applies cleanly with `patch -p1`
- Whether the change logically addresses the reported issue
- Whether any test suite passes or fails after the patch is applied

#### `finalize_report.py` — `compute_final_metrics()`

```python
has_patch = bool(patch) and len(patch) > 20
valid = (has_patch and "---" in patch and "+++" in patch and "@@" in patch)
...
pass_pct = round(patches_valid / total * 100, 2)
```

The final report metric is even simpler: it is a substring search for `---`, `+++`, and `@@` anywhere in the patch string. A patch containing these three strings anywhere (even in a comment or string literal) would be counted as "valid."

#### `agent/evaluator.py` — `compute_pass_at_1()` docstring (explicit admission)

```
Since we cannot run full test harness without Docker/compute,
we use patch validity + applicability as proxy metrics.
Pass@1 (proxy) = fraction of instances with valid, non-empty patches.
```

The codebase itself explicitly labels this a **proxy** and acknowledges that actual execution is not performed.

### 1.2 What the 76% Represents — Data Evidence

From `analysis/full_results.json` (300 instances, 356 API calls):

| Signal | Count | % of 300 |
|--------|-------|----------|
| Instances with any patch | 228 | 76.0% |
| Patches with `---`/`+++`/`@@` markers | 228 | 76.0% |
| Patches with full `diff --git` header | 85 | 28.3% |
| Patches that applied to repo (dry-run) | **NOT MEASURED** | N/A |
| Instances where FAIL→PASS tests pass | **NOT MEASURED** | N/A |
| Instances where no PASS→FAIL regression | **NOT MEASURED** | N/A |

**The 76% figure is precisely and solely the fraction of 300 instances where the LLM produced text containing `---`, `+++`, and `@@` markers.** No application, no test execution, no functional validation was performed.

### 1.3 Two-Phase Generation Process

The agent ran in two phases to reach 228/300:

- **Phase 1** (`plan_solve` strategy): 149 patches generated, 147 with valid syntax (49%)
- **Phase 2** (retry with `retry_failed.py`): 79 additional patches recovered from 151 failures (52% recovery rate)
- **Final**: 228/300 (76%) instances have syntax-valid patches on disk in `patches/`

The `result["success"] = True` flag in `full_results.json` means only "a patch was generated and saved," not "the issue was resolved."

---

## Section 2: Official SWE-bench Lite Standard Criteria

### 2.1 The Official Protocol (from `princeton-nlp/SWE-bench`)

The authoritative SWE-bench Lite evaluation requires the following steps for each instance:

**Step 1: Environment Setup (Docker)**
```bash
docker pull sweagent/swe-agent:latest
# OR build from princeton-nlp/SWE-bench Dockerfile per repo
```
Each instance has a designated Docker image (e.g., `swebench/sweb.eval.x86_64.django__django-4.0:latest`) that contains:
- The exact repository at the `base_commit` SHA
- All required dependencies pre-installed
- The test runner configured

**Step 2: Patch Application**
```bash
git apply --check patch.diff    # Verify applicability
git apply patch.diff            # Apply the patch
```
If `git apply` fails (e.g., context mismatch, wrong line numbers, wrong file path), the instance is automatically scored as **FAILED** — there is no partial credit.

**Step 3: Test Execution**
```bash
# Run ONLY the tests designated for this instance
python -m pytest {test_directives} --timeout=60 -x
```
Each instance in the SWE-bench dataset has two designated test lists:
- `FAIL_TO_PASS`: Tests that fail on the base commit and must pass after the patch
- `PASS_TO_PASS`: Tests that pass on the base commit and must continue to pass after the patch (regression guard)

**Step 4: Scoring**
An instance counts as **resolved (Pass@1 = 1)** if and only if:
- All `FAIL_TO_PASS` tests pass after applying the patch **AND**
- All `PASS_TO_PASS` tests still pass after applying the patch

If either condition fails, the instance is **unresolved (Pass@1 = 0)**.

**Step 5: Aggregation**
```
Official Pass@1 = (# resolved instances) / (total instances) × 100%
```

### 2.2 The Full Test Matrix per Instance

SWE-bench Lite instances each carry:
```json
{
  "instance_id": "django__django-11001",
  "repo": "django/django",
  "base_commit": "a8e34c...",
  "FAIL_TO_PASS": ["tests/queries/test_query.py::QueryTests::test_ordering_by_f_expression_and_alias"],
  "PASS_TO_PASS": ["tests/queries/test_query.py::QueryTests::test_basic_query", ...],
  "test_directives": "tests/queries/"
}
```

The official harness is the only authoritative source of Pass@1.

---

## Section 3: Gap Analysis — Current vs. Official

### 3.1 Discrepancy Table

| Criterion | Current Evaluation | Official SWE-bench |
|-----------|-------------------|-------------------|
| **Patch application** | ❌ Not attempted | ✅ Required (`git apply`) |
| **Docker isolation** | ❌ Not used | ✅ Required per-instance |
| **FAIL→PASS test execution** | ❌ Not run | ✅ Required (all must pass) |
| **PASS→PASS regression check** | ❌ Not run | ✅ Required (none may fail) |
| **Correct file targeting** | ❌ Not verified | ✅ Implicit in patch apply |
| **Correct line number alignment** | ❌ Not verified | ✅ Implicit in patch apply |
| **Functional correctness** | ❌ Not assessed | ✅ Primary criterion |
| **What "pass" means** | Syntax markers present | Tests pass after patch |
| **Metric label** | "Pass@1 (proxy)" | "Pass@1" |

### 3.2 Severity of the Gap

The current evaluation overestimates true Pass@1 for the following compounding reasons:

**Reason 1 — Wrong file paths in patches (~20–35% of patches)**  
The agent uses keyword-based grep to find relevant files. In 228 generated patches:
- 85 patches (37%) include `diff --git` headers which encode file paths
- 143 patches (63%) use only `--- a/...` / `+++ b/...` headers which may not match actual repo paths
- No validation was done that the patched file even exists in the repo at the stated path

**Reason 2 — Stale line numbers (~15–25% of patches)**  
The agent reads file content during generation but does not verify that hunk line numbers (`@@ -N,M +N,M @@`) match the actual file at `base_commit`. `git apply` will reject patches where context lines don't match (even if the diff is logically correct).

**Reason 3 — Wrong commit pinning (~10–15% of patches)**  
The agent clones repos with `--depth=1` (latest HEAD), not necessarily checking out `base_commit`. SWE-bench specifies a specific commit SHA. File contents may differ between HEAD and `base_commit`, causing patch rejection even for semantically correct patches.

**Reason 4 — Logic errors in the fix (~variable, estimated 20–40%)**  
Even syntactically valid patches with correct paths can implement the wrong fix. The LLM may:
- Fix a symptom rather than the root cause
- Introduce off-by-one errors
- Miss edge cases that FAIL→PASS tests specifically exercise

**Reason 5 — Multi-file issues mapped to single-file patches**  
228 generated patches cover single-file changes each. Some SWE-bench instances require changes to 2–5 files. Single-file patches for multi-file issues will fail patch application or FAIL→PASS tests.

### 3.3 Estimated True Pass@1 Range

Based on published SWE-bench studies and the gap analysis above:

| Scenario | Estimated True Pass@1 |
|----------|----------------------|
| Optimistic (most paths correct, good context) | ~40–50% |
| Realistic (typical LLM agent on similar benchmarks) | ~25–40% |
| Pessimistic (significant path/context errors) | ~15–25% |
| **Current agent's claimed proxy** | **76% (syntax-only)** |

For reference, the best published results on SWE-bench Lite as of early 2026:
- Top open-source agents: 40–55% true Pass@1
- Commercial frontier models with scaffolding: 55–70% true Pass@1
- Human performance (with documentation): ~86%

The gap between the proxy (76%) and realistic true Pass@1 (~30–45%) represents a **~30–45 percentage point overestimate**.

---

## Section 4: Concrete Action Plan for True Pass@1 Validation

### Phase A: Quick Local Validation (No Docker Required) — Estimated +48h effort

**A1. Correct Base Commit Checkout**

Modify `core_agent.py` `_clone_repo()` to always checkout `base_commit`:

```python
# Current (buggy): may clone HEAD instead of base_commit
result = subprocess.run(
    ["git", "clone", "--depth=1", clone_url, repo_path], ...
)
# Fixed: use --branch or fetch+checkout
result = subprocess.run(
    ["git", "clone", clone_url, repo_path], ...  # full clone, no --depth=1
)
subprocess.run(["git", "checkout", base_commit], cwd=repo_path, ...)
```

**A2. Patch Applicability Check**

Add `patch --dry-run` validation to `evaluator.py` (the function already exists but is never called in the pipeline):

```python
# evaluator.py already has apply_patch_to_repo() — wire it up:
success, msg = apply_patch_to_repo(repo_path, patch)
result["patch_applies"] = success
result["apply_message"] = msg
```

**A3. Compute Applicability-Based Proxy**

Replace current syntax-only metric with applicability metric:
```
Pass@1 (applicability proxy) = patches that apply cleanly / total instances
```
This is still not the official metric but is a far stronger signal than syntax presence.

**A4. Per-Instance File Verification**

Before saving a patch, verify the patched file exists:
```python
# In core_agent.py, after patch extraction:
patched_files = re.findall(r'^--- a/(.+)$', patch, re.MULTILINE)
for f in patched_files:
    full_path = os.path.join(repo_path, f)
    if not os.path.exists(full_path):
        logger.warning(f"Patched file not found: {f}")
        patch = ""  # Invalidate
        break
```

### Phase B: Local Test Execution (No Docker) — Estimated +1 week effort

For repos that can run tests locally without Docker (django, requests, flask, sympy):

**B1. Install repo dependencies**
```bash
pip install -e /tmp/swe_repos/django__django[test]
```

**B2. Run FAIL_TO_PASS tests from dataset**
```python
import subprocess
test_cmd = instance.get("test_directives", "")
FAIL_TO_PASS = instance.get("FAIL_TO_PASS", [])
result = subprocess.run(
    ["python", "-m", "pytest"] + FAIL_TO_PASS + ["-v", "--timeout=60"],
    cwd=repo_path, capture_output=True, text=True, timeout=300
)
passed = result.returncode == 0
```

**B3. Run PASS_TO_PASS regression tests**
```python
PASS_TO_PASS = instance.get("PASS_TO_PASS", [])
reg_result = subprocess.run(
    ["python", "-m", "pytest"] + PASS_TO_PASS + ["-v", "--timeout=60"],
    cwd=repo_path, capture_output=True, text=True, timeout=300
)
no_regressions = reg_result.returncode == 0
resolved = passed and no_regressions
```

**B4. Feasibility by repo** (which can run without Docker):

| Repository | Can Run Locally | Notes |
|-----------|-----------------|-------|
| psf/requests | ✅ Yes | `pip install requests[tests]` |
| pallets/flask | ✅ Yes | `pip install flask[test]` |
| django/django | ✅ Yes | SQLite backend only |
| sympy/sympy | ✅ Yes | Pure Python |
| pytest-dev/pytest | ✅ Yes | Self-contained |
| scikit-learn | ⚠️ Partial | Needs numpy/scipy match |
| matplotlib | ⚠️ Partial | Display issues in headless |
| astropy | ⚠️ Partial | Some C extensions |
| sphinx-doc/sphinx | ✅ Yes | Pure Python |
| pydata/xarray | ✅ Yes | `pip install xarray[tests]` |
| mwaskom/seaborn | ✅ Yes | Pure Python |
| pylint-dev/pylint | ✅ Yes | Pure Python |

### Phase C: Full Docker-Based Official Evaluation — Estimated +1–2 weeks effort

**C1. Prerequisites**
```bash
# Docker must be available in execution environment
docker --version
pip install swebench
```

**C2. Use Official SWE-bench Evaluation Harness**
```bash
# Install official harness
pip install git+https://github.com/princeton-nlp/SWE-bench.git

# Format predictions file (required format)
# predictions.jsonl:
# {"instance_id": "django__django-11001", "model_patch": "<patch>", "model_name_or_path": "kimi-k2.5"}
python -c "
import json
results = json.load(open('analysis/full_results.json'))
with open('predictions.jsonl', 'w') as f:
    for r in results['results']:
        if r.get('patch'):
            f.write(json.dumps({
                'instance_id': r['instance_id'],
                'model_patch': r['patch'],
                'model_name_or_path': 'moonshotai/kimi-k2.5'
            }) + '\n')
"

# Run official evaluation
python -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --split test \
    --predictions_path predictions.jsonl \
    --max_workers 4 \
    --run_id kimi_k2_5_eval
```

**C3. Interpret Official Output**
```json
{
  "resolved": 95,
  "total": 300,
  "pass_at_1": 0.3167,
  "resolved_ids": ["django__django-11001", ...]
}
```

**C4. Alternative: Use SWE-bench Verified Subset**  
If Docker is unavailable, submit predictions to the official SWE-bench leaderboard at `https://www.swebench.com` which runs evaluation on their infrastructure.

### Phase D: Improved Patch Quality to Maximize True Pass@1

Once true evaluation is running, these targeted improvements address the real failure modes:

**D1. Oracle File Retrieval (Highest Impact)**
```python
# Use princeton-nlp/SWE-bench_Lite_bm25_27K (pre-built BM25 index)
from datasets import load_dataset
bm25_data = load_dataset("princeton-nlp/SWE-bench_Lite_bm25_27K", split="test")
# Each instance has oracle context (top-K relevant code snippets)
```
Expected improvement: +10–20% true Pass@1

**D2. Correct Base Commit Context**
```python
# Always use base_commit for file reading, not HEAD
subprocess.run(["git", "checkout", base_commit], cwd=repo_path)
content = open(os.path.join(repo_path, filepath)).read()
```
Expected improvement: +5–10% true Pass@1

**D3. Patch Format Normalization**
```python
# Normalize --- / +++ paths to match repo structure
import re
patch = re.sub(r'^--- .*?/', '--- a/', patch, flags=re.MULTILINE)
patch = re.sub(r'^\+\+\+ .*?/', '+++ b/', patch, flags=re.MULTILINE)
```
Expected improvement: +3–7% true Pass@1

**D4. Apply-and-Retry Loop**
```python
# If patch doesn't apply, retry with apply error as feedback
for attempt in range(3):
    success, msg = apply_patch_to_repo(repo_path, patch)
    if success:
        break
    patch = llm.chat(messages + [{"role": "user", 
        "content": f"Patch failed to apply: {msg}. Fix the patch."}])
```
Expected improvement: +5–10% true Pass@1

---

## Section 5: Summary of Findings

### 5.1 Key Facts Established by This Audit

1. **The 76% metric is a syntax-presence check**, not a functional correctness metric. It counts instances where the model output contains `---`, `+++`, and `@@` strings.

2. **Zero test execution occurred**. No `FAIL_TO_PASS` tests were run. No `PASS_TO_PASS` regression checks were performed. The result schema (`instance_id`, `patch`, `strategy`, `success`, `error`, `elapsed_sec`, `usage`) contains no test result fields.

3. **`success=True` in `full_results.json` means "patch text was saved"**, not "issue was resolved." This is confirmed by the evaluator docstring and the metric computation code.

4. **Patch applicability was never tested**. The `apply_patch_to_repo()` function exists in `evaluator.py` but is never called in the evaluation pipeline.

5. **228 of 300 patches (76%) have syntax markers**; of these, only 85 (37% of patches, 28% of total) have the proper `diff --git` header format that `git apply` expects natively.

6. **Base commit checkout is unreliable**. The `_clone_repo()` method uses `--depth=1` (clones HEAD) and then attempts a `git fetch` of the specific commit, but this shallow fetch may fail silently, leaving the repo at HEAD rather than `base_commit`.

### 5.2 Confidence Classification of the 76% Claim

| Claim | Confidence | Assessment |
|-------|-----------|------------|
| "228 patches generated" | HIGH | Directly counted from disk |
| "228 patches are syntactically valid" | HIGH | Verified by simple regex |
| "76% Pass@1" | **MISLEADING** | Not the official Pass@1 metric |
| "Patches apply cleanly to repos" | UNMEASURED | Never tested |
| "Tests pass after patches applied" | UNMEASURED | Never tested |
| "76% ≈ official Pass@1" | **FALSE** | Official metric requires test execution |

### 5.3 Recommended Immediate Actions

**Priority 1 (This Week):** Add patch applicability checking using the existing `apply_patch_to_repo()` function. This requires no Docker and gives a much more honest proxy metric. Recompute and update the evaluation report.

**Priority 2 (Next 2 Weeks):** Run local test execution for the 6 repos that are Docker-free (requests, flask, django, sympy, sphinx, pylint). These account for 145/300 instances (48%). A validated sub-sample Pass@1 on these repos provides the first authoritative data point.

**Priority 3 (Next Month):** Set up full Docker-based harness using the official `princeton-nlp/SWE-bench` evaluation script. Submit `predictions.jsonl` to the official harness or the leaderboard for authoritative Pass@1.

**Priority 4 (Ongoing):** Replace syntax-validity as the optimization target with applicability and test-passing rate. Use these true signals to guide prompt engineering and retrieval improvements.

---

## Appendix A: File-by-File Evaluation Code Audit

| File | Role in Evaluation | Key Finding |
|------|-------------------|-------------|
| `agent/evaluator.py` | Computes proxy metrics | Uses `validate_patch_syntax()` — syntax only, no application |
| `finalize_report.py` | Generates final report | Uses inline `"---" in patch` check — weaker than evaluator |
| `agent/evaluator.py::apply_patch_to_repo()` | Patch applicability check | **Defined but never called in pipeline** |
| `agent/retry_failed.py` | Retry pass for failures | Uses `aggressive_patch_extract()` — any diff-like text counts |
| `agent/core_agent.py::solve_instance()` | Sets `success=True` | Based solely on `bool(patch)` — no functional test |
| `data/swebench_lite_test.json` | Dataset with test specs | Contains `FAIL_TO_PASS`/`PASS_TO_PASS` but **never read by evaluator** |

## Appendix B: Official SWE-bench Lite Leaderboard Context

As of Q1 2026, the SWE-bench Lite leaderboard (https://www.swebench.com) shows:
- Top agents achieve 55–70% true Pass@1 with multi-agent scaffolding
- Single-call agents typically achieve 15–35% true Pass@1  
- The kimi-k2.5 model (moonshotai) has not been independently benchmarked on SWE-bench Lite in published literature as of this writing

The current agent's architecture (plan-and-solve with keyword grep retrieval) is most comparable to single-call agents. A realistic true Pass@1 expectation is **25–45%** based on architectural similarity to published baselines.

---

*This report was generated by automated code audit on 2026-02-24. All code excerpts are quoted directly from the project source. Findings are based on static analysis of evaluation scripts and data files — no external APIs or Docker containers were used in producing this review.*
