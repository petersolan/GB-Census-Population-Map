"""Population layers for viewing in QGIS.

- uprn_population_points.parquet: GeoParquet of UPRNs with population > 0
  (uprn, postcode, population, point geometry), plus the OA centroid points.
- location_population_points.parquet: GeoParquet with one point per distinct
  location; populated UPRNs at exactly the same coordinates (e.g. flats in
  one block) are combined (population summed, uprns = number combined).

All in EPSG:27700.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
ONSUD = PROCESSED / "onsud_uprn.parquet"
UPRN_POP = PROCESSED / "uprn_population.parquet"
CENTROIDS = PROCESSED / "centroid_population.parquet"
POINTS_OUT = PROCESSED / "uprn_population_points.parquet"
LOCATIONS_OUT = PROCESSED / "location_population_points.parquet"


def main():
    onsud = pq.read_table(ONSUD, columns=["uprn", "geometry"])
    pop = pq.read_table(UPRN_POP, columns=["uprn", "postcode", "population"])
    assert onsud["uprn"].equals(pop["uprn"]), "UPRN order differs between files"

    x = onsud["geometry"].combine_chunks().field("x").to_numpy()
    y = onsud["geometry"].combine_chunks().field("y").to_numpy()
    people = pop["population"].to_numpy()

    # Add the OA centroid points so the layers hold everyone
    cent = gpd.read_parquet(CENTROIDS)
    x = np.concatenate([x, cent.geometry.x.to_numpy()])
    y = np.concatenate([y, cent.geometry.y.to_numpy()])
    people = np.concatenate([people, cent["population"].to_numpy()])
    uprn = np.concatenate([pop["uprn"].to_numpy(), np.full(len(cent), -1)])
    postcode = pa.concat_arrays([pop["postcode"].combine_chunks(),
                                 pa.array([None] * len(cent), pa.string())])
    keep = people > 0
    print(f"{keep.sum():,} populated points, {people.sum():,} residents")

    # Points: same native GeoParquet point encoding as onsud_uprn.parquet
    geom = pa.StructArray.from_arrays([pa.array(x[keep]), pa.array(y[keep])], names=["x", "y"])
    table = pa.table({"uprn": uprn[keep], "postcode": postcode.filter(pa.array(keep)),
                      "population": people[keep].astype("int32"), "geometry": geom})
    meta = pq.read_schema(ONSUD).metadata
    pq.write_table(table.replace_schema_metadata(meta), POINTS_OUT, compression="zstd",
                   row_group_size=500_000)
    print(f"-> {POINTS_OUT} ({POINTS_OUT.stat().st_size / 1e6:.0f} MB; centroid points have uprn -1)")

    loc = (pd.DataFrame({"x": x[keep], "y": y[keep], "population": people[keep]})
             .groupby(["x", "y"])["population"].agg(population="sum", uprns="size")
             .reset_index())
    geom = pa.StructArray.from_arrays([pa.array(loc["x"]), pa.array(loc["y"])], names=["x", "y"])
    table = pa.table({"population": loc["population"].to_numpy("int32"),
                      "uprns": loc["uprns"].to_numpy("int32"), "geometry": geom})
    pq.write_table(table.replace_schema_metadata(meta), LOCATIONS_OUT, compression="zstd",
                   row_group_size=500_000)
    shared = loc["uprns"] > 1
    print(f"-> {LOCATIONS_OUT} ({len(loc):,} locations; {shared.sum():,} combine "
          f"{loc.loc[shared, 'uprns'].sum():,} UPRNs; largest {loc['uprns'].max():,} UPRNs, "
          f"{LOCATIONS_OUT.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
