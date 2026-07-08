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
    // Zoom-responsive gage styling: small dots when zoomed out (clean national
    // overview), larger when zoomed in (easy click targets). Every gage stays
    // visible and clickable — nothing is hidden by zoom.
    var gageStyleCache = {};        // radius -> reusable Style (avoid rebuilding per feature)
    var gageLastResolution = null;  // every feature in one frame shares a resolution...
    var gageLastStyle = null;       // ...so memoize to skip recomputing ~9,000x per frame

    function gageStyle(feature, resolution) {
        // Fast path: same frame (same resolution) -> reuse the computed style.
        if (resolution === gageLastResolution) {
            return gageLastStyle;
        }
        var zoom = ol_map.getView().getZoomForResolution(resolution);

        // Ramp radius from 3px (zoom <= 4) to 6px (zoom >= 11), clamped; round to
        // 0.5 so only a handful of distinct Style objects are ever created.
        var t = Math.max(0, Math.min(1, (zoom - 4) / (11 - 4)));
        var radius = Math.round((3 + t * 3) * 2) / 2;

        if (!gageStyleCache[radius]) {
            gageStyleCache[radius] = new ol.style.Style({
                image: new ol.style.Circle({
                    radius: radius,
                    fill: new ol.style.Fill({ color: '#1f78b4' }),
                    stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 })
                })
            });
        }

        gageLastResolution = resolution;
        gageLastStyle = gageStyleCache[radius];
        return gageLastStyle;
    }

    var gage_layer = new ol.layer.Vector({
        source: gage_source,
        // Higher zIndex keeps gages drawn on top of the stream lines, so the
        // dots stay visible and win the click when a gage sits over a reach.
        zIndex: 10,
        style: gageStyle
    });

    ol_map.addLayer(gage_layer);

    var nwm_source = new ol.source.VectorTile({
        format: new ol.format.MVT(),
        // Tileset tops out at z16 (from Jerson's TileJSON), but the view allows
        // zoom 18. maxZoom:16 makes OL over-zoom (reuse z16 tiles) past 16 rather
        // than request z17/z18 tiles that don't exist and blank the streams.
        maxZoom: 16,
        url: 'https://{a-d}.tiles.mapbox.com/v4/byu-hydroinformatics.nwm-channels/{z}/{x}/{y}.vector.pbf?access_token=' + MAPBOX_TOKEN
    });

    var geoglows_source = new ol.source.VectorTile({
        format: new ol.format.MVT(),
        // Tileset tops out at z12 (from Jerson's TileJSON), but the view allows
        // zoom 18. maxZoom:12 makes OL over-zoom (reuse z12 tiles) past 12 rather
        // than request z17/z18 tiles that don't exist and blank the streams.
        maxZoom: 12,
        url: 'https://{a-d}.tiles.mapbox.com/v4/byu-hydroinformatics.geoglows-us/{z}/{x}/{y}.vector.pbf?access_token=' + MAPBOX_TOKEN
    });

    var nwm_layer = new ol.layer.VectorTile({
        source: nwm_source,
        zIndex: 5,   // below gages (10), above basemap
        style: new ol.style.Style({
            stroke: new ol.style.Stroke({ color: '#3388ff', width: 1 })
        })
    });

    var geoglows_layer = new ol.layer.VectorTile({
        source: geoglows_source,
        visible: false,
        zIndex: 5,   // below gages (10), above basemap
        style: new ol.style.Style({
            stroke: new ol.style.Stroke({ color: '#020447', width: 1 })
        })
    });

    ol_map.addLayer(nwm_layer);

    ol_map.addLayer(geoglows_layer);


    // --- Selected-gage highlight ring --------------------------------------
    // A normally-empty layer holding only the currently-selected gage, drawn as
    // a hollow ring above the gages. On each click we clear it and add the
    // clicked gage, so the previous highlight is removed automatically.
    var selection_source = new ol.source.Vector();
    var selection_layer = new ol.layer.Vector({
        source: selection_source,
        zIndex: 20,   // above gages (zIndex 10) so the ring sits on top
        style: new ol.style.Style({
            image: new ol.style.Circle({
                radius: 9,
                stroke: new ol.style.Stroke({ color: '#00e5ff', width: 3 })
            })
        })
    });
    ol_map.addLayer(selection_layer);


    $('#network-nwm').on('click', function() {
        nwm_layer.setVisible(true);
        geoglows_layer.setVisible(false);

        // Active button = solid (btn-primary); inactive = outline.
        $('#network-nwm').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-geoglows').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#network-geoglows').on('click', function() {
        geoglows_layer.setVisible(true);
        nwm_layer.setVisible(false);

        // Active button = solid (btn-primary); inactive = outline.
        $('#network-geoglows').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-nwm').removeClass('btn-primary').addClass('btn-outline-primary');
    });


    // --- Click handling ----------------------------------------------------

    // On a map click, find the topmost gage feature under the cursor and log
    // its id. forEachFeatureAtPixel walks features at that pixel; returning a
    // truthy value stops at the first hit. Week 2 stops at logging the id —
    // populating the panel is Week 4.
    ol_map.on('singleclick', function(evt) {
        var gage = null;
        var reach = null;

        // Walk every feature under the click and keep the FIRST of each layer.
        // The `=== null` guards dedupe: a second overlapping gage, or a reach
        // split across vector-tile boundaries, won't get logged twice.
        ol_map.forEachFeatureAtPixel(evt.pixel, function(feature, layer) {
            if (layer === gage_layer && gage === null) {
                gage = feature;
            } else if ((layer === nwm_layer || layer === geoglows_layer) && reach === null) {
                reach = feature;
            }
            // Stop early once we have one of each.
            return gage !== null && reach !== null;
        }, { hitTolerance: 5 });   // 1px stream lines are hard to hit exactly

        if (gage !== null) {
            // The geometry is in EPSG:3857 (the map projection); toLonLat converts
            // the coordinate back to [lon, lat] in 4326 for human-readable display —
            // the same transform-at-the-display-boundary rule used everywhere else.
            var lonLat = ol.proj.toLonLat(gage.getGeometry().getCoordinates());


            $('.panel-content').html(
                '<h6 class="gage-name">' + gage.get('station_nm') + '</h6>' +
                '<dl class="gage-meta">' +
                    '<dt>USGS ID</dt><dd>' + gage.get('USGSID') + '</dd>' +
                    '<dt>Latitude</dt><dd>' + lonLat[1].toFixed(5) + '</dd>' +
                    '<dt>Longitude</dt><dd>' + lonLat[0].toFixed(5) + '</dd>' +
                '</dl>' +
                '<div id="hydrograph"></div>'
            );


            $.get(GAGES_MD_URL, { usgs_id: gage.get('USGSID') }, function(data) {
                if (data.dates.length === 0) {
                    $('#hydrograph').html('<p class="text-muted">No discharge data for this gage.</p>');
                    return;
                }
                
                var traces = [{
                    x: data.dates,
                    y: data.values,
                    type: 'scatter', 
                    mode: 'lines',
                    name: 'USGS Observed'
                }];
                var layout = {
                    title: 'Observed daily discharge',
                    xaxis: { title: 'Date' },
                    yaxis: { title: 'Discharge (' + data.units + ')' }
                };
                Plotly.newPlot('hydrograph', traces, layout);

            });

            // Highlight this gage: clear the previous selection and add this one.
            // A fresh Feature sharing the geometry avoids putting the same feature
            // object into two sources.
            selection_source.clear();
            selection_source.addFeature(new ol.Feature(gage.getGeometry()));
        } else {
            $('.panel-content').html('<p class="text-muted">Select a gage to see details.</p>');
            selection_source.clear();
        }

        if (reach !== null) {
            // Confirms the NWM reach id + stream-order attribute are present on
            // the tile features (Week 3 requirement; streamOrder feeds the Week 9
            // headwater filter).
            console.log('NWM reach station_id:', reach.get('station_id'),
                        '| streamOrder:', reach.get('streamOrder'));
        }
    });

});
