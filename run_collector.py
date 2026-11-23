"""Start the standalone SSI collector without starting the web server."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    worker = Path(__file__).resolve().parent / "scripts" / "ssi_worker.py"
    runpy.run_path(str(worker), run_name="__main__")
