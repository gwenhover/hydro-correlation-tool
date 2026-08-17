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
    var gageLastViability = null;
    var headwaters = false;
    var statusColors = {'Verified': '#1fb449', 'Edited': '#e4c134', 'Unverified': '#b12424'}
    var unreachable_color = '#5a5f66'
    var selectedNwmId = null;
    var seededNwmId = null;
    var baseNwmId = null;
    var selectedGeoglowsId = null;
    var seededGeoglowsId = null;
    var baseGeoglowsId = null;
    var selectedUsgsId = null;
    var usgsFeature = null;
    var map_mode = null;
    var hw_button = document.createElement('button');
    var map_legend = document.createElement('div');
    var seeded_reach_color = '#f82aa9'
    var selected_reach_color = '#cfec11'
    var shown_statuses = ["Verified", "Edited", "Unverified"]

    var text_row = document.createElement('div');
    text_row.id = 'text-row';
    text_row.className = 'legend-title';
    text_row.textContent = 'Click Verification Status to Filter'
    map_legend.appendChild(text_row);

    for (let stat in statusColors){
        let row = document.createElement('div');
        row.className = 'legend-row clickable';
        row.title = 'Hide ' + stat.toLowerCase() + ' gages';
        var swatch = document.createElement('span');
        swatch.className = 'legend-swatch';
        swatch.style.background = statusColors[stat];
        var label = document.createElement('span');
        label.textContent = stat;
        row.appendChild(swatch);
        row.appendChild(label);
        map_legend.appendChild(row);

        row.addEventListener('click', function() {

            row.classList.toggle('off');
            if (shown_statuses.includes(stat)){
                shown_statuses = shown_statuses.filter(status => status !== stat);
                row.title = 'Show ' + stat.toLowerCase() + ' gages';
            }
            else{
                shown_statuses.push(stat);
                row.title = 'Hide ' + stat.toLowerCase() + ' gages';
            }
            gage_layer.changed();
        });
    }
    var unreachable_row = document.createElement('div');
    unreachable_row.className = 'legend-row';
    var unreachable_label = document.createElement('span');
    var unreachable_swatch = document.createElement('span');
    unreachable_swatch.className = 'legend-swatch';
    unreachable_swatch.style.background = unreachable_color;
    unreachable_label.textContent = 'No Data (ringed if edited or verified)';
    unreachable_row.appendChild(unreachable_swatch);
    unreachable_row.appendChild(unreachable_label);
    map_legend.appendChild(unreachable_row);

    var seeded_row = document.createElement('div');
    seeded_row.className = 'legend-row';
    var seeded_label = document.createElement('span');
    var seeded_line = document.createElement('span');
    seeded_line.className = 'legend-line';
    seeded_line.style.background = seeded_reach_color;
    seeded_label.textContent = 'Seeded Reach';
    seeded_row.appendChild(seeded_line);
    seeded_row.appendChild(seeded_label);
    map_legend.appendChild(seeded_row);

    var selected_row = document.createElement('div');
    selected_row.className = 'legend-row';
    var selected_label = document.createElement('span');
    var selected_line = document.createElement('span');
    selected_line.className = 'legend-line';
    selected_line.style.background = selected_reach_color;
    selected_label.textContent = 'Selected Reach';
    selected_row.appendChild(selected_line);
    selected_row.appendChild(selected_label);
    map_legend.appendChild(selected_row);


    hw_button.innerHTML = 'H';
    hw_button.title = 'Show headwater streams';
    
    var hw_element = document.createElement('div');
    hw_element.className = 'headwater-toggle ol-unselectable ol-control';
    hw_element.appendChild(hw_button);

    var map_legend_element = document.createElement('div');
    map_legend_element.className = 'chart-legend-container ol-unselectable ol-control';
    map_legend_element.appendChild(map_legend);

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
    ol_map.addControl(new ol.control.Control({ element: map_legend_element }));


    function gageStyle(feature, resolution) {
        
        if (!shown_statuses.includes(feature.get('verification_status'))) return;

        // Fast path: same frame (same resolution) -> reuse the computed style.
        var gage_status = feature.get('verification_status');
        var unreachable = feature.get('unreachable');
        var statusColor = statusColors[gage_status] || statusColors['Unverified'];
        if (resolution === gageLastResolution && gage_status === gageLastStatus && unreachable === gageLastViability) {
            return gageLastStyle;
        }
        var zoom = ol_map.getView().getZoomForResolution(resolution);

        // Ramp radius from 3px (zoom <= 4) to 6px (zoom >= 11), clamped; round to
        // 0.5 so only a handful of distinct Style objects are ever created.
        var t = Math.max(0, Math.min(1, (zoom - 4) / (11 - 4)));
        var radius = Math.round((3 + t * 3) * 2) / 2;
        var key = (radius + '|' + gage_status + '|' + unreachable);
        
        if (!gageStyleCache[key]) {
            if (unreachable && gage_status === 'Unverified'){
                gageStyleCache[key] = new ol.style.Style({
                    image: new ol.style.Circle({
                        radius: radius,
                        fill: new ol.style.Fill({ color: unreachable_color }),
                        stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 })
                    })
                });
            } else if (unreachable) {
                gageStyleCache[key] = new ol.style.Style({
                    image: new ol.style.Circle({
                        radius: radius,
                        fill: new ol.style.Fill({ color: unreachable_color }),
                        stroke: new ol.style.Stroke({ color: statusColor, width: 3 })
                    })
                });
            }
            else{
                gageStyleCache[key] = new ol.style.Style({
                    image: new ol.style.Circle({
                        radius: radius,
                        fill: new ol.style.Fill({ color: statusColor }),
                        stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 })
                    })
                });
            }
        }
        gageLastStatus = gage_status;
        gageLastResolution = resolution;
        gageLastStyle = gageStyleCache[key];
        gageLastViability = unreachable;
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
        stroke: new ol.style.Stroke({color: '#1054b3', width: 3 })
    });
    var base_highlight_style = new ol.style.Style({
        stroke: new ol.style.Stroke({color: seeded_reach_color, width: 6})
    });
    var geo_style = new ol.style.Style({
        stroke: new ol.style.Stroke({color: '#020447', width: 3 })
    });
    var selected_highlight_style = new ol.style.Style({
        stroke: new ol.style.Stroke({color: selected_reach_color, width: 6})
    });
    var nwm_layer = new ol.layer.VectorTile({
        source: nwm_source,
        zIndex: 5,   // below gages (10), above basemap
        minZoom: 9,
        style: function(feature){
            if (seededNwmId != null && String(feature.get('station_id')) === String(seededNwmId)){
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
            if (seededGeoglowsId != null && String(feature.get('station_id')) === String(seededGeoglowsId)){
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
        $('#log-geo').text('Pick from map');
        $('#log-nwm').text('Pick from map');
        $('#log-geo').removeClass('armed');
        $('#log-nwm').removeClass('armed');
        nwm_layer.setVisible(true);
        geoglows_layer.setVisible(false);
        
        $('#network-nwm').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-geoglows').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#network-geoglows').on('click', function() {
        map_mode = null;
        $('#log-geo').text('Pick from map');
        $('#log-nwm').text('Pick from map');
        $('#log-geo').removeClass('armed');
        $('#log-nwm').removeClass('armed');
        geoglows_layer.setVisible(true);
        nwm_layer.setVisible(false);

        $('#network-geoglows').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-nwm').removeClass('btn-primary').addClass('btn-outline-primary');
    });

    $('#log-nwm').on('click', function() {
        document.getElementById('nwm-id-input').classList.remove('is-invalid');
        document.getElementById('geo-id-input').classList.remove('is-invalid');
        map_mode = 'nwm'

        nwm_layer.setVisible(true);
        geoglows_layer.setVisible(false);

        $('#network-nwm').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-geoglows').removeClass('btn-primary').addClass('btn-outline-primary');
        $('#log-nwm').text('Select a reach');
        $('#log-nwm').addClass('armed');
        $('#log-geo').text('Pick from map');
        $('#log-geo').removeClass('armed');
        if (selectedNwmId){
            document.getElementById('nwm-id-input').value = selectedNwmId;
        }
        
    });
    $('#log-geo').on('click', function() {
        document.getElementById('nwm-id-input').classList.remove('is-invalid');
        document.getElementById('geo-id-input').classList.remove('is-invalid');
        map_mode = 'geoglows'
        
        geoglows_layer.setVisible(true);
        nwm_layer.setVisible(false);

        $('#network-geoglows').removeClass('btn-outline-primary').addClass('btn-primary');
        $('#network-nwm').removeClass('btn-primary').addClass('btn-outline-primary');
        $('#log-geo').text('Select a reach');
        $('#log-geo').addClass('armed');
        $('#log-nwm').text('Pick from map');
        $('#log-nwm').removeClass('armed');
        if (selectedGeoglowsId){
            document.getElementById('geo-id-input').value = selectedGeoglowsId;
        }
        
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

    document.getElementById('save-modal').addEventListener('shown.bs.modal', function() {
        render_hydrograph(true);
    });
    document.getElementById('nwm-id-input').addEventListener('keydown', function(event) {
        this.classList.remove('is-invalid')
        if(event.key === 'Enter'){
            nwm_logged = this.value.trim();
            nwm_logged = nwm_logged.replace(/^0+/, "");
            document.getElementById('nwm-id-input').value = nwm_logged;
            if (selectedUsgsId === null){
                document.getElementById('nwm-id-feedback').textContent = 'Select a gage first.';
                this.classList.add('is-invalid')
                return;
            } else if (!/^\d+$/.test(nwm_logged)) {
                this.classList.add('is-invalid')
                document.getElementById('nwm-id-feedback').textContent = 'Reach IDs are numbers only.';
                return;
            }
            var typed = true;
            var test_msg_nwm = msg_generation += 1;
            msg_has_note = false;
            $('#hydrograph-msg').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Confirming ID is valid, please wait.</p>')
            $('#panel-kge-rating').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>KGE ratings are dependent on reach data, please wait.</p>')
            load_reach(nwm_logged, 'NWM', test_msg_nwm, function(){
                if (test_msg_nwm === msg_generation){
                    if (!msg_has_note){
                        $('#hydrograph-msg').empty();
                    }
                    display_kge(selectedNwmId, selectedGeoglowsId, selectedUsgsId)
                }
            }, typed)
        }
    });
    document.getElementById('geo-id-input').addEventListener('keydown', function(event) {
        this.classList.remove('is-invalid');
        if(event.key === 'Enter'){
            geo_logged = this.value.trim();
            geo_logged = geo_logged.replace(/^0+/, "");
            document.getElementById('geo-id-input').value = geo_logged;
            if (selectedUsgsId === null){
                document.getElementById('geo-id-feedback').textContent = 'Select a gage first.';
                this.classList.add('is-invalid')
                return;
            } else if (!/^\d+$/.test(geo_logged)) {
                document.getElementById('geo-id-feedback').textContent = 'Reach IDs are numbers only.';
                this.classList.add('is-invalid')
                return;
            }
            var typed = true;
            var test_msg_geo = msg_generation += 1;
            msg_has_note = false;
            $('#hydrograph-msg').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Confirming ID is valid, please wait.</p>')
            $('#panel-kge-rating').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>KGE ratings are dependent on reach data, please wait.</p>')
            load_reach(geo_logged, 'GEOGLOWS', test_msg_geo, function(){
                if (test_msg_geo === msg_generation){
                    if (!msg_has_note){
                        $('#hydrograph-msg').empty();
                    }
                    display_kge(selectedNwmId, selectedGeoglowsId, selectedUsgsId)
                }
            }, typed)
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
        if (staged_nwm !== String(selectedNwmId) || staged_geoglows !== String(selectedGeoglowsId)){
            const unsavedModalEl = document.getElementById("non-selected-modal");
            bootstrap.Modal.getOrCreateInstance(unsavedModalEl).show();
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
            if ("NWM Error" in data || "GEOGLOWS Error"in data || "Error" in data){
                if (modalEl.classList.contains('show')){
                    bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                    const fModalEl = document.getElementById("fail-modal");
                    bootstrap.Modal.getOrCreateInstance(fModalEl).show();
                    $('#fail-text').text("Error in one or both KGE ratings");
                }
                nwm_kge = null;
                geo_kge = null;
                nwm_kge_length = null;
                geo_kge_length = null;
                document.getElementById('nwm-id-input').value = "";
                document.getElementById('geo-id-input').value = "";
                $("#save-and-verify").prop("disabled", false);
                return
            } else{
                nwm_kge = data.nwm_kge
                nwm_kge_length = data.nwm_kge_length;
                geo_kge = data.geo_kge
                geo_kge_length = data.geo_kge_length;
                // nwm_kge/geo_kge must stay numeric — they are exactly what
                // #save-confirm-footer POSTs to SAVE_URL. Rounding is for the
                // modal only, so it lands in its own variable; toFixed also
                // returns a string, and throws on a null KGE.
                var nwm_kge_text = nwm_kge;
                var geo_kge_text = geo_kge;
                if (nwm_kge === null){
                    nwm_color = 'red'
                }
                else{
                    nwm_color = null;
                    if (nwm_kge >= .3){
                        nwm_color = 'green';
                    } else if (-.41 <= nwm_kge){
                        nwm_color = 'darkgoldenrod';
                    } else{
                        nwm_color = 'red';
                    }
                    nwm_kge_text = nwm_kge.toFixed(2)
                }
                if (geo_kge === null){
                    geo_color = 'red'
                }
                else{
                    geo_color = null;
                    if (geo_kge >= .3){
                        geo_color = 'green';
                    } else if (-.41 <= geo_kge){
                        geo_color = 'darkgoldenrod';
                    } else{
                        geo_color = 'red';
                    }
                    geo_kge_text = geo_kge.toFixed(2)
                }
            }
            $("#save-and-verify").prop("disabled", false);
            $("#save-confirm-footer").prop("disabled", false);
            $("#geo-kge").text("GEOGLOWS KGE: " + geo_kge_text).css("color", geo_color);
            $("#nwm-kge").text("     NWM KGE: " + nwm_kge_text).css("color", nwm_color);
            
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
                usgsFeature.set('verification_status', data.status)
                usgsFeature.set('nwm_feature_id', staged_nwm)
                usgsFeature.set('geoglows_river_id', staged_geoglows)
                var min_distance = Infinity
                var next_gage = usgsFeature
                var prospect_coords = null
                var current_coords = usgsFeature.getGeometry().getCoordinates()
                var prospect_distance = null
                for (const gage of gage_source.getFeatures()){
                    if (gage.get("verification_status") === "Unverified" && shown_statuses.includes("Unverified")){
                        prospect_coords = gage.getGeometry().getCoordinates()
                        prospect_distance = Math.hypot((current_coords[0] - prospect_coords[0]), (current_coords[1] - prospect_coords[1]))
                        if (prospect_distance < min_distance){
                            min_distance = prospect_distance
                            next_gage = gage
                        }
                    }
                }
                select_gage(next_gage)
                const toastEl = document.getElementById("verified-toast");
                bootstrap.Toast.getOrCreateInstance(toastEl).show();
                document.getElementById('nwm-id-input').value = "";
                document.getElementById('geo-id-input').value = "";
                $('#log-geo').removeClass('armed');
                $('#log-nwm').removeClass('armed');
                $('#log-geo').text('Pick from map');
                $('#log-nwm').text('Pick from map');
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
    // The message div does double duty: it holds the spinner while requests are
    // in flight, and it holds an explanation ("no data", "invalid ID") once one
    // lands. Clearing the spinner when the last request finishes must not wipe
    // an explanation a sibling request already wrote. Set true by every write of
    // a real message, reset false whenever a fresh spinner goes in.
    var msg_has_note = false;
    // feature_count belongs to exactly one msg_generation — the group of requests
    // the current selection fired. Whoever starts a group assigns the count; a
    // callback from an older group is stale and must not touch it, or the counter
    // drifts and never lands on zero (spinner hangs forever).
    var feature_count = 0;
    var is_unreachable = false;
    // Written from two places (the gage callback and the wait-group completion),
    // so it lives in one constant — two copies would drift the moment the
    // wording changes.
    var UNREACHABLE_NOTE =
        '<div style="border-left: 4px solid #5a5f66; background: #f5f6f7; padding: 12px 16px; margin-top: 20px; border-radius: 4px;">' +
            '<div style="font-weight: 600; color: #33383f; margin-bottom: 4px;">No observed discharge</div>' +
            '<div style="color: #5a5f66; font-size: 0.9rem; line-height: 1.45;">This gage has no USGS record overlapping 2018&ndash;2022. Verify by spatial reference &mdash; compare the NWM and GEOGLOWS reaches for location and flow magnitude.</div>' +
        '</div>';
    function feature_done(cur_msg) {
        if (cur_msg !== msg_generation) {
            return;
        }
        feature_count -= 1;
        if (is_unreachable && feature_count === 0){
            $('#hydrograph-msg').html(UNREACHABLE_NOTE);
            display_kge(selectedNwmId, selectedGeoglowsId, selectedUsgsId)
        }
        else if (feature_count === 0) {
            if (!msg_has_note){
                $('#hydrograph-msg').empty();
            }
            display_kge(selectedNwmId, selectedGeoglowsId, selectedUsgsId)
        }
    }
    ol_map.on('singleclick', function(evt) {
        document.getElementById('nwm-id-input').classList.remove('is-invalid');
        document.getElementById('geo-id-input').classList.remove('is-invalid');
        var gage = null;
        var reach = null;

        ol_map.forEachFeatureAtPixel(evt.pixel, function(feature, layer) {
            if (layer === gage_layer && gage === null) {
                gage = feature;
            } else if ((layer === nwm_layer || layer === geoglows_layer) && reach === null) {
                reach = feature;
            }
            // Stop early once we have one of each.
            return gage !== null && reach !== null;
        }, { hitTolerance: 5 });   // 1px stream lines are hard to hit exactly

        if (gage !== null && gage.get('usgs_id') !== selectedUsgsId) {
            select_gage(gage)
            return
        }else if (gage){
            select_gage(gage, true)
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
            seededNwmId = null;
            baseNwmId = null;
            seededGeoglowsId = null;
            baseGeoglowsId = null;
            is_unreachable = false;
            nwm_layer.changed();
            geoglows_layer.changed();
            map_mode = null;
            $('#log-geo').text('Pick from map');
            $('#log-nwm').text('Pick from map');
            $('#log-geo').removeClass('armed');
            $('#log-nwm').removeClass('armed');
            document.getElementById('nwm-id-input').value = "";
            document.getElementById('geo-id-input').value = "";
            $('#panel-kge-rating').empty()
        }

        if (reach !== null) {

            if (gage === null || gage.get('usgs_id') === selectedUsgsId){
                // Starting a new wait group: this branch fires exactly one
                // load_reach, so the count is 1 outright, not an increment.
                feature_count = 1;
                msg_generation += 1;
                if (is_unreachable){
                    msg_has_note = true;
                }
                else{
                    msg_has_note = false;
                }
                $('#hydrograph-msg').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Loading data, please wait.</p>');
                $('#panel-kge-rating').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>KGE ratings are dependent on reach data, please wait.</p>');
            }
            var cur_msg = msg_generation;
            var network  = geoglows_layer.getVisible() ? "GEOGLOWS" : "NWM";
            var river_id = reach.get('station_id');

            load_reach(river_id, network, cur_msg, function() {
                feature_done(cur_msg);
            });
        };
    });

    function select_gage(gage_feature, recenter_only){
        if (gage_feature.get('unreachable') === true){
            is_unreachable = true
        }
        else{
            is_unreachable = false
        }
        var gage_coords = gage_feature.getGeometry().getCoordinates()
        ol_map.getView().animate({
            center: gage_coords,
            zoom: 16,
            duration: 300
        })
        if (recenter_only){
            return
        }
        // The geometry is in EPSG:3857 (the map projection); toLonLat converts
        // the coordinate back to [lon, lat] in 4326 for human-readable display —
        // the same transform-at-the-display-boundary rule used everywhere else.
        var lonLat = ol.proj.toLonLat(gage_feature.getGeometry().getCoordinates());
        feature_count = 1
        document.getElementById('nwm-id-input').value = "";
        document.getElementById('geo-id-input').value = "";
        map_mode = null;
        series_state = {};
        selection_generation += 1;
        msg_generation += 1;
        msg_has_note = false;
        var gage_status = gage_feature.get("verification_status")
        $('.panel-content').html(
            '<h4 class="title" style="color: blue; display: flex; justify-content: space-between; width: 100%; margin-top: 15px">' + '<span>Selected Gage </span>' + '<span style="color: ' + statusColors[gage_status] + '; margin-right: 35px; border: 2px solid ' + statusColors[gage_status] + '; padding: 4px 12px; border-radius: 20px;">' + gage_status + '</span>' + '</h4>' +
            '<h6 class="gage-name">' + gage_feature.get('gage_name') + '</h6>' +
            '<dl class="gage-meta">' +
                '<dt>USGS ID</dt><dd>' + gage_feature.get('usgs_id') + '</dd>' +
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
        $('#panel-kge-rating').html('<p class="fw-bold text-center mt-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>KGE ratings are dependent on reach data, please wait.</p>')
        seededNwmId = gage_feature.get('seeded_nwm_feature_id');
        baseNwmId = gage_feature.get('nwm_feature_id');
        seededGeoglowsId = gage_feature.get('seeded_geoglows_river_id');
        baseGeoglowsId = gage_feature.get('geoglows_river_id');
        if (baseNwmId){
            feature_count +=1;
        }
        if (baseGeoglowsId){
            feature_count += 1;
        }
        selectedNwmId = null;
        selectedGeoglowsId = null;
        selectedUsgsId = gage_feature.get('usgs_id');
        usgsFeature = gage_feature;
        $('#log-geo').text('Pick from map');
        $('#log-nwm').text('Pick from map');
        $('#log-geo').removeClass('armed');
        $('#log-nwm').removeClass('armed');
        document.getElementById('nwm-id-input').value = "";
        document.getElementById('geo-id-input').value = "";
        nwm_layer.changed();
        geoglows_layer.changed();

        // Capture the generation this request belongs to; the callback
        // compares it against the current one and drops stale responses.
        var gage_generation = selection_generation;
        var cur_msg = msg_generation;
        $.get(GAGES_MD_URL, { usgs_id: gage_feature.get('usgs_id') }, function(data) {
            if (gage_generation !== selection_generation) {
                feature_done(cur_msg);
                return;   // user has since selected a different gage (or cleared)
            }
            if (data.dates.length === 0) {
                if (cur_msg === msg_generation) {
                    msg_has_note = true;
                    $('#hydrograph-msg').html(UNREACHABLE_NOTE);
                }
                feature_done(cur_msg);
                return;
            }
            
            series_state['usgs'] = { 'dates': data.dates, 'values': data.values, 'name': 'USGS Observed' };

            feature_done(cur_msg);
            render_hydrograph();

        }).fail(function(){
            if (gage_generation !== selection_generation || cur_msg !== msg_generation) {
                feature_done(cur_msg);
                return;
            }
            msg_has_note = true;
            $('#hydrograph-msg').html('<p class="text-muted">Could not load USGS data — try re-selecting the gage.</p>');
            feature_done(cur_msg);
        });

        // Seeded crosswalk candidates from the tile data: load whichever
        // exist alongside the observed series, so a gage click lands with
        // all three hydrographs already drawn.
        if (baseGeoglowsId){
            load_reach(baseGeoglowsId, 'GEOGLOWS', cur_msg, function(){
                feature_done(cur_msg);
            });
        }
        if (baseNwmId){
            load_reach(baseNwmId, 'NWM', cur_msg, function(){
                feature_done(cur_msg);
            });
        }
        selection_source.clear();
        selection_source.addFeature(new ol.Feature(gage_feature.getGeometry()));
    }
    function commit_reach(river_id, network){
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

    }

    function load_reach(river_id, network, cur_msg, on_loaded, typed){
        if (!typed){
            commit_reach(river_id, network)
        }
        // Same staleness guard as the gage request: this reach series
        // belongs to the currently-selected gage, so drop the response if
        // the selection has changed by the time it arrives.
        var reach_generation = selection_generation;
        $.get(REACH_URL, { river_id: river_id, network: network }, function(data) {
            var cur_nwm_box = null;
            var cur_geo_box = null;
            if (reach_generation !== selection_generation) {
                on_loaded()
                return;
            }
            if ("error" in data && data.error === 'invalid id'){
                cur_geo_box = document.getElementById('geo-id-input').value.trim();
                cur_nwm_box = document.getElementById('nwm-id-input').value.trim();
                if (cur_msg === msg_generation){
                    msg_has_note = true;
                    $('#hydrograph-msg').html('<p class="text-muted">Invalid reach ID, please try again.</p>');
                }
                if (network === 'GEOGLOWS'){
                    if (cur_geo_box === String(river_id)){
                        document.getElementById('geo-id-input').value = "";
                    };
                } else {
                    if (cur_nwm_box === String(river_id)){
                        document.getElementById('nwm-id-input').value = "";
                    };
                }
                on_loaded();
                return;
            }
            else if ("error" in data && data.error === 'transient'){
                cur_geo_box = document.getElementById('geo-id-input').value.trim();
                cur_nwm_box = document.getElementById('nwm-id-input').value.trim();
                if (cur_msg === msg_generation){
                    msg_has_note = true;
                    $('#hydrograph-msg').html('<p class="text-muted">Unknown API error, please try again.</p>');
                }
                if (network === 'GEOGLOWS'){
                    if (cur_geo_box === String(river_id)){
                        document.getElementById('geo-id-input').value = "";
                    };
                } else {
                    if (cur_nwm_box === String(river_id)){
                        document.getElementById('nwm-id-input').value = "";
                    };
                }
                on_loaded();
                return;
            }
            else if ("error" in data && data.error === 'no data or invalid id'){
                cur_geo_box = document.getElementById('geo-id-input').value.trim();
                cur_nwm_box = document.getElementById('nwm-id-input').value.trim();
                if (cur_msg === msg_generation){
                    msg_has_note = true;
                    $('#hydrograph-msg').html('<p class="text-muted">No retrospective data or invalid ID (undeterminable).</p>');
                }
                on_loaded();
                return;
            }

            if (data.dates.length === 0) {
                if (typed){
                    commit_reach(river_id, network);
                }
                if (cur_msg === msg_generation) {
                    msg_has_note = true;
                    $('#hydrograph-msg').html('<p class="text-muted">No retrospective data for this reach.</p>');
                    series_state[network.toLowerCase()] = { 'dates': data.dates, 'values': data.values, 'name': network };
                    render_hydrograph();
                }
                on_loaded();
                return;
            }

            if (typed){
                commit_reach(river_id, network)
            }

            series_state[network.toLowerCase()] = { 'dates': data.dates, 'values': data.values, 'name': network };
            on_loaded();

            render_hydrograph();

        }).fail(function(){
            if (reach_generation !== selection_generation || cur_msg !== msg_generation) {
                on_loaded();
                return;
            }
            msg_has_note = true;
            $('#hydrograph-msg').html('<p class="text-muted">Could not load ' + network + ' data — try re-selecting the gage.</p>');
            on_loaded();
        });


    }
    function display_kge(nwm_id, geoglows_id, usgs_id){
        $.post(COMPUTE_KGE_URL, { nwm_id: nwm_id, geo_id: geoglows_id, usgs_id: usgs_id}, function(data){
            if ("Error" in data){
                $('#panel-kge-rating').empty()
                return
            }
            // Panel display only. These deliberately do NOT write to the outer
            // nwm_kge/geo_kge/nwm_color/geo_color: those hold the numeric values
            // #save-confirm-footer POSTs to SAVE_URL, and this panel refresh is
            // async — it can land while a save modal is open and would otherwise
            // overwrite the save payload with a rounded string, or with an error
            // message like "Missing data, check NWM ID".
            var nwm_text, nwm_col, geo_text, geo_col, nwm_border, geo_border;
            if ("unreachable" in data){
                nwm_text = 'N/A'
                geo_text = 'N/A'
                nwm_col = '#5a5f66'
                geo_col = '#5a5f66'
                nwm_border = 'dashed'
                geo_border = 'dashed'
            } else {
                if ("NWM Error" in data){
                    nwm_text = data['NWM Error']
                    nwm_col = 'red'
                }
                else if (data.nwm_kge === null){
                    nwm_text = 'N/A'
                    nwm_col = '#5a5f66'
                    nwm_border = 'dashed'
                }
                else{
                    nwm_border = 'solid'
                    nwm_text = data.nwm_kge
                    if (nwm_text >= .3){
                        nwm_col = 'green';
                    } else if (-.41 <= nwm_text){
                        nwm_col = 'darkgoldenrod';
                    } else{
                        nwm_col = 'red';
                    }
                    nwm_text = nwm_text.toFixed(2)
                }
                if ("GEOGLOWS Error" in data){
                    geo_text = data['GEOGLOWS Error']
                    geo_col = 'red'
                }
                else if (data.geo_kge === null){
                    geo_text = 'N/A'
                    geo_col = '#5a5f66'
                    geo_border = 'dashed'
                }
                else{
                    geo_text = data.geo_kge
                    geo_border = 'solid'
                    if (geo_text >= .3){
                        geo_col = 'green';
                    } else if (-.41 <= geo_text){
                        geo_col = 'darkgoldenrod';
                    } else{
                        geo_col = 'red';
                    }
                    geo_text = geo_text.toFixed(2)
                }
            }
            $('#panel-kge-rating').html(
                '<h4 style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; width: 100%; margin-top: 15px">' + '<span style="color: ' + nwm_col + '; border: 2px '+ nwm_border + ' ' + nwm_col + '; padding: 4px 12px; border-radius: 20px; justify-self: start;">NWM KGE: ' + nwm_text + '</span>' + '<span style="color: ' + geo_col + '; border: 2px '+ geo_border + ' ' + geo_col +'; padding: 4px 12px; border-radius: 20px; justify-self: start;">GEO KGE: ' + geo_text + '</span>' + '</h4>'
            )

        }).fail(function(){
            $('#panel-kge-rating').empty()
        });
    }
    function render_hydrograph(save_mode) {

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
        if (save_mode){
            var layout = {
                title: traces.map(function(t) { return t.name; }).join(' vs '),
                xaxis: { title: 'Date' },
                yaxis: { title: 'Discharge (' + current_unit + ')' },
                margin: {t: 40, b: 45, l: 55, r: 20},
                height: Math.round(window.innerHeight * (is_unreachable ? 0.46 : 0.58))

            };
            Plotly.react('hydro-modal', traces, layout);
            return;

        }
        var chart_ids = ['hydrograph-1', 'hydrograph-2', 'hydrograph-3'];
        var used_count;

        if (current_chart === 'single') {
            var layout = {
                // Title names exactly the series drawn — a reach-only chart says
                // "GEOGLOWS", a full comparison says "USGS Observed vs GEOGLOWS".
                title: traces.map(function(t) { return t.name; }).join(' vs '),
                xaxis: { title: 'Date' },
                yaxis: { title: 'Discharge (' + current_unit + ')' },
                margin: {t: 40, b: 45, l: 55, r: 20},
                height: Math.round(window.innerHeight * (is_unreachable ? 0.46 : 0.58))

            };

            Plotly.react('hydrograph-1', traces, layout, {responsive: true});
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

