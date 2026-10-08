"""Combine the ONS UPRN Directory (ONSUD) regional CSVs into one GeoParquet file.

Keeps the UPRN, its postcode, and the country, region, local authority and
Census 2021 output area codes (OA, LSOA, MSOA, OA classification). Easting and
northing become point geometry in British National Grid (EPSG:27700), stored
with GeoParquet's native point encoding so readers can filter by bounding box.
Placeholder codes such as E99999999 ("not applicable") become nulls.

Regional files are read one at a time and written as separate row groups,
each sorted by local authority and output area, so the full 11 GB of CSV is
never in memory at once.
"""

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv
import pyarrow.parquet as pq
from pyproj import CRS

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "data" / "uprn_postcode"
OUT_PATH = ROOT / "data" / "processed" / "onsud_uprn.parquet"

CODE_COLUMNS = ["PCDS", "CTRY26CD", "RGN26CD", "LAD26CD", "OA21CD", "LSOA21CD",
                "MSOA21CD", "OAC21IND"]
COLUMNS = ["UPRN", "GRIDGB1E", "GRIDGB1N"] + CODE_COLUMNS
PLACEHOLDER = r"^[EWSNL]99999999$"


def read_region(path):
    table = pv.read_csv(
        path,
        read_options=pv.ReadOptions(use_threads=True, block_size=64 << 20),
        convert_options=pv.ConvertOptions(
            include_columns=COLUMNS,
            column_types={"UPRN": pa.int64(), "GRIDGB1E": pa.float64(),
                          "GRIDGB1N": pa.float64(),
                          **{c: pa.string() for c in CODE_COLUMNS}},
            strings_can_be_null=True),
    )
    cols = {"uprn": table["UPRN"]}
    for c in CODE_COLUMNS:
        col = pc.utf8_trim_whitespace(table[c])
        empty = pc.or_(pc.equal(col, ""), pc.match_substring_regex(col, PLACEHOLDER))
        cols[c.lower()] = pc.if_else(empty, pa.scalar(None, pa.string()), col)
    x = table["GRIDGB1E"].to_numpy()
    y = table["GRIDGB1N"].to_numpy()
    cols["geometry"] = pa.StructArray.from_arrays([pa.array(x), pa.array(y)], names=["x", "y"])
    out = pa.table(cols)
    order = pc.sort_indices(out, sort_keys=[("lad26cd", "ascending"), ("oa21cd", "ascending"),
                                            ("uprn", "ascending")])
    return out.take(order), (x.min(), y.min(), x.max(), y.max())


def geo_metadata(bbox):
    return {"version": "1.1.0", "primary_column": "geometry",
            "columns": {"geometry": {"encoding": "point", "geometry_types": ["Point"],
                                     "crs": CRS.from_epsg(27700).to_json_dict(),
                                     "bbox": [float(v) for v in bbox]}}}


def main():
    files = sorted(SRC_DIR.glob("ONSUD_*.csv"))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".tmp.parquet")
    writer, bounds, total = None, [], 0
    for f in files:
        table, bbox = read_region(f)
        if writer is None:
            writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
        writer.write_table(table, row_group_size=1_000_000)
        bounds.append(bbox); total += table.num_rows
        print(f"{f.name}: {table.num_rows:,} rows", flush=True)
    writer.close()

    # Rewrite the footer with GeoParquet metadata (needs the overall bbox)
    b = np.array(bounds)
    bbox = (b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max())
    with pq.ParquetFile(tmp) as pf:
        schema = pf.schema_arrow.with_metadata({b"geo": json.dumps(geo_metadata(bbox)).encode()})
        with pq.ParquetWriter(OUT_PATH, schema, compression="zstd") as w:
            for i in range(pf.num_row_groups):
                w.write_table(pf.read_row_group(i).cast(schema))
    tmp.unlink()
    print(f"\n{total:,} rows -> {OUT_PATH} ({OUT_PATH.stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
