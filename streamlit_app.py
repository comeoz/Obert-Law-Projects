"""
Streamlit Community Cloud entry point.

The actual app lives in `Liquidation_Date_Auto/app.py`. This root-level launcher
exists so the app can be deployed on Streamlit Cloud with its *default* main-file
setting (`streamlit_app.py`) and zero extra configuration — just pick the repo and
click Deploy.

It puts the app folder on the import path (so `import cbp_client` resolves) and
then runs the real app script fresh on every Streamlit rerun.
"""

import os
import sys
import runpy

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Liquidation_Date_Auto")

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

runpy.run_path(os.path.join(APP_DIR, "app.py"), run_name="__main__")
