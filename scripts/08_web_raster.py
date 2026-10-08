"""Population raster on the web map grid, for GeoLibre and other web viewers.

Web maps draw in Web Mercator (EPSG:3857) using a fixed pyramid of 256 px
tiles. Rather than warping the British National Grid raster (which resamples
the counts), the combined location points are projected to Web Mercator and
summed straight into the cells of zoom level 13 of that pyramid: about 10-12 m
on the ground across GB, the closest match to the 10 m raster. Every cell lines
up with a map tile, so viewers read it without resampling.

Full-resolution values are whole people; 0 is nodata. Overviews average the
populated cells only (GDAL skips nodata), so zoomed out a pixel shows the mean
people per populated cell and sparse rural areas stay visible; Float32 keeps
those means unrounded.

Output: data/processed/population_web_z13.tif (EPSG:3857).
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
from pyproj import Transformer
from rasterio.shutil import copy as rio_copy
from rasterio.transform import from_origin
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
POINTS = PROCESSED / "location_population_points.parquet"
OUT = PROCESSED / "population_web_z13.tif"
ZOOM = 13
TILE = 256
ORIGIN = 20037508.342789244  # Web Mercator half-width (m)
RES = 2 * ORIGIN / TILE / 2**ZOOM  # 19.109 m
BAND_ROWS = 2048


def main():
    table = pq.read_table(POINTS, columns=["population", "geometry"])
    geom = table["geometry"].combine_chunks()
    people = table["population"].to_numpy().astype(np.int64)
    x, y = Transformer.from_crs(27700, 3857, always_xy=True).transform(
        geom.field("x").to_numpy(), geom.field("y").to_numpy())
    print(f"{len(people):,} points, {people.sum():,} residents")

    # Cell of each point in the global zoom-13 grid, then crop to whole tiles
    col = np.floor((x + ORIGIN) / RES).astype(np.int64)
    row = np.floor((ORIGIN - y) / RES).astype(np.int64)
    col0, row0 = col.min() // TILE * TILE, row.min() // TILE * TILE
    width = (col.max() // TILE + 1) * TILE - col0
    height = (row.max() // TILE + 1) * TILE - row0
    index = (row - row0) * width + (col - col0)
    order = np.argsort(index, kind="stable")
    index, people = index[order], people[order]
    starts = np.flatnonzero(np.r_[True, index[1:] != index[:-1]])
    cells, sums = index[starts], np.add.reduceat(people, starts)

    tmp = OUT.with_suffix(".tmp.tif")
    profile = dict(driver="GTiff", width=width, height=height, count=1, dtype="float32",
                   nodata=0, crs="EPSG:3857",
                   transform=from_origin(col0 * RES - ORIGIN, ORIGIN - row0 * RES, RES, RES),
                   tiled=True, blockxsize=TILE, blockysize=TILE, compress="zstd", BIGTIFF="YES")
    with rasterio.open(tmp, "w", **profile) as dst:
        # Band name shown by viewers (GeoLibre Identify, QGIS) instead of "Band 1"
        dst.set_band_description(1, "Population")
        for top in range(0, height, BAND_ROWS):
            rows = min(BAND_ROWS, height - top)
            lo, hi = np.searchsorted(cells, [top * width, (top + rows) * width])
            band = np.zeros(rows * width, dtype="float32")
            band[cells[lo:hi] - top * width] = sums[lo:hi]
            dst.write(band.reshape(rows, width), 1, window=Window(0, top, width, rows))
    # Grid already matches GoogleMapsCompatible zoom 13, so this only adds overviews
    rio_copy(tmp, OUT, driver="COG", TILING_SCHEME="GoogleMapsCompatible", ZOOM_LEVEL=ZOOM,
             RESAMPLING="nearest", OVERVIEW_RESAMPLING="average", COMPRESS="ZSTD",
             PREDICTOR="FLOATING_POINT", BIGTIFF="YES", NUM_THREADS="ALL_CPUS")
    tmp.unlink()
    print(f"-> {OUT} ({width:,} x {height:,} px, {len(cells):,} populated cells, "
          f"{sums.sum():,} residents, max {sums.max():,} per cell, "
          f"{OUT.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
