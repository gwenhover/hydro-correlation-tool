$(function() {
    var ol_map = TETHYS_MAP_VIEW.getMap();

    // Fit the view to the CONUS bounding box [west, south, east, north].
    // The extent is in EPSG:4326 (degrees) — our data's native units — so we
    // transform it into the view's projection (EPSG:3857) before fitting.
    // This is the transform-at-the-display-boundary pattern: data stays in
    // 4326, the map view is 3857.
    var conus_extent = ol.proj.transformExtent(
        [-125, 24, -66, 50], 'EPSG:4326', 'EPSG:3857'
    );
    ol_map.getView().fit(conus_extent, { size: ol_map.getSize() });
});
