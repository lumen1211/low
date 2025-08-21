import sys
from pathlib import Path

# Ensure project root is on sys.path for 'import src'
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
