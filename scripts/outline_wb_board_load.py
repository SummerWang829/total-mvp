"""Thin wrapper: the load test lives in app/outline_wb/loadtest.py so it is also in the server image. python scripts/outline_wb_board_load.py [args]"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
runpy.run_module("app.outline_wb.loadtest", run_name="__main__")
