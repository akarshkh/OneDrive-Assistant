"""
Tests package init.
Adds the project root to sys.path so `app` imports work without installation.
"""
import sys
import os

# Ensure `app` is importable from tests/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
