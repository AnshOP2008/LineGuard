"""Command line: python -m lineguard <command>

  download   fetch SKAB and the UCI hydraulic dataset into ./data
  evaluate   regenerate results/ (add --deep to train the autoencoder baselines)
  export     write the dashboard data into web/data/
  serve      run the live API + dashboard (FastAPI) on http://localhost:8000
  all        download, evaluate, export
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> None:
    command = argv[0] if argv else "help"
    if command in ("download", "all"):
        from .data import download_hydraulic, download_skab
        download_skab()
        try:
            download_hydraulic()
        except Exception as exc:  # the hydraulic rig is optional for the core demo
            print(f"Hydraulic dataset not downloaded ({exc}). Component diagnosis will be skipped.")
    if command in ("evaluate", "all"):
        from .evaluate import run
        report = run(deep="--deep" in argv)
        print(open(__import__("lineguard.config", fromlist=["RESULTS_DIR"]).RESULTS_DIR / "RESULTS.md").read())
        _ = report
    if command in ("export", "all"):
        from .export import export
        export()
    if command == "serve":
        import uvicorn
        uvicorn.run("service.app:app", host="0.0.0.0", port=8000)
    if command in ("help", "-h", "--help"):
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
