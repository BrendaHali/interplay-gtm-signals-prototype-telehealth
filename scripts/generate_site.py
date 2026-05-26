"""
Static site generator for GitHub Pages.

Copies the rich client-side dashboard (root index.html) into site/ along with
the latest run outputs so GitHub Pages serves the same UX as the local app.
The dashboard fetches outputs/alerts.json and outputs/run_summary.json at
page load and renders every panel client-side. A .nojekyll marker prevents
GitHub Pages from running Jekyll on the directory.

Run after the pipeline writes outputs/. The GitHub Actions workflow uploads
site/ as the Pages artifact.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# Allow `from scripts._lib...` imports when this script is invoked directly
# from the repo root (matches run_pipeline.py's import strategy).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SITE_DIR = Path("site")
OUTPUTS_SRC = Path("outputs")
INDEX_SRC = Path("index.html")
DOCS_SRC = Path("docs")


def main() -> None:
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "outputs").mkdir(exist_ok=True)

    shutil.copyfile(INDEX_SRC, SITE_DIR / "index.html")

    if DOCS_SRC.exists():
        shutil.copytree(DOCS_SRC, SITE_DIR / "docs", dirs_exist_ok=True)

    # NOTE: The per-account aggregator runs at the end of run_pipeline.py's
    # stage_publish, so outputs/accounts_with_signals.json is always fresh
    # against outputs/alerts.json by the time this script copies assets.
    # generate_site.py stays focused on static asset copying.

    fallbacks = {
        "alerts.json": {"alerts": [], "count": 0},
        "run_summary.json": {},
        "watchlist_opportunities.json": {"count": 0, "opportunities": []},
        "accounts_with_signals.json": {"count": 0, "accounts": []},
    }
    for name, fallback in fallbacks.items():
        src = OUTPUTS_SRC / name
        if src.exists():
            shutil.copyfile(src, SITE_DIR / "outputs" / name)
        else:
            (SITE_DIR / "outputs" / name).write_text(json.dumps(fallback))

    # Copy accounts directory for full watchlist visual directory
    acc_src = Path("data/accounts.json")
    if acc_src.exists():
        shutil.copyfile(acc_src, SITE_DIR / "outputs" / "accounts.json")
    else:
        (SITE_DIR / "outputs" / "accounts.json").write_text(json.dumps([]))

    # Copy account_profiles.json for HQ, website, product, founded fields the
    # dashboard's full-watchlist directory consumes.
    profiles_src = Path("data/account_profiles.json")
    if profiles_src.exists():
        shutil.copyfile(profiles_src, SITE_DIR / "outputs" / "account_profiles.json")
    else:
        (SITE_DIR / "outputs" / "account_profiles.json").write_text(json.dumps({"profiles": {}}))

    (SITE_DIR / ".nojekyll").write_text("")

    print(
        "site/ generated: index.html + docs/ + outputs/alerts.json + "
        "outputs/accounts_with_signals.json + outputs/run_summary.json + "
        "outputs/accounts.json + .nojekyll"
    )


if __name__ == "__main__":
    main()
