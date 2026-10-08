"""Helpers shared by the census scripts."""

import io
import re

import numpy as np
import pandas as pd


def read_mv_total(path):
    """First (total) column of an NRS multivariate table, indexed by OA code."""
    lines = path.read_text(encoding="utf-8").splitlines()
    body = [l for l in lines if re.match(r'^"?S00\d+', l)]
    df = pd.read_csv(io.StringIO("\n".join(body)), header=None, usecols=[0, 1], na_values="-")
    return pd.to_numeric(df[1]).fillna(0).astype(int).set_axis(df[0].str.strip('"'))


def largest_remainder(shares, group, target):
    """Round float shares to ints so each group sums exactly to its target."""
    floor = np.floor(shares).astype(int)
    left = (target - floor.groupby(group).transform("sum")).astype(int)
    rank = (shares - floor).groupby(group).rank(method="first", ascending=False)
    return floor + (rank <= left).astype(int)
