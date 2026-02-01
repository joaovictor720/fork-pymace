import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in_csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--out_points_csv", default=None)
    args = ap.parse_args()

    df = pd.read_csv(args.in_csv)
    if "degree" not in df.columns:
        raise SystemExit("[ERROR] Missing 'degree' column.")

    deg = pd.to_numeric(df["degree"], errors="coerce").dropna().astype(int).to_numpy()
    if deg.size == 0:
        raise SystemExit("[ERROR] No degree samples.")

    uniq, cnt = np.unique(deg, return_counts=True)
    order = np.argsort(uniq)
    x = uniq[order]
    c = cnt[order]
    y = np.cumsum(c) / np.sum(c)

    if args.out_points_csv is None:
        out_path = Path(args.out)
        out_points_csv = str(out_path.with_suffix("")) + "_points.csv"
    else:
        out_points_csv = args.out_points_csv

    pd.DataFrame({"degree": x, "probability": y}).to_csv(out_points_csv, index=False)
    print(f"[OK] Wrote: {out_points_csv}")

    plt.figure(figsize=(7, 4.5))
    plt.step(x, y, where="post")
    plt.plot(x, y, marker="o", linestyle="None")

    xmin = int(np.min(x))
    xmax = int(np.max(x))
    plt.xticks(np.arange(xmin, xmax + 1, 1))

    plt.ylim(0.0, 1.0)
    plt.xlabel("Neighborhood Degree")
    plt.ylabel("Probability")
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.tight_layout()
    plt.savefig(args.out, bbox_inches="tight", dpi=300)
    print(f"[OK] Saved: {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
