import argparse
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


class UnionFind:
    def __init__(self, n: int):
        self.parent = np.arange(n, dtype=np.int32)
        self.size = np.ones(n, dtype=np.int32)

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: int, b: int):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]

    def comp_sizes_per_node(self) -> np.ndarray:
        roots = np.array([self.find(i) for i in range(len(self.parent))], dtype=np.int32)
        return self.size[roots]


def compute_for_time_snapshot(pos: np.ndarray, range_m: float):
    n = pos.shape[0]
    valid = np.isfinite(pos[:, 0]) & np.isfinite(pos[:, 1])
    degrees = np.full(n, np.nan, dtype=np.float64)
    comp_sizes = np.full(n, np.nan, dtype=np.float64)

    if valid.sum() == 0:
        return degrees, comp_sizes

    idx = np.where(valid)[0]
    p = pos[idx]
    r2 = range_m * range_m

    dx = p[:, 0][:, None] - p[:, 0][None, :]
    dy = p[:, 1][:, None] - p[:, 1][None, :]
    d2 = dx * dx + dy * dy

    adj = (d2 <= r2)
    np.fill_diagonal(adj, False)

    deg = adj.sum(axis=1).astype(np.int32)

    uf = UnionFind(len(idx))
    triu = np.triu_indices(len(idx), k=1)
    edges = np.where(adj[triu])[0]
    for k in edges:
        a = int(triu[0][k])
        b = int(triu[1][k])
        uf.union(a, b)

    cs = uf.comp_sizes_per_node().astype(np.int32)

    degrees[idx] = deg
    comp_sizes[idx] = cs
    return degrees, comp_sizes


def _load_run_gps(run_dir: Path, bin_s: float) -> pd.DataFrame:
    files = sorted(run_dir.glob("node_*.gps.csv"))
    if not files:
        raise SystemExit(f"[ERROR] No node_*.gps.csv found in {run_dir}")

    dfs = []
    for fp in files:
        df = pd.read_csv(fp)
        for c in ["time_s", "node", "x_m", "y_m", "ok"]:
            if c not in df.columns:
                raise SystemExit(f"[ERROR] Missing column {c} in {fp}")

        df["time_s"] = pd.to_numeric(df["time_s"], errors="coerce")
        df["node"] = pd.to_numeric(df["node"], errors="coerce")
        df["x_m"] = pd.to_numeric(df["x_m"], errors="coerce")
        df["y_m"] = pd.to_numeric(df["y_m"], errors="coerce")
        df["ok"] = pd.to_numeric(df["ok"], errors="coerce").fillna(0).astype(int)

        df = df.dropna(subset=["time_s", "node"])
        df["node"] = df["node"].astype(int)

        df = df[df["ok"] == 1].copy()
        if df.empty:
            continue

        df["time_bin_s"] = (df["time_s"] / bin_s).round() * bin_s
        dfs.append(df[["time_bin_s", "node", "x_m", "y_m"]])

    if not dfs:
        return pd.DataFrame(columns=["time_bin_s", "node", "x_m", "y_m"])

    return pd.concat(dfs, ignore_index=True)


def _compute_degree_table(mob: pd.DataFrame, range_m: float, max_time_s: Optional[float]) -> pd.DataFrame:
    if mob.empty:
        return pd.DataFrame(columns=["time_bin_s", "node", "degree", "component_size", "is_isolated"])

    if max_time_s is not None:
        mob = mob[mob["time_bin_s"] <= float(max_time_s)]

    nodes = np.sort(mob["node"].unique())
    times = np.sort(mob["time_bin_s"].unique())

    node_to_idx = {int(n): i for i, n in enumerate(nodes)}

    rows = []
    for t in times:
        g = mob[mob["time_bin_s"] == t]
        pos = np.full((len(nodes), 2), np.nan, dtype=np.float64)
        for _, r in g.iterrows():
            i = node_to_idx[int(r["node"])]
            pos[i, 0] = float(r["x_m"])
            pos[i, 1] = float(r["y_m"])

        deg, cs = compute_for_time_snapshot(pos, range_m)

        for i, nid in enumerate(nodes):
            if np.isfinite(deg[i]):
                di = int(deg[i])
                csi = int(cs[i])
                rows.append({
                    "time_bin_s": float(t),
                    "node": int(nid),
                    "degree": di,
                    "component_size": csi,
                    "is_isolated": int(di == 0),
                })

    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_dir", default=None)
    ap.add_argument("--runs_root", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--range_m", required=True, type=float)
    ap.add_argument("--bin_s", default=1.0, type=float)
    ap.add_argument("--max_time_s", default=None, type=float)
    args = ap.parse_args()

    if (args.run_dir is None) == (args.runs_root is None):
        raise SystemExit("[ERROR] Provide exactly one of --run_dir or --runs_root")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.run_dir is not None:
        run_dir = Path(args.run_dir)
        mob = _load_run_gps(run_dir, args.bin_s)
        out = _compute_degree_table(mob, args.range_m, args.max_time_s)
        out.insert(0, "run", run_dir.name)
        out.to_csv(out_path, index=False)
        print(f"[OK] Wrote: {out_path} (runs=1, bin_s={args.bin_s}, range_m={args.range_m}, rows={len(out)})")
        return 0

    runs_root = Path(args.runs_root)
    run_dirs = sorted([d for d in runs_root.glob("run_*") if d.is_dir()])
    if not run_dirs:
        raise SystemExit(f"[ERROR] No run_* directories found in {runs_root}")

    all_rows = []
    used_runs = 0
    for rd in run_dirs:
        mob = _load_run_gps(rd, args.bin_s)
        if mob.empty:
            continue
        tab = _compute_degree_table(mob, args.range_m, args.max_time_s)
        if tab.empty:
            continue
        tab.insert(0, "run", rd.name)
        all_rows.append(tab)
        used_runs += 1

    if not all_rows:
        raise SystemExit("[ERROR] No valid degree samples across runs (ok==1).")

    out = pd.concat(all_rows, ignore_index=True)
    out.to_csv(out_path, index=False)
    print(f"[OK] Wrote: {out_path} (runs_found={len(run_dirs)}, runs_used={used_runs}, bin_s={args.bin_s}, range_m={args.range_m}, rows={len(out)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
