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

    // --- USGS gage layer ---------------------------------------------------

    // The source = WHERE the data is + HOW to parse it. The GeoJSON file is in
    // lon/lat (CRS84 / EPSG:4326), but the map view is EPSG:3857, so we tell
    // the format to reproject each feature from 4326 into 3857 as it loads.
    var gage_source = new ol.source.Vector({
        url: GAGES_GEOJSON_URL,
        format: new ol.format.GeoJSON({
            dataProjection: 'EPSG:4326',
            featureProjection: 'EPSG:3857'
        })
    });

    // The layer = HOW to draw the source. One flat-colored dot per gage.
    // (Color-by-verification-status comes in Week 7 once the DB exists — a
    // single color is correct for now.)
    var gage_layer = new ol.layer.Vector({
        source: gage_source,
        style: new ol.style.Style({
            image: new ol.style.Circle({
                radius: 5,
                fill: new ol.style.Fill({ color: '#1f78b4' }),
                stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 })
            })
        })
    });

    ol_map.addLayer(gage_layer);

    // --- Click handling ----------------------------------------------------

    // On a map click, find the topmost gage feature under the cursor and log
    // its id. forEachFeatureAtPixel walks features at that pixel; returning a
    // truthy value stops at the first hit. Week 2 stops at logging the id —
    // populating the panel is Week 4.
    ol_map.on('singleclick', function(evt) {
        ol_map.forEachFeatureAtPixel(evt.pixel, function(feature) {
            console.log('Clicked gage:', feature.get('monitoring_location_id'));
            return true;
        });
    });
});
