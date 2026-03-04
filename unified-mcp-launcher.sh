#!/bin/bash
# Unified Memory MCP server launcher (stdio transport)
# Combines flat text search (SQLite FTS5) with vector similarity search (sentence-transformers)

# Run the unified memory server
exec ~/.claude-memory/graphiti-venv/bin/python3 \
    ~/claude-memory/src/unified_memory_server.py
