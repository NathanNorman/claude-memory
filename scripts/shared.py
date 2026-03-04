"""
Shared utilities for Graphiti ingestion scripts.

Consolidates duplicated code from ingest_session.py and backfill.py:
- Config loading from graphiti-config/config.yaml
- normalize_group_id() for project name cleanup
- create_graphiti_client() for Graphiti connection setup
"""

from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

# Defaults matching graphiti-config/config.yaml
_DEFAULT_GRAPH_DATABASE = 'claude-memory'
_DEFAULT_LLM_MODEL = 'gpt-4o-mini'
_DEFAULT_EMBED_MODEL = 'text-embedding-3-small'

CONFIG_PATH = Path(__file__).parent.parent / 'graphiti-config' / 'config.yaml'


def _load_config() -> dict:
    """Load config from graphiti-config/config.yaml, returning empty dict on failure."""
    if yaml is None:
        return {}
    try:
        if CONFIG_PATH.exists():
            return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        pass
    return {}


_config = _load_config()

GRAPH_DATABASE = _config.get('graphiti', {}).get('group_id', _DEFAULT_GRAPH_DATABASE)
LLM_MODEL = _config.get('llm', {}).get('model', _DEFAULT_LLM_MODEL)
EMBED_MODEL = _config.get('embedder', {}).get('model', _DEFAULT_EMBED_MODEL)


def normalize_group_id(project_name: str) -> str:
    """
    Convert project directory name to a clean group_id.
    e.g. '-Users-nathan-norman' -> 'home'
         '-Users-nathan-norman--toast-analytics' -> 'toast-analytics'
    """
    name = project_name.lstrip('-')
    home_prefix = 'Users-nathan-norman'
    if name.startswith(home_prefix):
        remainder = name[len(home_prefix):].lstrip('-')
        return remainder if remainder else 'home'
    return name


def create_graphiti_client(api_key: str) -> Graphiti:
    """Create a configured Graphiti client with FalkorDB driver and OpenAI LLM/embedder."""
    driver = FalkorDriver(host='localhost', port=6379, database=GRAPH_DATABASE)
    llm_client = OpenAIClient(
        config=LLMConfig(api_key=api_key, model=LLM_MODEL, small_model=LLM_MODEL),
    )
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(api_key=api_key, embedding_model=EMBED_MODEL),
    )
    return Graphiti(graph_driver=driver, llm_client=llm_client, embedder=embedder)
