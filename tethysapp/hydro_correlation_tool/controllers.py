from tethys_sdk.routing import controller
from .app import App
from tethys_sdk.gizmos import MapView, MVView
from django.http import JsonResponse
from .fetchers import get_usgs_daily_discharge, get_geoglows_retrospective, get_nwm_retrospective
import geopandas as gpd
from django.http import HttpResponse
from sqlalchemy.orm import sessionmaker
from .model import cacheTable

start_date = '2018-01-01'
end_date = '2022-12-31'

@controller
def home(request):
    """
    Controller for the app home page.
    """
    

    
    # This renders the base map on the home page
    map_view = MapView(
        height='100%',
        width='100%',
        controls=[
            'ZoomSlider', 'Rotate', 'FullScreen',
            {'ZoomToExtent': {
                'projection': 'EPSG:4326',
                'extent': [-125, 24, -66, 50]
            }}
        ],
        basemap=[
            'ESRI',
            {'CartoDB': {'style': 'dark'}},
            'OpenStreetMap',
            'CartoDB'
        ],
        view=MVView(
            # Web Mercator (EPSG:3857) — the native projection of the basemap
            # tiles and of the MapBox vector tiles used later for the stream
            # networks. center is the CONUS midpoint [-95, 37.5] transformed
            # into 3857 meters. JS (main.js) fits the view to the CONUS extent
            # on load; this initial center/zoom just frames it before that runs.
            projection='EPSG:3857',
            center=[-10575351.63, 4509031.39],
            zoom=4.5,
            maxZoom=18,
            minZoom=2
        )
    )

    context = {
        'map_view': map_view,
        'mapbox_token': App.get_custom_setting('MapBox PK Token'),
        'headwater_threshold': App.get_custom_setting('Headwater Threshold') or 2,
    }

    return App.render(request, 'home.html', context)

@controller(
    name='get_gage_info',
    url='gage-info',
)
def get_gage_info(request):
    usgs_id = request.GET.get('usgs_id')
    gage_data = get_usgs_daily_discharge(
        usgs_id, start_date, end_date,
        api_key=App.get_custom_setting('USGS API Token'),
    )
    return JsonResponse(gage_data)

@controller(
    name='get_reach_info',
    url='reach-info',
)
def get_reach_info(request):
    river_id = int(request.GET.get('river_id'))
    network = request.GET.get('network')
    Engine = App.get_persistent_store_database('primary_db')
    Session = sessionmaker(bind=Engine)
    session = Session() 
    try:
        row = session.get(cacheTable, (network, river_id))
        if (row):
            return (JsonResponse(row.reach_data))
        if network == 'GEOGLOWS':
            reach_data = get_geoglows_retrospective(
                river_id, start_date, end_date
            )
        else:
            reach_data = get_nwm_retrospective(
                river_id, start_date, end_date, api_key=App.get_custom_setting('NWM API Token')
            )
        if (reach_data['dates']):
            new_row = cacheTable(network=network, reach_id=river_id, reach_data=reach_data, start_date=start_date, end_date=end_date)
            session.add(new_row)
            session.commit()
        return JsonResponse(reach_data)
    finally:
        session.close()

@controller(
    name='db_get_gage',
    url='db_gage',
)
def db_get_gage(request):
    Engine = App.get_persistent_store_database('primary_db')
    sql = 'SELECT usgs_id, nwm_feature_id, gage_name, geoglows_river_id, geom, verification_status FROM gage_mapping;'
    # pandas 2.x requires SQLAlchemy>=2.0 to recognize an Engine directly, but
    # tethys_dataset_services pins sqlalchemy<2 — pass the raw DBAPI connection
    # instead so pandas' legacy DBAPI2 path (which read_postgis also uses) works.
    raw_conn = Engine.raw_connection()
    try:
        gdf_json = gpd.read_postgis(sql, raw_conn, 'geom').to_json()
    finally:
        raw_conn.close()
    return (HttpResponse(gdf_json, content_type="application/json"))
    