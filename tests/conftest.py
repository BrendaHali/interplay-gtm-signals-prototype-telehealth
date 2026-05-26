"""
Pytest configuration: add repo root to sys.path so tests can import
scripts._lib modules using the same import paths the runtime uses.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
