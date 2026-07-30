$(function() {
    function getCookie(name) {
        var m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return m ? m.pop() : '';
    }
    $.ajaxSetup({
        beforeSend: function(xhr, settings) {
            if (!/^(GET|HEAD)$/i.test(settings.type)) {
                xhr.setRequestHeader('X-CSRFToken', getCookie('csrftoken'));
            }
        }
    });

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


    // Zoom-responsive gage styling: small dots when zoomed out (clean national
    // overview), larger when zoomed in (easy click targets). Every gage stays
    // visible and clickable — nothing is hidden by zoom.
    var gageStyleCache = {};
    var gageLastResolution = null;  // every feature in one frame shares a resolution...
    var gageLastStyle = null;   
    var gageLastStatus = null;    // ...so memoize to skip recomputing ~9,000x per frame
    var headwaters = false;
    var statusColors = {'Verified': '#1fb449', 'Edited': '#e4c134', 'Unverified': '#b12424'}
    var selectedNwmId = null;
    var baseNwmId = null;
    var selectedGeoglowsId = null;
    var baseGeoglowsId = null;
    var selectedUsgsId = null;
    var usgsFeature = null;
    var map_mode = null;

    var hw_button = document.createElement('button');
    hw_button.innerHTML = 'H';
    hw_button.title = 'Show headwater streams';
    
    var hw_element = document.createElement('div');
    hw_element.className = 'headwater-toggle ol-unselectable ol-control';
    hw_element.appendChild(hw_button);

    hw_button.classList.add('active');
    hw_button.addEventListener('click', function() {

        hw_button.classList.toggle('active');
        if (headwaters){
            headwaters = false;
            hw_button.title = 'Show headwater streams';
        }
        else {
            headwaters = true;
            hw_button.title = 'Hide headwater streams';
        }
        nwm_layer.changed();
        geoglows_layer.changed();
    });
    ol_map.addControl(new ol.control.Control({ element: hw_element }));



    function gageStyle(feature, resolution) {
        // Fast path: same frame (same resolution) -> reuse the computed style.
        var gage_status = feature.get('verification_status');
        var statusColor = statusColors[gage_status] || statusColors['Unverified'];
        if (resolution === gageLastResolution && gage_status === gageLastStatus) {
            return gageLastStyle;
        }
        var zoom = ol_map.getView().getZoomForResolution(resolution);

        // Ramp radius from 3px (zoom <= 4) to 6px (zoom >= 11), clamped; round to
        // 0.5 so only a handful of distinct Style objects are ever created.
        var t = Math.max(0, Math.min(1, (zoom - 4) / (11 - 4)));
        var radius = Math.round((3 + t * 3) * 2) / 2;
        var key = (radius + '|' + gage_status);

        if (!gageStyleCache[key]) {
            gageStyleCache[key] = new ol.style.Style({
                image: new ol.style.Circle({
                    radius: radius,
                    fill: new ol.style.Fill({ color: statusColor }),
                    stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 })
                })
            });
        }
        gageLastStatus = gage_status;
        gageLastResolution = resolution;
        gageLastStyle = gageStyleCache[key];
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
    var nwm_style = new ol.style.Style({
        stroke: new ol.style.Stroke({color: '#3388ff', width: 1 })
    });
    var base_highlight_style = new ol.style.Style({
        stroke: new ol.style.Stroke({color: '#f82aa9', width: 3})
    });
    var geo_style = new ol.style.Style({
        stroke: new ol.style.Stroke({color: '#020447', width: 1 })
    });
    var selected_highlight_style = new ol.style.Style({
        stroke: new ol.style.Stroke({color: '#cfec11', width: 3})
    });
    var nwm_layer = new ol.layer.VectorTile({
        source: nwm_source,
        zIndex: 5,   // below gages (10), above basemap
        minZoom: 9,
        style: function(feature){
            if (baseNwmId != null && String(feature.get('station_id')) === String(baseNwmId)){
                return (base_highlight_style)
            }
            else if (selectedNwmId != null && String(feature.get('station_id')) === String(selectedNwmId)){
                return (selected_highlight_style)
            }
            if (!headwaters){
                if (feature.get("streamOrder") > HEADWATER_THRESHOLD){
                    return (nwm_style)
                } 
                else {
                    return
                }
            }
            return (nwm_style)
            
        }
    });

    var geoglows_layer = new ol.layer.VectorTile({
        source: geoglows_source,
        visible: false,
        zIndex: 5,   // below gages (10), above basemap
        minZoom: 9,
        style: function(feature){
            if (baseGeoglowsId != null && String(feature.get('station_id')) === String(baseGeoglowsId)){
                return (base_highlight_style)
            }
            else if (selectedGeoglowsId != null && String(feature.get('station_id')) === String(selectedGeoglowsId)){
                return (selected_highlight_style)
            }
            if (!headwaters){
                if (feature.get("streamOrder") > HEADWATER_THRESHOLD){
                    return (geo_style)
                } 
                else {
                    return
                }
            }
            return (geo_style)
        },
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
        map_mode = null;
        $('#log-geo').text('GEO select from map');
        $('#log-nwm').text('NWM select from map');
        nwm_layer.setVisible(true);
        geoglows_layer.setVisible(false);
        
        $('#network-nwm').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-geoglows').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#network-geoglows').on('click', function() {
        map_mode = null;
        $('#log-geo').text('GEO select from map');
        $('#log-nwm').text('NWM select from map');
        geoglows_layer.setVisible(true);
        nwm_layer.setVisible(false);

        $('#network-geoglows').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-nwm').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#log-nwm').on('click', function() {
        map_mode = 'nwm'

        nwm_layer.setVisible(true);
        geoglows_layer.setVisible(false);

        $('#network-nwm').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-geoglows').removeClass('btn-primary').addClass('btn-outline-primary');
        $('#log-nwm').text('Select a reach');
        $('#log-geo').text('GEO select from map');
        
    });
    $('#log-geo').on('click', function() {
        map_mode = 'geoglows'

        geoglows_layer.setVisible(true);
        nwm_layer.setVisible(false);

        $('#network-geoglows').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-nwm').removeClass('btn-primary').addClass('btn-outline-primary');
        $('#log-geo').text('Select a reach');
        $('#log-nwm').text('NWM select from map');
        
    });

    var nwmInput = document.getElementById("nwm-id-input");
    var geoInput = document.getElementById("geo-id-input");

    var staged_geoglows = null;
    var staged_nwm = null;
    var nwm_kge = null;
    var geo_kge = null;
    var nwm_kge_length = null;
    var geo_kge_length = null;
    var nwm_color = null;
    var geo_color = null;
    var nwm_logged = null;
    var geo_logged = null;
    var test_msg_nwm = null;
    var test_msg_geo = null;

    document.getElementById('save-modal').addEventListener('shown.bs.modal', function() {

    });
    document.getElementById('nwm-id-input').addEventListener('keydown', function(event) {
        if(event.key === 'Enter'){
            nwm_logged = this.value.trim();
            if (selectedUsgsId === null || !/^\d+$/.test(nwm_logged)){
                return;
            }
            test_msg_nwm = msg_generation += 1;
            $('#hydrograph-msg').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Loading data, please wait.</p>')
            load_reach(nwm_logged, 'NWM', test_msg_nwm, function(){
                if (test_msg_nwm === msg_generation){
                    $('#hydrograph-msg').empty()
                }
            })
        }
    });
    document.getElementById('geo-id-input').addEventListener('keydown', function(event) {
        if(event.key === 'Enter'){
            geo_logged = this.value.trim();
            if (selectedUsgsId === null || !/^\d+$/.test(geo_logged)){
                return;
            }
            test_msg_geo = msg_generation += 1;
            $('#hydrograph-msg').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Loading data, please wait.</p>')
            load_reach(geo_logged, 'GEOGLOWS', test_msg_geo, function(){
                if (test_msg_geo === msg_generation){
                    $('#hydrograph-msg').empty()
                }
            })
        }
    });

    $('#save-and-verify').on('click', function() {
        staged_nwm = nwmInput.value.trim();
        staged_geoglows = geoInput.value.trim();
        if (!staged_geoglows|| !staged_nwm|| !selectedUsgsId){
            const modalEl = document.getElementById("unstaged-modal");
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
            return
        }
        nwm_color = null;
        geo_color = null;
        const modalEl = document.getElementById("save-modal");
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
        $("#save-confirm-footer").prop("disabled", true);
        $("#save-and-verify").prop("disabled", true);
        $("#usgs-id").text("    USGS ID: " + selectedUsgsId);
        $("#geo-id").text( "GEOGLOWS ID: " + staged_geoglows);
        $("#nwm-id").text( "     NWM ID: " + staged_nwm);
        $("#geo-kge").text("GEOGLOWS KGE Loading");
        $("#nwm-kge").text("NWM KGE Loading");

        $.post(COMPUTE_KGE_URL, { nwm_id: staged_nwm, geo_id: staged_geoglows, usgs_id: selectedUsgsId}, function(data){
            if ("Error" in data){
                if (modalEl.classList.contains('show')){
                    bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                    const fModalEl = document.getElementById("fail-modal");
                    bootstrap.Modal.getOrCreateInstance(fModalEl).show();
                }
                nwm_kge = null;
                geo_kge = null;
                nwm_kge_length = null;
                geo_kge_length = null;
                document.getElementById('nwm-id-input').value = "";
                document.getElementById('geo-id-input').value = "";
                $("#save-and-verify").prop("disabled", false);
            }
            else{
                nwm_kge = data.nwm_kge;
                geo_kge = data.geo_kge;
                nwm_kge_length = data.nwm_kge_length;
                geo_kge_length = data.geo_kge_length;
                nwm_color = null;
                geo_color = null;
                if (nwm_kge >= .3){
                    nwm_color = 'green';
                } else if (-.41 <= nwm_kge){
                    nwm_color = 'darkgoldenrod';
                } else{
                    nwm_color = 'red';
                }
                if (geo_kge >= .3){
                    geo_color = 'green';
                } else if (-.41 <= geo_kge){
                    geo_color = 'darkgoldenrod';
                } else{
                    geo_color = 'red';
                }
                $("#save-and-verify").prop("disabled", false);
                $("#save-confirm-footer").prop("disabled", false);
                $("#geo-kge").text("GEOGLOWS KGE: " + geo_kge.toFixed(2)).css("color", geo_color);
                $("#nwm-kge").text("     NWM KGE: " + nwm_kge.toFixed(2)).css("color", nwm_color);
            }
        }).fail(function(){
            if (modalEl.classList.contains('show')) {
                bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                const fModalEl = document.getElementById("fail-modal")
                bootstrap.Modal.getOrCreateInstance(fModalEl).show();
            }
            $("#save-and-verify").prop("disabled", false);
            nwm_kge = null;
            geo_kge = null;
            nwm_kge_length = null;
            geo_kge_length = null;
            document.getElementById('nwm-id-input').value = "";
            document.getElementById('geo-id-input').value = "";
        });

    });

    $('#save-confirm-footer').on('click', function() {
        $.post(SAVE_URL, { nwm_kge: nwm_kge, geo_kge: geo_kge, nwm_kge_length: nwm_kge_length, geo_kge_length: geo_kge_length, nwm_id: staged_nwm, geo_id: staged_geoglows, usgs_id: selectedUsgsId}, function(data){
            if ("Error" in data){
                nwm_kge = null;
                geo_kge = null;
                nwm_kge_length = null;
                geo_kge_length = null;
                $("#save-and-verify").prop("disabled", false);
                const fModalEl2 = document.getElementById("fail-modal-2");
                bootstrap.Modal.getOrCreateInstance(fModalEl2).show();
                document.getElementById('nwm-id-input').value = "";
                document.getElementById('geo-id-input').value = "";
            }
            else{
                const modalEl = document.getElementById("verified-modal");
                bootstrap.Modal.getOrCreateInstance(modalEl).show();
                usgsFeature.set('verification_status', data.status)
                document.getElementById('nwm-id-input').value = "";
                document.getElementById('geo-id-input').value = "";
            }
        }).fail(function(){
            document.getElementById('nwm-id-input').value = "";
            document.getElementById('geo-id-input').value = "";
            nwm_kge = null;
            geo_kge = null;
            nwm_kge_length = null;
            geo_kge_length = null;
            const fModalEl = document.getElementById("fail-modal-2")
            bootstrap.Modal.getOrCreateInstance(fModalEl).show();
        });
        
    })

    // --- Click handling ----------------------------------------------------

    // On a map click, find the gage and/or reach under the cursor. A gage
    // click rebuilds the panel (metadata + hydrograph divs) and fetches the
    // USGS series; a reach click fetches the model series and overlays it on
    // the current gage's hydrograph; a click on neither clears the selection.

    var series_state = {};
    var current_unit = 'cms'
    var current_chart = 'single'
    // Bumped every time series_state is reset (new gage selected, or selection
    // cleared). Each $.get captures the value at request time; if it changed by
    // the time the response lands, that response belongs to a previous selection
    // and is dropped instead of drawn under the wrong gage's metadata.
    var selection_generation = 0;
    // The message div is shared by every request a click fires, and reach-only
    // clicks legitimately share the gage's selection_generation — so the div
    // needs its own owner. Bumped by each click that writes a spinner; only
    // callbacks holding the current value may write to or clear the div.
    var msg_generation = 0;

    ol_map.on('singleclick', function(evt) {
        var gage = null;
        var reach = null;
        var feature_count = 0;
        
        ol_map.forEachFeatureAtPixel(evt.pixel, function(feature, layer) {
            if (layer === gage_layer && gage === null) {
                gage = feature;
                feature_count += 1;
            } else if ((layer === nwm_layer || layer === geoglows_layer) && reach === null) {
                reach = feature;
                feature_count += 1;
            }
            // Stop early once we have one of each.
            return gage !== null && reach !== null;
        }, { hitTolerance: 5 });   // 1px stream lines are hard to hit exactly
        
        if (gage !== null && gage.get('usgs_id') !== selectedUsgsId) {
            // The geometry is in EPSG:3857 (the map projection); toLonLat converts
            // the coordinate back to [lon, lat] in 4326 for human-readable display —
            // the same transform-at-the-display-boundary rule used everywhere else.
            var lonLat = ol.proj.toLonLat(gage.getGeometry().getCoordinates());

            document.getElementById('nwm-id-input').value = "";
            document.getElementById('geo-id-input').value = "";
            map_mode = null;
            series_state = {};
            selection_generation += 1;
            msg_generation += 1;
            var gage_status = gage.get("verification_status")
            $('.panel-content').html(
                '<h4 class="title" style="color: blue; display: flex; justify-content: space-between; width: 100%; margin-top: 15px">' + '<span>Selected Gage </span>' + '<span style="color: ' + statusColors[gage_status] + '; margin-right: 35px; border: 2px solid ' + statusColors[gage_status] + '; padding: 4px 12px; border-radius: 20px;">' + gage_status + '</span>' + '</h4>' +
                '<h6 class="gage-name">' + gage.get('gage_name') + '</h6>' +
                '<dl class="gage-meta">' +
                    '<dt>USGS ID</dt><dd>' + gage.get('usgs_id') + '</dd>' +
                    '<dt>Latitude</dt><dd>' + lonLat[1].toFixed(5) + '</dd>' +
                    '<dt>Longitude</dt><dd>' + lonLat[0].toFixed(5) + '</dd>' +
                '</dl>' +
                // Message div is separate from the chart divs: the gage and reach
                // callbacks race, and a "no data" note must be able to coexist
                // with a chart (e.g. gage without records sitting on a live reach).
                '<div id="hydrograph-msg"><p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Loading data, please wait.</p></div>' +
                '<div id="hydrograph-1"></div>' +
                '<div id="hydrograph-2"></div>' +
                '<div id="hydrograph-3"></div>'
            );
            
            baseNwmId = gage.get('nwm_feature_id');
            baseGeoglowsId = gage.get('geoglows_river_id');
            selectedNwmId = null;
            selectedGeoglowsId = null;
            selectedUsgsId = gage.get('usgs_id');
            usgsFeature = gage;
            $('#log-geo').text('GEO select from map');
            $('#log-nwm').text('NWM select from map');
            document.getElementById('nwm-id-input').value = "";
            document.getElementById('geo-id-input').value = "";
            nwm_layer.changed();
            geoglows_layer.changed();

            // Capture the generation this request belongs to; the callback
            // compares it against the current one and drops stale responses.
            var gage_generation = selection_generation;
            var cur_msg = msg_generation;
            $.get(GAGES_MD_URL, { usgs_id: gage.get('usgs_id') }, function(data) {
                if (gage_generation !== selection_generation) {
                    return;   // user has since selected a different gage (or cleared)
                }
                if (data.dates.length === 0) {
                    if (cur_msg === msg_generation) {
                        $('#hydrograph-msg').html('<p class="text-muted">No observed discharge data for this gage.</p>');
                    }
                    return;
                }

                series_state['usgs'] = { 'dates': data.dates, 'values': data.values, 'name': 'USGS Observed' };

                feature_count -= 1;
                if (feature_count === 0 && cur_msg === msg_generation){
                    $('#hydrograph-msg').empty();
                }
                render_hydrograph();

            }).fail(function(){
                if (gage_generation !== selection_generation || cur_msg !== msg_generation) {
                    return;
                }
                $('#hydrograph-msg').html('<p class="text-muted">Could not load USGS data — try re-selecting the gage.</p>');
            });

            
            selection_source.clear();
            selection_source.addFeature(new ol.Feature(gage.getGeometry()));
        } else if (gage !== null && gage.get('usgs_id') === selectedUsgsId){
            feature_count -= 1;
        }
            
        if (reach === null && gage === null) {
            $('.panel-content').html('<p class="text-muted">Select a gage to see details.</p>');
            selection_source.clear();
            // Reset the series too — the hydrograph divs are gone from the DOM,
            // and bumping the generation invalidates any in-flight requests.
            series_state = {};
            selection_generation += 1;
            selectedGeoglowsId = null;
            selectedNwmId = null;
            selectedUsgsId = null;
            usgsFeature = null;
            baseNwmId = null;
            baseGeoglowsId = null;
            nwm_layer.changed();
            geoglows_layer.changed();
            map_mode = null;
            $('#log-geo').text('GEO select from map');
            $('#log-nwm').text('NWM select from map');
            document.getElementById('nwm-id-input').value = "";
            document.getElementById('geo-id-input').value = "";
        }

        if (reach !== null) {
            if (gage === null || gage.get('usgs_id') === selectedUsgsId){
                msg_generation += 1;
                $('#hydrograph-msg').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Loading data, please wait.</p>');
            }
    
            var cur_msg = msg_generation;
            var network  = geoglows_layer.getVisible() ? "GEOGLOWS" : "NWM";
            var river_id = reach.get('station_id');

            load_reach(river_id, network, cur_msg, function() {
                feature_count -= 1;
                if (feature_count === 0 && cur_msg === msg_generation){
                    $('#hydrograph-msg').empty();
                }
            });
        };
    });


    function load_reach(river_id, network, cur_msg, on_loaded){
        if (network === 'GEOGLOWS'){
            selectedGeoglowsId = river_id;
            if (map_mode === 'geoglows'){
                $('#geo-id-input').val(selectedGeoglowsId);
            }
            geoglows_layer.changed()
        } else {
            selectedNwmId = river_id;
            if (map_mode === 'nwm'){
                $('#nwm-id-input').val(selectedNwmId);
            }
            nwm_layer.changed()
        }

        var net_class = 'reach-row-' + network.toLowerCase();
        $('.' + net_class).remove();
        $('.gage-meta').append(
            '<dt class="' + net_class + '">' + network + ' Reach ID</dt>' +
            '<dd class="' + net_class + '">' + river_id + '</dd>'
        )
        // Same staleness guard as the gage request: this reach series
        // belongs to the currently-selected gage, so drop the response if
        // the selection has changed by the time it arrives.
        var reach_generation = selection_generation;
        $.get(REACH_URL, { river_id: river_id, network: network }, function(data) {
            if (reach_generation !== selection_generation) {
                return;
            }
            if (data.dates.length === 0) {
                if (cur_msg === msg_generation) {
                    $('#hydrograph-msg').html('<p class="text-muted">No retrospective data for this reach.</p>');
                    series_state[network.toLowerCase()] = { 'dates': data.dates, 'values': data.values, 'name': network };
                    render_hydrograph();
                }
                return;
            }

            series_state[network.toLowerCase()] = { 'dates': data.dates, 'values': data.values, 'name': network };

            on_loaded();

            render_hydrograph();

        }).fail(function(){
            if (reach_generation !== selection_generation || cur_msg !== msg_generation) {
                return;
            }
            $('#hydrograph-msg').html('<p class="text-muted">Could not load ' + network + ' data — try re-selecting the gage.</p>');
        });


    }
    function render_hydrograph() {

        if (Object.keys(series_state).length === 0) {
            return;
        }
        // The hydrograph divs only exist after a gage click rebuilds the panel.
        // Without a selected gage (e.g. a reach clicked on its own) there is
        // nowhere to draw — Plotly.react on a missing div throws.
        if (document.getElementById('hydrograph-1') === null) {
            return;
        }
        var factor = (current_unit === 'cfs') ? 35.3147 : 1;

        var traces = Object.values(series_state).map(function(s) {
            return {
                x: s.dates,
                y: s.values.map(function(v){return v * factor;}),
                type: 'scatter',
                mode: 'lines',
                name: s.name
            }
        });

        // The three fixed divs are always in the DOM; `used_count` records how
        // many this render actually draws into, so we can blank the rest below.
        var chart_ids = ['hydrograph-1', 'hydrograph-2', 'hydrograph-3'];
        var used_count;

        if (current_chart === 'single') {
            var layout = {
                // Title names exactly the series drawn — a reach-only chart says
                // "GEOGLOWS", a full comparison says "USGS Observed vs GEOGLOWS".
                title: traces.map(function(t) { return t.name; }).join(' vs '),
                xaxis: { title: 'Date' },
                yaxis: { title: 'Discharge (' + current_unit + ')' }
            };

            Plotly.react('hydrograph-1', traces, layout);
            used_count = 1;                 // single mode draws into the first div only
            
        } else {
            // Stacked mode: one chart per series, all sharing a common y-axis
            // range so magnitudes are directly comparable — a wrong reach shows
            // up as an obvious over/under-shoot instead of being hidden by
            // per-chart auto-scaling. Reuse the traces built above (already in
            // the current unit) and recompute the range every render so it
            // tracks the unit toggle and whichever series are loaded.
            var y_max = traces.reduce(function(max, t) {
                return t.y.reduce(function(m, v) {
                    return (isFinite(v) && v > m) ? v : m;
                }, max);
            }, 0);

            traces.forEach(function(trace, i) {
                var layout = {
                    title: trace.name,
                    xaxis: { title: 'Date' },
                    yaxis: { title: 'Discharge (' + current_unit + ')' }
                };
                // Share the scale only when there's a real positive peak; a
                // [0, 0] range would be degenerate, so fall back to auto then.
                if (y_max > 0) {
                    layout.yaxis.range = [0, y_max * 1.05];   // 5% headroom
                }
                Plotly.react(chart_ids[i], [trace], layout);
            });
            used_count = traces.length;     // stacked draws one div per series
        }

        // Clear any divs we didn't draw into this render, so an old chart doesn't
        // linger — e.g. switching stacked -> single, or loading a gage that has
        // fewer series than the last one. purge() empties a div, and is a safe
        // no-op on a div that was never plotted.
        chart_ids.slice(used_count).forEach(function(id) {
            Plotly.purge(id);
        });
    };

    $('#unit-cms').on('click', function() {
        current_unit = 'cms';
        render_hydrograph();
        $('#unit-cms').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#unit-cfs').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#unit-cfs').on('click', function() {
        current_unit = 'cfs';
        render_hydrograph();
        $('#unit-cfs').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#unit-cms').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#chart-single').on('click', function() {
        current_chart = 'single';
        render_hydrograph();
        $('#chart-single').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#chart-stacked').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#chart-stacked').on('click', function() {
        current_chart = 'stacked';
        render_hydrograph();
        $('#chart-stacked').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#chart-single').removeClass('btn-primary').addClass('btn-outline-primary');
    })

});

