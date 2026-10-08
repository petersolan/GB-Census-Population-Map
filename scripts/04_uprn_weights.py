"""Weight each UPRN by whether it is likely to be a home, from OS Open Zoomstack.

- local_buildings: a UPRN must lie in a building (or within BUILDING_TOLERANCE
  metres of one, for small positional offsets); otherwise its weight is 0.
- sites: UPRNs in transport sites get 0; in education and medical care sites
  they get a reduced weight, because those sites hold halls of residence and
  care homes as well as classrooms and wards.

Inputs are the Zoomstack layers exported to GeoParquet (data/buildings/).
Buildings are read one spatially sorted row group at a time.

Output: data/processed/uprn_weights.parquet with columns uprn, in_building,
site, weight, in the same row order as onsud_uprn.parquet.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely

ROOT = Path(__file__).resolve().parents[1]
ONSUD = ROOT / "data" / "processed" / "onsud_uprn.parquet"
BUILDINGS = ROOT / "data" / "buildings" / "local_buildings.parquet"
SITES = ROOT / "data" / "buildings" / "sites.parquet"
OUT = ROOT / "data" / "processed" / "uprn_weights.parquet"
BUILDING_TOLERANCE = 2.0  # metres
SITE_WEIGHTS = {"Education": 0.25, "Medical Care": 0.25,
                "Air Transport": 0.0, "Road Transport": 0.0, "Water Transport": 0.0}


def geometry_columns(path):
    """Names of the WKB geometry column and its GeoParquet bbox covering column."""
    geo = json.loads(pq.read_schema(path).metadata[b"geo"])
    col = geo["primary_column"]
    cover = geo["columns"][col].get("covering", {}).get("bbox", {})
    return col, cover.get("xmin", [None])[0]


def in_buildings(x, y):
    """True where the point is in, or within BUILDING_TOLERANCE of, a building.

    The buildings file is spatially sorted, so each row group covers a compact
    area: it is read once, and only the points inside its extent are tested.
    """
    geom_col, bbox_col = geometry_columns(BUILDINGS)
    pf = pq.ParquetFile(BUILDINGS)
    inside = np.zeros(len(x), dtype=bool)
    order = np.argsort(x, kind="stable")
    xs = x[order]
    pad = BUILDING_TOLERANCE
    for i in range(pf.num_row_groups):
        rg = pf.read_row_group(i, columns=[geom_col, bbox_col])
        bb = rg[bbox_col].combine_chunks()
        x0, x1 = pc.min(bb.field("xmin")).as_py() - pad, pc.max(bb.field("xmax")).as_py() + pad
        y0, y1 = pc.min(bb.field("ymin")).as_py() - pad, pc.max(bb.field("ymax")).as_py() + pad
        cand = order[np.searchsorted(xs, x0):np.searchsorted(xs, x1, side="right")]
        cand = cand[(y[cand] >= y0) & (y[cand] <= y1) & ~inside[cand]]
        if len(cand):
            tree = shapely.STRtree(shapely.from_wkb(rg[geom_col].to_numpy()))
            pts = shapely.points(x[cand], y[cand])
            hit = np.unique(tree.query(pts, predicate="intersects")[0])
            rest = np.setdiff1d(np.arange(len(cand)), hit)
            near = np.unique(tree.query(pts[rest], predicate="dwithin", distance=pad)[0])
            inside[cand[hit]] = True
            inside[cand[rest[near]]] = True
        print(f"  row group {i + 1}/{pf.num_row_groups}: {len(cand):,} candidates, "
              f"{inside.sum():,} UPRNs in buildings so far", flush=True)
    return inside


def site_types(x, y):
    """Site type per point (the lowest-weight one where sites overlap), else None."""
    geom_col, _ = geometry_columns(SITES)
    sites = pq.read_table(SITES, columns=[geom_col, "type"])
    tree = shapely.STRtree(shapely.from_wkb(sites[geom_col].to_numpy()))
    types = np.array(sites["type"].to_pylist(), dtype=object)
    weight = pd.Series(SITE_WEIGHTS)
    best = pd.Series(1.0, index=range(len(x)))
    site = np.full(len(x), None, dtype=object)
    for start in range(0, len(x), 2_000_000):
        pts = shapely.points(x[start:start + 2_000_000], y[start:start + 2_000_000])
        p, s = tree.query(pts, predicate="intersects")
        hits = pd.DataFrame({"p": p + start, "type": types[s]})
        hits["w"] = hits["type"].map(weight).fillna(1.0)
        hits = hits.sort_values("w").drop_duplicates("p")
        site[hits["p"].to_numpy()] = hits["type"].to_numpy()
        best.iloc[hits["p"].to_numpy()] = hits["w"].to_numpy()
    return site, best.to_numpy()


def main():
    onsud = pq.read_table(ONSUD, columns=["uprn", "geometry"])
    g = onsud["geometry"].combine_chunks()
    x, y = g.field("x").to_numpy(), g.field("y").to_numpy()

    inside = in_buildings(x, y)
    print(f"UPRNs in a building (<= {BUILDING_TOLERANCE:g} m): {inside.sum():,} of {len(x):,} "
          f"({inside.mean():.1%})")
    site, site_weight = site_types(x, y)
    weight = (inside * site_weight).astype("float32")
    counts = pd.Series(site).value_counts()
    print("UPRNs by site type:", counts.to_dict())
    print(f"Weight 1: {(weight == 1).sum():,}; reduced: {((weight > 0) & (weight < 1)).sum():,}; "
          f"0: {(weight == 0).sum():,}")

    pd.DataFrame({"uprn": onsud["uprn"].to_numpy(), "in_building": inside,
                  "site": pd.array(site, dtype="string"), "weight": weight}
                 ).to_parquet(OUT, index=False)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
