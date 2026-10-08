"""Scotland's Census 2022 usual residents per postcode.

The postcode file (Scotland.csv) counts people in households only. People in
communal establishments (halls of residence, care homes, prisons, ...) are
added per output area as MV302 "All people" minus MV801 "All people in
households" (negatives from NRS perturbation are clipped to 0). Each OA's
communal count is shared among that OA's postcodes in proportion to their
number of UPRNs in ONSUD, rounded to whole people so OA totals stay exact.

Output: data/processed/postcode_population_scotland.parquet and .csv with two
columns: postcode, total.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from common import largest_remainder, read_mv_total

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "census" / "Scotland.csv"
MV_DIR = ROOT / "data" / "census" / "test" / "outputarea"
ALL_PEOPLE = MV_DIR / "MV302 - General health by ethnic group (8) by age (6).csv"
HOUSEHOLD_PEOPLE = MV_DIR / "MV801 - Length of residence in the UK by household tenure - People.csv"
ONSUD = ROOT / "data" / "processed" / "onsud_uprn.parquet"
OUT = ROOT / "data" / "processed" / "postcode_population_scotland"


def main():
    pc = pd.read_csv(SRC, dtype={"Postcode": str})
    pc["Postcode"] = pc["Postcode"].str.strip()

    communal = (read_mv_total(ALL_PEOPLE) - read_mv_total(HOUSEHOLD_PEOPLE)).clip(lower=0)
    print(f"Household residents {pc['PopulationCount'].sum():,}; "
          f"communal residents {communal.sum():,} in {(communal > 0).sum():,} OAs")

    onsud = pd.read_parquet(ONSUD, columns=["pcds", "ctry26cd"],
                            filters=[("ctry26cd", "=", "S92000003")])
    pc["uprns"] = pc["Postcode"].map(onsud.groupby("pcds").size()).fillna(0)

    oa = pc["OutputArea2022Code"]
    pc["communal_target"] = oa.map(communal).fillna(0).astype(int)
    oa_uprns = pc["uprns"].groupby(oa).transform("sum")
    oa_pcs = pc.groupby("OutputArea2022Code")["Postcode"].transform("size")
    # Postcodes without UPRNs share equally if the whole OA has none
    weight = np.where(oa_uprns > 0, pc["uprns"] / oa_uprns.where(oa_uprns > 0, 1), 1 / oa_pcs)
    pc["communal"] = largest_remainder(pd.Series(weight * pc["communal_target"], index=pc.index),
                                       oa, pc["communal_target"])
    missing_oas = communal.index.difference(oa.unique())
    if len(missing_oas):
        print(f"WARNING: {communal[missing_oas].sum():,} communal residents in "
              f"{len(missing_oas)} OAs with no postcode")

    out = (pc.assign(total=pc["PopulationCount"] + pc["communal"])
             .rename(columns={"Postcode": "postcode"})[["postcode", "total"]])
    print(f"{len(out):,} postcodes, total residents {out['total'].sum():,} "
          f"(communal allocated {pc['communal'].sum():,})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT.with_suffix(".parquet"), index=False)
    out.to_csv(OUT.with_suffix(".csv"), index=False)
    print(out.head(10).to_string(index=False))
    print(f"\n-> {OUT.with_suffix('.parquet')}\n-> {OUT.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
