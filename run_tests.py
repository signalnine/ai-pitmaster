#!/usr/bin/env python3
"""
Test runner for AI Pitmaster
"""

import subprocess
import sys
import os

def run_tests():
    """Run all tests with pytest"""
    try:
        # Run pytest on the tests directory
        result = subprocess.run([
            sys.executable, "-m", "pytest", "tests", "-v"
        ], check=True, cwd=os.path.dirname(os.path.abspath(__file__)))
        
        print("\n✅ All tests passed!")
        return True
    except subprocess.CalledProcessError:
        print("\n❌ Some tests failed!")
        return False

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)