import argparse
import json
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move transient node logs from reports/ into a run directory.")
    parser.add_argument("result_dir", help="Run directory where logs should be moved")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    result_dir = Path(args.result_dir).resolve()
    result_dir.mkdir(parents=True, exist_ok=True)

    root_dir = Path(__file__).resolve().parent.parent
    report_dir = root_dir / "reports"
    summary_path = result_dir / "collect_logs_summary.json"

    moved_files = []
    errors = []
    warnings = []

    if not report_dir.exists():
        warnings.append(f"Report directory not found: {report_dir}")
    else:
        for log in sorted(report_dir.glob("node_*.log*")):
            dest = result_dir / log.name
            try:
                shutil.move(str(log), dest)
                moved_files.append(log.name)
            except Exception as exc:
                errors.append(f"Failed to move {log} -> {dest}: {exc}")

    if not moved_files:
        warnings.append(f"No node logs found under {report_dir}")
        print(f"[WARN] No node logs found in {report_dir}", file=sys.stderr)
    else:
        print(f"[INFO] Moved {len(moved_files)} log files into {result_dir}")

    for warning in warnings:
        if warning.startswith("No node logs found"):
            continue
        print(f"[WARN] {warning}", file=sys.stderr)

    for error in errors:
        print(f"[ERROR] {error}", file=sys.stderr)

    payload = {
        "result_dir": str(result_dir),
        "report_dir": str(report_dir),
        "moved_count": len(moved_files),
        "moved_files": moved_files,
        "warnings": warnings,
        "errors": errors,
    }
    summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
