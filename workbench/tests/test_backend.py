"""Include gateway auth/isolation tests in the existing project test entry point."""
import unittest
from pathlib import Path
def load_tests(loader,tests,pattern):
 return unittest.TestLoader().discover(str(Path(__file__).resolve().parents[1]/'backend/tests'),pattern='test_*.py')
