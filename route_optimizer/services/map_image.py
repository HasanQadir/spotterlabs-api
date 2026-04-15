"""
Render a static PNG map of the route + fuel stops using OpenStreetMap tiles.
No external API key needed - uses the public OSM tile CDN.

Returns a base64-encoded PNG string suitable for embedding in JSON responses
or displaying as <img src="data:image/png;base64,...">.
"""

import base64
import io

try:
    from staticmap import StaticMap, Line, CircleMarker
    _STATICMAP_AVAILABLE = True
except ImportError:
    _STATICMAP_AVAILABLE = False


def render_map(route_coords: list, stops) -> str | None:
    """
    Parameters
    ----------
    route_coords : [[lon, lat], ...] from ORS
    stops        : list of StopResult (from optimizer)

    Returns
    -------
    Base64-encoded PNG string, or None if staticmap is not installed.
    """
    if not _STATICMAP_AVAILABLE:
        return None

    m = StaticMap(900, 600, url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png")

    # Draw route line (blue)
    line_coords = [(c[0], c[1]) for c in route_coords]
    m.add_line(Line(line_coords, "#2980b9", 3))

    # Draw fuel stop markers (red)
    for stop in stops:
        if stop.station.longitude and stop.station.latitude:
            m.add_marker(
                CircleMarker(
                    (stop.station.longitude, stop.station.latitude),
                    "#e74c3c",
                    14,
                )
            )

    image = m.render()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")
