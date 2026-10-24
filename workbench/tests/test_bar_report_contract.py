"""Run Bar tests in their actual workbench module namespace, not the chat backend's."""
import subprocess,unittest
from pathlib import Path

class BarReportContractTests(unittest.TestCase):
 def test_bar_workbench_adapter_suite(self):
  root=Path(__file__).resolve().parents[1]
  result=subprocess.run(['/usr/bin/python3','-m','unittest','discover','-s',str(root/'bar-report/tests'),'-p','test_bar_reports.py','-v'],capture_output=True,text=True,timeout=60)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
