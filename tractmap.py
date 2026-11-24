"""Map of one lender's applications across the tracts of one MSA (Lender analysis page).

Each tract of the chosen type (e.g. majority-minority) is shaded by how many applications the lender
took there: pink for none, then three shades of green. Other tracts are outlined only.

Boundaries: Census TIGER/Line tracts (scripts/load_tracts.py). Applications: the same ones the
redlining test counts (counts.TRACT_TABLE), for the product and loan types in the sidebar.
Basemap: CARTO Voyager (roads, highways and place names), which needs no account or API key.
"""

import json
import math

import pandas as pd
import pydeck as pdk

import redlining as rl

# Free vector basemap with road and highway detail; no account or API key
BASEMAP = "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json"

# key -> (legend label, RGBA fill)
CATEGORIES = {
    "none": ("No applications", [244, 114, 182, 190]),
    "low": ("1–4 applications", [220, 252, 231, 220]),
    "mid": ("5–9 applications", [134, 239, 172, 220]),
    "high": ("10 or more applications", [22, 163, 74, 220]),
    "other": ("Other tracts", [0, 0, 0, 0]),
}
OUTLINE = [100, 116, 139, 150]
# Tooltip label for the population share behind each tract type (rl.TRACT_TYPES)
SHARE_LABEL = {
    "MMCT": "Minority residents",
    "MBCT": "Non-Hispanic Black residents",
    "MHCT": "Hispanic residents",
    "MBHCT": "Black + Hispanic residents",
}


def _not_type(tract_type: str) -> str:  # "Not majority-Black": keeps group names capitalized
    label = rl.TRACT_TYPES[tract_type][0]
    return f"Not {label[0].lower()}{label[1:]}"


def app_bin(n: int) -> str:
    if n == 0:
        return "none"
    if n < 5:
        return "low"
    if n < 10:
        return "mid"
    return "high"


def classify(tracts: pd.DataFrame, apps: pd.DataFrame, tract_type: str, product: str,
             loan_types: tuple[str, ...]) -> pd.DataFrame:
    """tracts: data.msa_tracts(); apps: data.lender_tract_apps(). Adds apps, pct, in_type, category."""
    pct_col = rl.TRACT_TYPES[tract_type][2]
    keep = apps[apps["product"].isin(rl.PRODUCTS[product]) & apps["loan_type"].isin(loan_types)]
    per_tract = keep.groupby("census_tract")["apps"].sum()
    out = tracts.copy()
    out["apps"] = out["census_tract"].map(per_tract).fillna(0).astype(int)
    out["pct"] = out[pct_col]
    out["in_type"] = out["pct"] >= rl.THRESHOLD
    out["category"] = out["apps"].map(app_bin).where(out["in_type"], "other")
    return out


class _CompactDeck(pdk.Deck):
    """pydeck writes its JSON with indent=2, which more than doubles a big MSA's map (one line per
    coordinate). Streamlit sends whatever to_json() returns, so return it without the whitespace."""

    def to_json(self):
        return json.dumps(json.loads(super().to_json()), separators=(",", ":"))


def _view(geoms: list[dict], width_px: int = 900, height_px: int = 560) -> pdk.ViewState:
    """Center and zoom that fit every geometry (Web Mercator)."""
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for p in c:
                walk(p)

    for g in geoms:
        walk(g["coordinates"])
    lon0, lon1, lat0, lat1 = min(xs), max(xs), min(ys), max(ys)
    lat = (lat0 + lat1) / 2
    z_lon = math.log2(360 * width_px / (256 * max(lon1 - lon0, 1e-4)))
    z_lat = math.log2(360 * height_px * math.cos(math.radians(lat)) / (256 * max(lat1 - lat0, 1e-4)))
    return pdk.ViewState(longitude=(lon0 + lon1) / 2, latitude=lat, zoom=min(z_lon, z_lat) - 0.15)


def deck(tracts: pd.DataFrame, tract_type: str) -> pdk.Deck:
    """tracts: output of classify()."""
    share_label = SHARE_LABEL.get(tract_type, "Residents in group")
    features, geoms = [], []
    for t in tracts.itertuples():
        geom = json.loads(t.geojson)
        geoms.append(geom)
        label = CATEGORIES[t.category][0] if t.category != "other" else _not_type(tract_type)
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "tract": t.census_tract,
                "label": label,
                "share": f"{share_label}: {t.pct:.0f}%" if pd.notna(t.pct) else f"{share_label}: no residents",
                "apps": f"{t.apps:,}",
                "fill": CATEGORIES[t.category][1],
            },
        })
    layer = pdk.Layer(
        "GeoJsonLayer",
        {"type": "FeatureCollection", "features": features},
        id="tracts",
        pickable=True,
        stroked=True,
        filled=True,
        get_fill_color="properties.fill",
        get_line_color=OUTLINE,
        line_width_min_pixels=0.6,
        auto_highlight=True,
        highlight_color=[30, 64, 175, 90],
    )
    return _CompactDeck(
        layers=[layer],
        initial_view_state=_view(geoms),
        map_style=BASEMAP,
        tooltip={
            "html": "<b>Tract {tract}</b><br/>{label}<br/>{share}<br/>Lender applications: {apps}",
            "style": {"fontSize": "12px"},
        },
    )


def legend_html(tracts: pd.DataFrame, tract_type: str) -> str:
    counts = tracts["category"].value_counts()
    items = []
    for key, (label, rgba) in CATEGORIES.items():
        if key == "other":
            label = _not_type(tract_type)
        r, g, b, a = rgba
        swatch = (f"display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;"
                  f"margin-right:6px;background:rgba({r},{g},{b},{a / 255:.2f});"
                  f"border:1px solid rgba({OUTLINE[0]},{OUTLINE[1]},{OUTLINE[2]},0.8)")
        items.append(f'<span style="margin-right:18px;white-space:nowrap"><span style="{swatch}"></span>'
                     f'{label} ({int(counts.get(key, 0)):,})</span>')
    return '<div style="font-size:0.85rem;line-height:1.9">' + "".join(items) + "</div>"
