"""Share census population among UPRNs in whole people, matching OA totals.

Targets are the postcode populations (England & Wales 2021, Scotland 2022)
and the output area populations (TS001 for England & Wales, MV302 for
Scotland), with OA totals scaled to the official country totals. Postcodes can straddle OAs, so each postcode is split into its
postcode x OA cells (seeded by summed UPRN weights from 04_uprn_weights) and
the cells are fitted
to both sets of totals by iterative proportional fitting. The two sets of
totals do not agree exactly, so the fit ends on the OA step: OA totals are
exact and postcode totals are as close as the data allows.

UPRN weights come from OS Open Zoomstack: 0 outside buildings and in
transport sites, reduced in education and medical sites, else 1. A postcode
(or OA) whose UPRNs all weigh 0 falls back to weight 1 for each, so no
census residents are lost.

Cells are rounded to whole people within each OA (largest remainder), then
each cell is shared among its weighted UPRNs in proportion to weight, again
by largest remainder with ties going to the lowest UPRN numbers.

Residents of census postcodes missing from ONSUD reach UPRNs through their
OA's total. An OA that no cell reaches is shared among its own UPRNs; the
Scottish OAs that ONSUD lacks entirely get one point each instead, at the
mean position of the UPRNs in the postcodes Scotland.csv places in the OA.

Outputs: data/processed/uprn_population.parquet with columns uprn, postcode,
population (join back to onsud_uprn.parquet on uprn for geometry and areas),
and data/processed/centroid_population.parquet, a GeoParquet of the OA
centroid points (oa, population, geometry in EPSG:27700).
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from common import largest_remainder, read_mv_total

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
CENSUS = ROOT / "data" / "census"
ONSUD = PROCESSED / "onsud_uprn.parquet"
WEIGHTS = PROCESSED / "uprn_weights.parquet"
POSTCODE_FILES = [PROCESSED / "postcode_population_england_wales.parquet",
                  PROCESSED / "postcode_population_scotland.parquet"]
SCOTLAND = CENSUS / "Scotland.csv"
SCOTLAND_OA = CENSUS / "test" / "outputarea" / "MV302 - General health by ethnic group (8) by age (6).csv"
OUT = PROCESSED / "uprn_population.parquet"
POINTS_OUT = PROCESSED / "centroid_population.parquet"
# Official usual resident totals: Census 2021 (England & Wales), Scotland's Census 2022
OFFICIAL_TOTALS = {"EW": 59_597_542, "S": 5_439_842}
IPF_ITERATIONS = 200
IPF_TOLERANCE = 0.01  # largest postcode error (people) at which to stop


def oa_totals():
    """OA populations, scaled per country to the official census totals.

    The published OA tables are perturbed, so they sum slightly above the
    official totals; the difference is removed in proportion to OA size,
    in whole people (largest remainder).
    """
    ts001 = pd.read_csv(next(CENSUS.glob("TS001-*.csv")))
    ew = ts001.groupby("Output Areas Code")["Observation"].sum()
    oa = pd.concat([ew, read_mv_total(SCOTLAND_OA)])
    country = pd.Series(np.where(oa.index.str.startswith("S"), "S", "EW"), index=oa.index)
    target = country.map(OFFICIAL_TOTALS)
    return largest_remainder(oa * target / oa.groupby(country).transform("sum"), country, target)


def centroid_points(uprn, oa_people):
    """One point per OA at the mean position of its census postcodes' UPRNs."""
    scot = pd.read_csv(SCOTLAND, usecols=["Postcode", "OutputArea2022Code"])
    pcs = scot[scot["OutputArea2022Code"].isin(oa_people.index)].set_index("Postcode")["OutputArea2022Code"]
    sel = uprn["postcode"].isin(pcs.index)
    geom = pd.read_parquet(ONSUD, columns=["uprn", "geometry"]).loc[sel.to_numpy()]
    xy = pd.DataFrame({"oa": uprn.loc[sel, "postcode"].map(pcs).to_numpy(),
                       "x": geom["geometry"].str["x"].to_numpy(),
                       "y": geom["geometry"].str["y"].to_numpy()})
    pts = xy.groupby("oa")[["x", "y"]].mean()
    pts["population"] = oa_people.reindex(pts.index).astype("int32")
    return gpd.GeoDataFrame(pts.reset_index()[["oa", "population"]],
                            geometry=gpd.points_from_xy(pts["x"], pts["y"]), crs=27700)


def spread(uprn, key, totals, weight):
    """Whole-person split of totals[area] over the UPRNs whose `key` is that area.

    People are shared in proportion to `weight` (equal weights if the whole
    area weighs 0), rounded by largest remainder with ties going to the
    lowest UPRN numbers. UPRNs outside any area in `totals` get 0.
    """
    area = uprn[key].where(uprn[key].isin(totals.index))
    sub = area.notna().to_numpy()
    a, w = area[sub], pd.Series(weight[sub], index=area.index[sub])
    w = w.where(w.groupby(a).transform("sum") > 0, 1.0)
    w = w.where(w > 0)  # weight-0 UPRNs drop out of the split
    a, w = a[w.notna()], w.dropna()
    target = a.map(totals)
    share = target * w / w.groupby(a).transform("sum")
    order = uprn["uprn"][a.index].sort_values().index  # tie-break by UPRN number
    people = largest_remainder(share[order], a[order], target[order])
    out = pd.Series(0, index=uprn.index, dtype="int64")
    out[people.index] = people
    return out


def fit(cells, pc_target, oa_target):
    """IPF of cell values to postcode and OA totals; ends on the OA step.

    Cells whose postcode has no census count (`pc_target` NaN) are only
    scaled to OA totals.
    """
    x = cells["n"].astype(float).to_numpy()
    pc_t, oa_t = pc_target.to_numpy(), oa_target.to_numpy()
    has_pc = ~np.isnan(pc_t)
    pc_codes, oa_codes = cells["pc_code"].to_numpy(), cells["oa_code"].to_numpy()
    for i in range(IPF_ITERATIONS):
        pc_sum = np.bincount(pc_codes, x)[pc_codes]
        x = np.where(has_pc, x * np.divide(pc_t, pc_sum, out=np.zeros_like(x), where=pc_sum > 0), x)
        oa_sum = np.bincount(oa_codes, x)[oa_codes]
        x = x * np.divide(oa_t, oa_sum, out=np.zeros_like(x), where=oa_sum > 0)
        pc_err = np.abs(np.bincount(pc_codes, x) - np.bincount(pc_codes, np.where(has_pc, pc_t, 0)))
        err = pc_err[np.unique(pc_codes[has_pc])].max()
        if err < IPF_TOLERANCE:
            break
    print(f"IPF: {i + 1} iterations, largest postcode error {err:.2f} people")
    return x


def main():
    pop = pd.concat([pd.read_parquet(f) for f in POSTCODE_FILES], ignore_index=True)
    assert pop["postcode"].is_unique
    pop = pop.set_index("postcode")["total"]
    oa_pop = oa_totals()
    print(f"Targets: postcodes {pop.sum():,} residents ({len(pop):,}); "
          f"OAs {oa_pop.sum():,} residents ({len(oa_pop):,})")

    uprn = pd.read_parquet(ONSUD, columns=["uprn", "pcds", "oa21cd"])
    uprn = uprn.rename(columns={"pcds": "postcode", "oa21cd": "oa"})
    weights = pd.read_parquet(WEIGHTS, columns=["uprn", "weight"])
    assert (weights["uprn"].to_numpy() == uprn["uprn"].to_numpy()).all(), "row order differs"
    uprn["w"] = weights["weight"].to_numpy("float64")
    in_pop = uprn["postcode"].map(pop).fillna(0) > 0
    zero_pc = uprn["w"].groupby(uprn["postcode"]).transform("sum").eq(0) & in_pop
    uprn.loc[zero_pc, "w"] = 1.0
    print(f"UPRN weights: {(uprn['w'] > 0).sum():,} of {len(uprn):,} UPRNs kept; "
          f"{uprn.loc[zero_pc, 'postcode'].nunique():,} census postcodes with no weighted "
          f"UPRN fall back to all their UPRNs")

    # Seed cells: UPRNs in postcodes with census residents. OAs with residents
    # but no such UPRN fall back to all their UPRNs with a postcode.
    in_census = in_pop & (uprn["w"] > 0)
    seeded_oas = uprn.loc[in_census, "oa"].unique()
    fallback = uprn["postcode"].notna() & ~uprn["oa"].isin(seeded_oas) & uprn["oa"].map(oa_pop).gt(0)
    use = in_census | fallback
    uprn.loc[fallback & (uprn["w"] == 0), "w"] = 1.0
    print(f"Fallback OAs (no census postcode among their UPRNs): "
          f"{uprn.loc[fallback, 'oa'].nunique():,}, {oa_pop[uprn.loc[fallback, 'oa'].unique()].sum():,} residents")

    cells = uprn[use].groupby(["postcode", "oa"])["w"].sum().rename("n").reset_index()
    cells = cells[cells["oa"].isin(oa_pop.index)]
    cells["pc_code"] = cells["postcode"].astype("category").cat.codes
    cells["oa_code"] = cells["oa"].astype("category").cat.codes
    print(f"{len(cells):,} postcode x OA cells; "
          f"{(cells.groupby('postcode').size() > 1).sum():,} postcodes span more than one OA")

    x = fit(cells, cells["postcode"].map(pop).where(cells["postcode"].map(pop) > 0),
            cells["oa"].map(oa_pop))
    cells["people"] = largest_remainder(pd.Series(x, index=cells.index), cells["oa"],
                                        cells["oa"].map(oa_pop))

    uprn["cell"] = (uprn["postcode"] + "|" + uprn["oa"]).where(use)
    cell_totals = cells.set_index(cells["postcode"] + "|" + cells["oa"])["people"]
    uprn["population"] = spread(uprn, "cell", cell_totals, uprn["w"].to_numpy()).astype("int32")

    # OAs no cell reached: use the OA's own UPRNs if ONSUD has any. Otherwise
    # (Scottish OAs missing from ONSUD) put all the OA's residents on one
    # point: the mean position of the UPRNs in the postcodes that Scotland.csv
    # places in that OA, standing in for the OA centroid.
    unreached = oa_pop[oa_pop.index.difference(cells["oa"])]
    unreached = unreached[unreached > 0]
    own = unreached[unreached.index.isin(uprn["oa"])]
    uprn["population"] += spread(uprn, "oa", own, uprn["w"].to_numpy()).astype("int32")
    no_uprn = unreached.drop(own.index)
    points = centroid_points(uprn, no_uprn)
    print(f"OAs no cell reached: {len(unreached):,} ({unreached.sum():,} residents); "
          f"{own.sum():,} on the OA's own UPRNs, {points['population'].sum():,} on "
          f"{len(points):,} centroid points, "
          f"{no_uprn.sum() - points['population'].sum():,} not placed")

    oa_sum = uprn.groupby("oa")["population"].sum()
    pc_sum = uprn.groupby("postcode")["population"].sum()
    common = pc_sum.index.intersection(pop.index)
    pc_diff = pc_sum[common] - pop[common]
    print(f"OA totals exact: {(oa_sum.reindex(oa_pop.index, fill_value=0) == oa_pop).mean():.2%} "
          f"(by ONSUD OA, so OAs filled via their census postcodes count as misses)")
    print(f"Postcode totals: exact {(pc_diff == 0).mean():.2%}, within 1 "
          f"{(pc_diff.abs() <= 1).mean():.2%}, within 5 {(pc_diff.abs() <= 5).mean():.2%}, "
          f"total |diff| {pc_diff.abs().sum():,}")
    print(f"Allocated to UPRNs {uprn['population'].sum():,} residents, centroid points "
          f"{points['population'].sum():,}; total {uprn['population'].sum() + points['population'].sum():,}")

    uprn = uprn[["uprn", "postcode", "population"]]
    uprn.to_parquet(OUT, index=False)
    print(uprn.head(10).to_string(index=False))
    points.to_parquet(POINTS_OUT, index=False)
    print(f"\n-> {OUT} ({OUT.stat().st_size / 1e6:.0f} MB)\n-> {POINTS_OUT} ({len(points)} points)")


if __name__ == "__main__":
    main()
