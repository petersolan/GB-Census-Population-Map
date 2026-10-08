"""Population rasters at 25 m and 10 m from the combined location points.

Each cell holds the summed population of the points inside it (UInt16;
0 = no people, set as nodata so empty land is transparent). Grids are aligned
to the British National Grid origin and cover GB.

How: every point gets a cell index (row * width + col); points are sorted by
that index and summed per cell. The raster is then written in horizontal
bands, filling only the populated cells, and converted to a Cloud Optimized
GeoTIFF (COG) with overviews so viewers like QGIS and GeoLibre can zoom
smoothly.

Outputs: data/processed/population_25m.tif, population_10m.tif (EPSG:27700).
"""

import importlib.util
import os
from pathlib import Path

# Use rasterio's bundled PROJ database, not one from another install (e.g.
# PostGIS) that a system-wide PROJ_LIB may point to
_proj = Path(importlib.util.find_spec("rasterio").origin).parent / "proj_data"
if _proj.exists():
    os.environ.pop("PROJ_LIB", None)
    os.environ["PROJ_DATA"] = str(_proj)

import numpy as np
import pyarrow.parquet as pq
import rasterio
from rasterio.shutil import copy as rio_copy
from rasterio.transform import from_origin
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
POINTS = PROCESSED / "location_population_points.parquet"
XMIN, YMIN, XMAX, YMAX = 0, 0, 660_000, 1_220_000  # metres, divisible by both sizes
SIZES = [25, 10]
BAND_ROWS = 2048  # rows written per band


def cell_sums(x, y, people, size):
    """Populated cells as (sorted cell index, summed population)."""
    width = (XMAX - XMIN) // size
    col = ((x - XMIN) // size).astype(np.int64)
    row = ((YMAX - y) // size).astype(np.int64)
    index = row * width + col
    order = np.argsort(index, kind="stable")
    index, people = index[order], people[order]
    starts = np.flatnonzero(np.r_[True, index[1:] != index[:-1]])
    return index[starts], np.add.reduceat(people, starts)


def write_raster(cells, sums, size, path):
    width, height = (XMAX - XMIN) // size, (YMAX - YMIN) // size
    tmp = path.with_suffix(".tmp.tif")
    profile = dict(driver="GTiff", width=width, height=height, count=1, dtype="uint16",
                   nodata=0, crs="EPSG:27700", transform=from_origin(XMIN, YMAX, size, size),
                   tiled=True, blockxsize=512, blockysize=512, compress="zstd", BIGTIFF="YES")
    with rasterio.open(tmp, "w", **profile) as dst:
        for top in range(0, height, BAND_ROWS):
            rows = min(BAND_ROWS, height - top)
            lo, hi = np.searchsorted(cells, [top * width, (top + rows) * width])
            band = np.zeros(rows * width, dtype="uint16")
            band[cells[lo:hi] - top * width] = sums[lo:hi]
            dst.write(band.reshape(rows, width), 1, window=Window(0, top, width, rows))
    rio_copy(tmp, path, driver="COG", compress="ZSTD", overview_resampling="average",
             BIGTIFF="YES", num_threads="ALL_CPUS")
    tmp.unlink()


def main():
    table = pq.read_table(POINTS, columns=["population", "geometry"])
    geom = table["geometry"].combine_chunks()
    x, y = geom.field("x").to_numpy(), geom.field("y").to_numpy()
    people = table["population"].to_numpy().astype(np.int64)
    print(f"{len(people):,} points, {people.sum():,} residents")

    for size in SIZES:
        cells, sums = cell_sums(x, y, people, size)
        assert sums.max() <= np.iinfo(np.uint16).max, "cell population exceeds UInt16"
        out = PROCESSED / f"population_{size}m.tif"
        write_raster(cells, sums, size, out)
        print(f"-> {out} ({len(cells):,} populated cells, {sums.sum():,} residents, "
              f"max {sums.max():,} per cell, {out.stat().st_size / 1e6:.0f} MB)", flush=True)


if __name__ == "__main__":
    main()
