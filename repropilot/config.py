import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))

RUNTIME = Path(os.environ.get("REPROPILOT_RUNTIME", str(ROOT / "runtime"))).resolve()
DATA = ROOT / "data"
WEB = ROOT / "web"
DB = RUNTIME / "runs.sqlite3"
TOOLS = ["inspect_dataset", "validate_design", "fit_deseq2", "render_figures", "write_report", "verify_delivery"]
