"""Shared pytest configuration."""

import sys
import os

# Add parent directory to path so modules can be imported as plain names
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
