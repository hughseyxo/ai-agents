"""Tests for vidqueue skill."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "vidqueue"
sys.path.insert(0, str(SKILL_DIR))
