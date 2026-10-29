import importlib
import json
import os
import runpy
import sys
from pathlib import Path

if len(sys.argv) != 5:
    raise SystemExit("usage: bootstrap RUN_REPO MATERIALIZER OLD_GRANT NEW_GRANT")
repo = Path(sys.argv[1]).resolve(strict=True)
helper = Path(sys.argv[2]).resolve(strict=True)
old = Path(sys.argv[3]).resolve(strict=True)
new = Path(sys.argv[4]).resolve()
package_root = repo / "src" / "adaptive_trading_bot"
if any(n == "adaptive_trading_bot" or n.startswith("adaptive_trading_bot.")
       for n in sys.modules):
    raise SystemExit("project was imported before path binding")
sys.path[:0] = [str(repo / "src"), str(repo)]
os.chdir(repo)
name = "adaptive_trading_bot.research.breakout_history_screened_comparison"
module = importlib.import_module(name)
expected = package_root / "research" / "breakout_history_screened_comparison.py"
if Path(module.__file__).resolve() != expected.resolve(strict=True):
    raise SystemExit("grant loader is not from the frozen checkout")
for n, m in tuple(sys.modules.items()):
    if n == "adaptive_trading_bot" or n.startswith("adaptive_trading_bot."):
        origin = getattr(m, "__file__", None)
        paths = list(getattr(m, "__path__", ()))
        if origin is not None and not Path(origin).resolve().is_relative_to(package_root):
            raise SystemExit("mixed project origin: " + n)
        if any(not Path(p).resolve().is_relative_to(package_root) for p in paths):
            raise SystemExit("mixed project package path: " + n)
print(json.dumps({"status": "CHILD_ORIGIN_OK", "python": sys.executable,
                  "cwd": str(Path.cwd()), "loader": module.__file__}), flush=True)
sys.argv = [str(helper), str(old), str(new)]
runpy.run_path(str(helper), run_name="__main__")
