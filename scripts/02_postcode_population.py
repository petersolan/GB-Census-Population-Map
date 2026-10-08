"""Census 2021 usual residents per postcode (table P001, by sex).

Sums the counts for each postcode and writes one row per postcode with two
columns: postcode, total. Output goes to
data/processed/postcode_population_england_wales.parquet and .csv (the
Census 2021 table covers England and Wales only).
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "census" / "EnglandWales.csv"
OUT = ROOT / "data" / "processed" / "postcode_population_england_wales"


def main():
    df = pd.read_csv(SRC, dtype={"Postcode": str}, encoding="utf-8-sig")
    df["Postcode"] = df["Postcode"].str.strip()
    wide = (df.pivot_table(index="Postcode", columns="Sex (2 categories) Label",
                           values="Count", aggfunc="sum", fill_value=0)
              .rename(columns=str.lower))
    wide["total"] = wide.sum(axis=1)
    wide = wide.reset_index().rename(columns={"Postcode": "postcode"})
    wide.columns.name = None

    rows_per_pc = df.groupby("Postcode").size()
    print(f"{len(df):,} input rows -> {len(wide):,} postcodes "
          f"(rows per postcode: {rows_per_pc.value_counts().to_dict()})")
    print(f"Total residents {wide['total'].sum():,} "
          f"(female {wide['female'].sum():,}, male {wide['male'].sum():,})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wide = wide[["postcode", "total"]]
    wide.to_parquet(OUT.with_suffix(".parquet"), index=False)
    wide.to_csv(OUT.with_suffix(".csv"), index=False)
    print(wide.head(10).to_string(index=False))
    print(f"\n-> {OUT.with_suffix('.parquet')}\n-> {OUT.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
