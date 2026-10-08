"""Open the population raster in GeoLibre in your web browser.

GeoLibre (https://github.com/opengeos/geolibre) runs on your machine: this
script starts its small local web server, which serves the GeoLibre app and
reads data/processed/population_web_z13.tif (from 08_web_raster.py) straight
from disk. It then writes a page that loads the map and opens it in your
browser. Keep the script running while you use the map; Ctrl+C stops it.

To see the population of a cell, click the Identify tool (the pointer icon
at the top of the Layers panel) and then click the map: Band 1 is the number
of residents in that cell.

Usage: python scripts/09_geolibre_map.py
"""

import importlib.util
import os
import time
import webbrowser
from pathlib import Path

# Use rasterio's bundled PROJ database, not one from another install (e.g.
# PostGIS) that a system-wide PROJ_LIB may point to
_proj = Path(importlib.util.find_spec("rasterio").origin).parent / "proj_data"
if _proj.exists():
    os.environ.pop("PROJ_LIB", None)
    os.environ["PROJ_DATA"] = str(_proj)

import geolibre
import geolibre._server

# GeoLibre's local server keeps Python's default backlog of 5 waiting
# connections; a fast (GPU) browser fires dozens of tile requests at once and
# the rest are refused, leaving holes in the map. Must be set before the
# first Map starts the server.
geolibre._server._QuietServer.request_queue_size = 128

ROOT = Path(__file__).resolve().parents[1]
RASTER = ROOT / "data" / "processed" / "population_web_z13.tif"
PAGE = ROOT / "data" / "processed" / "geolibre_map.html"
GB_BOUNDS = [-9.0, 49.5, 2.5, 61.0]  # lon/lat box the map cannot be panned out of


def main():
    assert RASTER.exists(), f"{RASTER} not found: run scripts/08_web_raster.py first"
    m = geolibre.Map(center=[-2.5, 54.5], zoom=6, basemap="fiord", layout="full")
    # Colour ramp from 1 to 20 people per cell: 99% of populated cells hold
    # 16 or fewer, so busier cells all show in the darkest red
    m.add_cog(RASTER, "Population", colormap="reds", rescale=[[1, 20]])
    # Lock the view to GB: no panning outside GB_BOUNDS or zooming out past it
    prefs = m.project["preferences"]
    prefs["map"].update(restrictBounds=True, bounds=GB_BOUNDS, minZoom=4.5)
    m.project = {**m.project, "preferences": prefs}
    # The page points at this session's local server, so it only works while
    # the script runs
    m.to_html(str(PAGE), title="Population map", height="100vh", app_url=m._app_url)
    webbrowser.open(PAGE.as_uri())
    print(f"Map open in your browser ({PAGE}). Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
