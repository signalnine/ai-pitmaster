import pytest
import sys
import os
from unittest.mock import Mock

# Add the project root to the path so we can import ai-pitmaster
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Mock external dependencies
sys.modules['anthropic'] = Mock()
sys.modules['requests'] = Mock()
sys.modules['scipy'] = Mock()
sys.modules['scipy.optimize'] = Mock()

# For tests that need the real curve_fit, we'll handle that specifically in those tests