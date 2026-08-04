from tethys_sdk.routing import controller
from .app import App
from tethys_sdk.gizmos import MapView, MVView
from django.http import JsonResponse
from .fetchers import get_usgs_daily_discharge, get_geoglows_retrospective, get_nwm_retrospective
import geopandas as gpd
from django.http import HttpResponse
from sqlalchemy.orm import sessionmaker
from .model import cacheTable, hctTable
import pandas as pd
import hydroeval as he
from django.utils import timezone
from datetime import datetime
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
    usgs_id = (request.GET.get('usgs_id'))
    network = 'USGS'
    Engine = App.get_persistent_store_database('primary_db')
    Session = sessionmaker(bind=Engine)
    session = Session() 
    try:
        row = session.get(cacheTable, (network, int(usgs_id.removeprefix("USGS-"))))
        if (row):
            return (JsonResponse(row.reach_data))
        else:
            gage_data = get_usgs_daily_discharge(
                usgs_id, start_date, end_date,
                api_key=App.get_custom_setting('USGS API Token'),
            )
        if not gage_data.get("error"):
            new_row = cacheTable(network=network, reach_id=int(usgs_id.removeprefix("USGS-")), reach_data=gage_data, start_date=start_date, end_date=end_date)
            session.add(new_row)
            session.commit()
        return JsonResponse(gage_data)
    finally:
        session.close()

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
        if not reach_data.get("error") or reach_data.get("error") == "invalid id":
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

@controller(
    name='save_and_verify',
    url='s_and_v'
)
def save_and_verify(request):
    Engine = App.get_persistent_store_database('primary_db')
    Session = sessionmaker(bind=Engine)
    session = Session() 
    try:
        nwm_final  = int((request.POST.get('nwm_id')))
        geo_final  = int((request.POST.get('geo_id')))
        usgs_final = (request.POST.get('usgs_id')).removeprefix("USGS-")
        nwm_kge = float((request.POST.get('nwm_kge')))
        geo_kge = float((request.POST.get('geo_kge')))
        nwm_kge_length = int((request.POST.get('nwm_kge_length')))
        geo_kge_length = int((request.POST.get('geo_kge_length')))
        new_row = session.get(hctTable, f"USGS-{usgs_final}")
        if new_row is None:
            print("ERROR: Could not save or validate")
            return JsonResponse({"Error": "Could not save or validate"})
        if new_row.nwm_feature_id is not None and new_row.geoglows_river_id is not None and int(new_row.nwm_feature_id) == nwm_final and int(new_row.geoglows_river_id) == geo_final and new_row.verification_status != 'Edited':
            new_row.verification_status = 'Verified'
        else:
            new_row.verification_status = 'Edited'
            new_row.nwm_feature_id = nwm_final
            new_row.geoglows_river_id = geo_final
        verification_status = new_row.verification_status
        new_row.nwm_kge_rating = nwm_kge
        new_row.geoglows_kge_rating = geo_kge
        new_row.nwm_kge_shared_dates = nwm_kge_length
        new_row.geoglows_kge_shared_dates = geo_kge_length
        new_row.last_modified_by = request.user.username
        new_row.last_modified_timestamp = timezone.now()
        
        session.commit()
        
    finally:
        session.close()
    
    return JsonResponse({'status': verification_status})

def kge_rating(simulated, observed):
    
    kge_array, r, alpha, beta = he.evaluator(he.kge, simulated, observed)
    kge = float(kge_array[0])
    return kge

@controller(
    name='compute_kge',
    url='compute_kge_url'
)
def compute_kge(request):
    Engine = App.get_persistent_store_database('primary_db')
    Session = sessionmaker(bind=Engine)
    session = Session() 
    try:
        try:
            nwm_final  = int((request.POST.get('nwm_id')))
            geo_final  = int((request.POST.get('geo_id')))
        except:
            print("Error: Invalid ID(s) or session connect error")
            return JsonResponse({"Error": "Invalid ID(s) or session connect error"})
        usgs_final = (request.POST.get('usgs_id')).removeprefix("USGS-")
        nwm_row =  session.get(cacheTable, ("NWM", nwm_final))
        geo_row =  session.get(cacheTable, ("GEOGLOWS", geo_final))
        usgs_row = session.get(cacheTable, ("USGS", int(usgs_final)))
        if geo_row is None or len(geo_row.reach_data["dates"]) == 0 or nwm_row is None or len(nwm_row.reach_data["dates"]) == 0 or usgs_row is None or len(usgs_row.reach_data["dates"]) == 0:
            return JsonResponse({"Error": "Missing data, check selected IDs"})
        
        geo_df = pd.DataFrame({
            "dates": geo_row.reach_data["dates"], "values": geo_row.reach_data["values"]
        })
        geo_df = geo_df.drop_duplicates(subset="dates", keep="first")
        nwm_df = pd.DataFrame({
            "dates": nwm_row.reach_data["dates"], "values": nwm_row.reach_data["values"]
        })
        nwm_df = nwm_df.drop_duplicates(subset="dates", keep="first")
        usgs_df = pd.DataFrame({
            "dates": usgs_row.reach_data["dates"], "values": usgs_row.reach_data["values"]
        })
        usgs_df = usgs_df.drop_duplicates(subset="dates", keep="first")
        geo_df["dates"] = pd.to_datetime(geo_df["dates"])
        nwm_df["dates"] = pd.to_datetime(nwm_df["dates"])
        usgs_df["dates"] = pd.to_datetime(usgs_df["dates"])
        geo_df = geo_df.set_index("dates")
        nwm_df = nwm_df.set_index("dates")
        usgs_df = usgs_df.set_index("dates")
        geo_shared_dates = geo_df.index.intersection(usgs_df.index)
        geo_df_shared = geo_df.loc[geo_shared_dates]
        usgs_geo_shared = usgs_df.loc[geo_shared_dates]
        nwm_shared_dates = nwm_df.index.intersection(usgs_df.index)
        nwm_df_shared = nwm_df.loc[nwm_shared_dates]
        usgs_nwm_shared = usgs_df.loc[nwm_shared_dates]
        if len(nwm_shared_dates) == 0:
            print("Error: No NWM overlapping dates")
            return JsonResponse({"Error": "No NWM overlapping dates"})
        if len(geo_shared_dates) == 0:
            print("Error: No GEOGLOWS overlapping dates")
            return JsonResponse({"Error": "No GEOGLOWS overlapping dates"})
        nwm_usgs_kge = kge_rating(nwm_df_shared['values'].to_numpy(), usgs_nwm_shared['values'].to_numpy())
        geo_usgs_kge = kge_rating(geo_df_shared['values'].to_numpy(), usgs_geo_shared['values'].to_numpy())
        nwm_usgs_kge_length = len(nwm_df_shared['values'])
        geo_usgs_kge_length = len(geo_df_shared['values'])
        
    finally:
        session.close()
        
    return JsonResponse({'nwm_kge': float(nwm_usgs_kge), 'geo_kge': float(geo_usgs_kge), 'nwm_kge_length': nwm_usgs_kge_length, 'geo_kge_length': geo_usgs_kge_length})

@controller(
    name='export_csv',
    url='export_csv_url'
)
def export_csv (request):
    Engine = App.get_persistent_store_database('primary_db')
    sql = 'SELECT usgs_id, gage_name, nwm_feature_id, geoglows_river_id, latitude, longitude, nwm_kge_rating, nwm_kge_shared_dates, geoglows_kge_rating, geoglows_kge_shared_dates, verification_status, last_modified_by, last_modified_timestamp FROM gage_mapping ORDER BY usgs_id;'
    raw_conn = Engine.raw_connection()
    try:
        df_table = pd.read_sql(sql, raw_conn)
        df_table['nwm_feature_id'] = df_table['nwm_feature_id'].astype("Int64")
        df_table['geoglows_river_id'] = df_table['geoglows_river_id'].astype("Int64")
        df_table['nwm_kge_shared_dates'] = df_table['nwm_kge_shared_dates'].astype("Int64")
        df_table['geoglows_kge_shared_dates'] = df_table['geoglows_kge_shared_dates'].astype("Int64")
        df_table['last_modified_timestamp'] = df_table['last_modified_timestamp'].dt.strftime('%Y-%m-%d %H:%M %Z')
        csv_string = df_table.to_csv(index=False)
        time_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        my_response = HttpResponse(csv_string, content_type="text/csv")
        my_response["Content-Disposition"] = f'attachment; filename="reach_id_crosswalk_{time_now}_UTC.csv"'
    finally:
        raw_conn.close()
    return my_response