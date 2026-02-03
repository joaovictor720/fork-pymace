import argparse
from pathlib import Path
from typing import Optional, List, Tuple, Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


def ecdf_points(values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    uniq, cnt = np.unique(values, return_counts=True)
    order = np.argsort(uniq)
    x = uniq[order]
    c = cnt[order]
    y = np.cumsum(c) / np.sum(c)
    return x, y


def pmf_points(values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    uniq, cnt = np.unique(values, return_counts=True)
    order = np.argsort(uniq)
    x = uniq[order]
    c = cnt[order]
    y = c / np.sum(c)
    return x, y


def parse_count_label(path_str: str) -> str:
    s = path_str.replace("\\", "/")
    token = "count="
    i = s.find(token)
    if i == -1:
        return Path(path_str).parent.name
    j = i + len(token)
    k = j
    while k < len(s) and s[k].isdigit():
        k += 1
    if k == j:
        return Path(path_str).parent.name
    return f"{s[j:k]} nodes/km$^2$"


def set_integer_xticks(xmin: int, xmax: int, max_xticks: int):
    ax = plt.gca()
    ax.xaxis.set_major_locator(MaxNLocator(nbins=max_xticks, integer=True, prune=None))
    ax.set_xlim(xmin, xmax)


def extend_ecdf_to_xmax(x: np.ndarray, y: np.ndarray, xmax: int) -> Tuple[np.ndarray, np.ndarray]:
    if x.size == 0:
        return x, y
    if int(x[-1]) == int(xmax):
        return x, y
    x2 = np.concatenate([x, np.array([int(xmax)], dtype=x.dtype)])
    y2 = np.concatenate([y, np.array([1.0], dtype=y.dtype)])
    return x2, y2


def ensure_out_dir(out_prefix: Path):
    out_prefix.parent.mkdir(parents=True, exist_ok=True)


def save_points_csv(out_prefix: Path, metric: str, kind: str, label: str, x: np.ndarray, y: np.ndarray):
    safe_label = (
        label.replace(" ", "")
        .replace("/", "_")
        .replace("\\", "_")
        .replace("$", "")
        .replace("^", "")
        .replace("{", "")
        .replace("}", "")
    )
    fp = out_prefix.with_name(f"{out_prefix.name}_{metric}_{kind}_{safe_label}.csv")
    if metric == "degree":
        df = pd.DataFrame({"degree": x.astype(int), kind: y})
    elif metric == "component":
        df = pd.DataFrame({"component_size": x.astype(int), kind: y})
    else:
        df = pd.DataFrame({"components_count": x.astype(int), kind: y})
    df.to_csv(fp, index=False)
    print(f"[OK] Wrote: {fp}")


def plot_multi(
    series: List[Tuple[str, np.ndarray, np.ndarray]],
    out_path: Path,
    xlabel: str,
    ylabel: str,
    max_xticks: int,
    xmin: int,
    xmax: int,
    drawstyle: Optional[str],
    ylim_top: float,
):
    plt.figure(figsize=(7, 4.5))
    for label, x, y in series:
        if drawstyle is None:
            plt.plot(x, y, linestyle="-", label=label)
        else:
            plt.plot(x, y, linestyle="-", label=label, drawstyle=drawstyle)

    set_integer_xticks(xmin, xmax, max_xticks)

    plt.ylim(0.0, ylim_top)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.legend(frameon=True)
    plt.tight_layout()
    plt.savefig(out_path, bbox_inches="tight", dpi=300)
    plt.close()
    print(f"[OK] Saved: {out_path}")


def extract_snapshot_values(df: pd.DataFrame, col: str) -> np.ndarray:
    if col not in df.columns:
        return np.array([], dtype=np.int64)
    needed = ["run", "time_bin_s", col]
    for c in needed:
        if c not in df.columns:
            return np.array([], dtype=np.int64)
    snap = df[needed].dropna().drop_duplicates(subset=["run", "time_bin_s"])
    vals = pd.to_numeric(snap[col], errors="coerce").dropna().astype(int).to_numpy()
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in_csv", required=True, nargs="+")
    ap.add_argument("--out_prefix", required=True)
    ap.add_argument("--max_xticks", type=int, default=9)
    ap.add_argument("--extend_ecdf_to_global_x", action="store_true")
    ap.add_argument("--pmf_style", choices=["line", "step"], default="line")
    ap.add_argument("--plot_components_count", action="store_true")
    args = ap.parse_args()

    in_paths = [Path(p) for p in args.in_csv]
    out_prefix = Path(args.out_prefix)
    ensure_out_dir(out_prefix)

    degree_ecdf_series = []
    degree_pmf_series = []
    comp_ecdf_series = []
    comp_pmf_series = []

    components_ecdf_series = []
    components_pmf_series = []

    degree_xmins = []
    degree_xmaxs = []
    comp_xmins = []
    comp_xmaxs = []
    components_xmins = []
    components_xmaxs = []

    per_csv_cache: List[Dict[str, object]] = []

    for p in in_paths:
        df = pd.read_csv(p)
        required = {"degree", "component_size"}
        missing = [c for c in sorted(required) if c not in df.columns]
        if missing:
            raise SystemExit(f"[ERROR] {p}: missing columns {missing}")

        label = parse_count_label(str(p))

        deg = pd.to_numeric(df["degree"], errors="coerce").dropna().astype(int).to_numpy()
        cs = pd.to_numeric(df["component_size"], errors="coerce").dropna().astype(int).to_numpy()

        if deg.size == 0 or cs.size == 0:
            continue

        x_deg_ecdf, y_deg_ecdf = ecdf_points(deg)
        x_deg_pmf, y_deg_pmf = pmf_points(deg)

        x_cs_ecdf, y_cs_ecdf = ecdf_points(cs)
        x_cs_pmf, y_cs_pmf = pmf_points(cs)

        degree_xmins.append(int(x_deg_ecdf.min()))
        degree_xmaxs.append(int(x_deg_ecdf.max()))
        comp_xmins.append(int(x_cs_ecdf.min()))
        comp_xmaxs.append(int(x_cs_ecdf.max()))

        comps_vals = extract_snapshot_values(df, "components_count")

        per_csv_cache.append({
            "label": label,
            "x_deg_ecdf": x_deg_ecdf,
            "y_deg_ecdf": y_deg_ecdf,
            "x_deg_pmf": x_deg_pmf,
            "y_deg_pmf": y_deg_pmf,
            "x_cs_ecdf": x_cs_ecdf,
            "y_cs_ecdf": y_cs_ecdf,
            "x_cs_pmf": x_cs_pmf,
            "y_cs_pmf": y_cs_pmf,
            "comps_vals": comps_vals,
        })

        if args.plot_components_count and comps_vals.size > 0:
            x_cc_ecdf, _ = ecdf_points(comps_vals)
            components_xmins.append(int(x_cc_ecdf.min()))
            components_xmaxs.append(int(x_cc_ecdf.max()))

    if not per_csv_cache:
        raise SystemExit("[ERROR] No valid samples across input CSVs.")

    deg_xmin = int(min(degree_xmins))
    deg_xmax = int(max(degree_xmaxs))
    cs_xmin = int(min(comp_xmins))
    cs_xmax = int(max(comp_xmaxs))

    have_components = args.plot_components_count and len(components_xmins) > 0
    if have_components:
        cc_xmin = int(min(components_xmins))
        cc_xmax = int(max(components_xmaxs))
    else:
        cc_xmin = 0
        cc_xmax = 0

    for it in per_csv_cache:
        label = str(it["label"])

        x_deg_ecdf = np.array(it["x_deg_ecdf"])
        y_deg_ecdf = np.array(it["y_deg_ecdf"])
        x_deg_pmf = np.array(it["x_deg_pmf"])
        y_deg_pmf = np.array(it["y_deg_pmf"])

        x_cs_ecdf = np.array(it["x_cs_ecdf"])
        y_cs_ecdf = np.array(it["y_cs_ecdf"])
        x_cs_pmf = np.array(it["x_cs_pmf"])
        y_cs_pmf = np.array(it["y_cs_pmf"])

        if args.extend_ecdf_to_global_x:
            x_deg_ecdf, y_deg_ecdf = extend_ecdf_to_xmax(x_deg_ecdf, y_deg_ecdf, deg_xmax)
            x_cs_ecdf, y_cs_ecdf = extend_ecdf_to_xmax(x_cs_ecdf, y_cs_ecdf, cs_xmax)

        degree_ecdf_series.append((label, x_deg_ecdf, y_deg_ecdf))
        degree_pmf_series.append((label, x_deg_pmf, y_deg_pmf))
        comp_ecdf_series.append((label, x_cs_ecdf, y_cs_ecdf))
        comp_pmf_series.append((label, x_cs_pmf, y_cs_pmf))

        save_points_csv(out_prefix, "degree", "ecdf", label, x_deg_ecdf, y_deg_ecdf)
        save_points_csv(out_prefix, "degree", "pmf", label, x_deg_pmf, y_deg_pmf)
        save_points_csv(out_prefix, "component", "ecdf", label, x_cs_ecdf, y_cs_ecdf)
        save_points_csv(out_prefix, "component", "pmf", label, x_cs_pmf, y_cs_pmf)

        if have_components:
            comps_vals = np.array(it["comps_vals"])
            if comps_vals.size > 0:
                x_cc_ecdf, y_cc_ecdf = ecdf_points(comps_vals)
                x_cc_pmf, y_cc_pmf = pmf_points(comps_vals)
                if args.extend_ecdf_to_global_x:
                    x_cc_ecdf, y_cc_ecdf = extend_ecdf_to_xmax(x_cc_ecdf, y_cc_ecdf, cc_xmax)
                components_ecdf_series.append((label, x_cc_ecdf, y_cc_ecdf))
                components_pmf_series.append((label, x_cc_pmf, y_cc_pmf))
                save_points_csv(out_prefix, "components", "ecdf", label, x_cc_ecdf, y_cc_ecdf)
                save_points_csv(out_prefix, "components", "pmf", label, x_cc_pmf, y_cc_pmf)

    drawstyle_pmf = None if args.pmf_style == "line" else "steps-mid"

    plot_multi(
        degree_ecdf_series,
        out_prefix.with_name(f"{out_prefix.name}_degree_ecdf.pdf"),
        xlabel="Neighborhood Degree",
        ylabel="Probability (ECDF)",
        max_xticks=int(args.max_xticks),
        xmin=deg_xmin,
        xmax=deg_xmax,
        drawstyle=None,
        ylim_top=1.0,
    )

    plot_multi(
        degree_pmf_series,
        out_prefix.with_name(f"{out_prefix.name}_degree_pmf.pdf"),
        xlabel="Neighborhood Degree",
        ylabel="Probability (PMF)",
        max_xticks=int(args.max_xticks),
        xmin=deg_xmin,
        xmax=deg_xmax,
        drawstyle=drawstyle_pmf,
        ylim_top=1.0,
    )

    plot_multi(
        comp_ecdf_series,
        out_prefix.with_name(f"{out_prefix.name}_component_ecdf.pdf"),
        xlabel="Connected Component Size",
        ylabel="Probability (ECDF)",
        max_xticks=int(args.max_xticks),
        xmin=cs_xmin,
        xmax=cs_xmax,
        drawstyle=None,
        ylim_top=1.0,
    )

    plot_multi(
        comp_pmf_series,
        out_prefix.with_name(f"{out_prefix.name}_component_pmf.pdf"),
        xlabel="Connected Component Size",
        ylabel="Probability (PMF)",
        max_xticks=int(args.max_xticks),
        xmin=cs_xmin,
        xmax=cs_xmax,
        drawstyle=drawstyle_pmf,
        ylim_top=1.0,
    )

    if have_components and len(components_ecdf_series) > 0:
        plot_multi(
            components_ecdf_series,
            out_prefix.with_name(f"{out_prefix.name}_components_ecdf.pdf"),
            xlabel="Number of Partitions",
            ylabel="Probability (ECDF)",
            max_xticks=int(args.max_xticks),
            xmin=cc_xmin,
            xmax=cc_xmax,
            drawstyle=None,
            ylim_top=1.0,
        )

        plot_multi(
            components_pmf_series,
            out_prefix.with_name(f"{out_prefix.name}_components_pmf.pdf"),
            xlabel="Number of Partitions",
            ylabel="Probability (PMF)",
            max_xticks=int(args.max_xticks),
            xmin=cc_xmin,
            xmax=cc_xmax,
            drawstyle=drawstyle_pmf,
            ylim_top=1.0,
        )


if __name__ == "__main__":
    raise SystemExit(main())
