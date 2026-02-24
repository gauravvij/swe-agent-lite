"""Configuration module for the SWE agent."""
import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "moonshotai/kimi-k2.5")

# Agent settings
MAX_ITERATIONS = 12
MAX_TOKENS_PER_CALL = 8192
MAX_CONTEXT_TOKENS = 32000
TEMPERATURE = 0.0
REQUEST_TIMEOUT = 120
MAX_RETRIES = 3
RETRY_DELAY = 5

# Paths
PROJECT_ROOT = "/app/swe_agent_benchmark_0958"
PATCHES_DIR = os.path.join(PROJECT_ROOT, "patches")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
ANALYSIS_DIR = os.path.join(PROJECT_ROOT, "analysis")
