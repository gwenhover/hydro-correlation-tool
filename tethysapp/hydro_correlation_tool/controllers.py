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
import math
import os
from .fetchers import da_reference_ready, ensure_da_reference_tables, start_da_reference_download, lookup_da_reference, get_usgs_drainage_areas
from .model import UsgsDrainageArea
from .da_analysis import build_da_columns, da_result, nwm_us_ds, geoglows_us_ds, sq_mi_to_km2
from .model import VerificationLog, VERIFICATION_REASONS, clean_verification_reason
from sqlalchemy import inspect, text
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
        'verification_reasons': VERIFICATION_REASONS,
        'verification_models': (('geoglows', 'GEOGLOWS'), ('nwm', 'NWM')),
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
        if not gage_data.get("error") or gage_data.get("error") == "no_data":
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
        if not reach_data.get("error") or reach_data.get("error") == "invalid id" or reach_data.get("error") == "no data or invalid id":
            new_row = cacheTable(network=network, reach_id=river_id, reach_data=reach_data, start_date=start_date, end_date=end_date)
            session.add(new_row)
            session.commit()
        return JsonResponse(reach_data)
    finally:
        session.close()

UNREACHABLE_CSV = os.path.join(os.path.dirname(__file__), 'public', 'data', 'unreachable_gages.csv')
UNREACHABLE_GAGES = (pd.read_csv(UNREACHABLE_CSV)['gages'].astype(str).to_list())

@controller(
    name='db_get_gage',
    url='db_gage',
)
def db_get_gage(request):
    Engine = App.get_persistent_store_database('primary_db')
    sql = 'SELECT usgs_id, nwm_feature_id, gage_name, geoglows_river_id, geom, verification_status, seeded_nwm_feature_id, seeded_geoglows_river_id FROM gage_mapping;'
    # pandas 2.x requires SQLAlchemy>=2.0 to recognize an Engine directly, but
    # tethys_dataset_services pins sqlalchemy<2 — pass the raw DBAPI connection
    # instead so pandas' legacy DBAPI2 path (which read_postgis also uses) works.
    raw_conn = Engine.raw_connection()
    try:
        gdf = gpd.read_postgis(sql, raw_conn, 'geom')
        gdf['unreachable'] = gdf['usgs_id'].isin(UNREACHABLE_GAGES)
        gdf_json = gdf.to_json()
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
        nwm_kge = (request.POST.get('nwm_kge'))
        geo_kge = (request.POST.get('geo_kge'))
        if nwm_kge:
            nwm_kge = float(nwm_kge)
        else:
            nwm_kge = None
        if geo_kge:
            geo_kge = float(geo_kge)
        else:
            geo_kge = None
        nwm_kge_length = (request.POST.get('nwm_kge_length'))
        geo_kge_length = (request.POST.get('geo_kge_length'))
        if nwm_kge_length:
            nwm_kge_length = int(nwm_kge_length)
        else: 
            nwm_kge_length = None
        if geo_kge_length:
            geo_kge_length = int(geo_kge_length)
        else:
            geo_kge_length = None
        nwm_reason, nwm_comment = clean_verification_reason(request.POST.get('nwm_verification_reason'), request.POST.get('nwm_verification_comment'))
        geo_reason, geo_comment = clean_verification_reason(request.POST.get('geoglows_verification_reason'), request.POST.get('geoglows_verification_comment'))
        if nwm_reason is None or geo_reason is None:
            print("ERROR: Could not save or validate\nCode: missing or invalid NWM/GEOGLOWS verification reason")
            return JsonResponse({"Error": "Could not save or validate"})
        ensure_verification_log_table(Engine)
        new_row = session.get(hctTable, f"USGS-{usgs_final}")
        if new_row is None:
            print("ERROR: Could not save or validate")
            return JsonResponse({"Error": "Could not save or validate"})
        if new_row.seeded_nwm_feature_id is not None and new_row.seeded_geoglows_river_id is not None and int(new_row.seeded_nwm_feature_id) == nwm_final and int(new_row.seeded_geoglows_river_id) == geo_final:
            new_row.verification_status = 'Verified'
            new_row.nwm_feature_id = nwm_final
            new_row.geoglows_river_id = geo_final
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
        session.add(VerificationLog(usgs_id=new_row.usgs_id, nwm_feature_id=nwm_final, geoglows_river_id=geo_final, verification_status=verification_status, nwm_reason=nwm_reason, nwm_comment=nwm_comment, geoglows_reason=geo_reason, geoglows_comment=geo_comment, username=new_row.last_modified_by, timestamp=new_row.last_modified_timestamp))
        
        session.commit()
    except Exception as e:
        print("ERROR: Could not save or validate\nCode: " + repr(e))
        return JsonResponse({"Error": "Could not save or validate"})
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
    nwm_error = False
    geo_error = False
    try:
        usgs_final = (request.POST.get('usgs_id')).removeprefix("USGS-")
        usgs_row = session.get(cacheTable, ("USGS", int(usgs_final)))
        if usgs_row is None or len(usgs_row.reach_data["dates"]) == 0:
            return JsonResponse({'nwm_kge': None, 'geo_kge': None, 'nwm_kge_length': None, 'geo_kge_length': None, 'unreachable': True})
        usgs_df = load_series(usgs_row)
        
        nwm = compute_one_kge(session, 'NWM', request.POST.get('nwm_id'), usgs_df)
        geoglows = compute_one_kge(session, 'GEOGLOWS', request.POST.get('geo_id'), usgs_df)

        if ("NWM Error" not in nwm):
            if (nwm['kge'] is not None):
                nwm_kge = float(nwm['kge'])
            else:
                nwm_kge = nwm['kge']
            nwm_length = nwm['length']
        else:
            nwm_error = True
            
        if ("GEOGLOWS Error" not in geoglows):
            if (geoglows['kge'] is not None):
                geo_kge = float(geoglows['kge'])
            else:
                geo_kge = geoglows['kge']
            geo_length = geoglows['length']
        else:
            geo_error = True

        if (nwm_error and geo_error):
            return JsonResponse(nwm | geoglows)
        if (nwm_error):
            return JsonResponse({'geo_kge': geo_kge, 'geo_kge_length': geo_length} | nwm)
        if (geo_error):
            return JsonResponse({'nwm_kge': nwm_kge, 'nwm_kge_length': nwm_length} | geoglows)
        if nwm_kge is None and geo_kge is None:
            return JsonResponse({'nwm_kge': nwm_kge, 'geo_kge': geo_kge, 'nwm_kge_length': nwm_length, 'geo_kge_length': geo_length})
        if nwm_kge is None:
            return JsonResponse({'nwm_kge': nwm_kge, 'geo_kge': float(geo_kge), 'nwm_kge_length': nwm_length, 'geo_kge_length': geo_length})
        if geo_kge is None:
            return JsonResponse({'nwm_kge': float(nwm_kge), 'geo_kge': geo_kge, 'nwm_kge_length': nwm_length, 'geo_kge_length': geo_length})
    except Exception as e:
        print(f"KGE ERROR: {repr(e)}")
        return JsonResponse({"Error": "Unknown error"})
    finally:
        session.close()
        
    return JsonResponse({'nwm_kge': float(nwm_kge), 'geo_kge': float(geo_kge), 'nwm_kge_length': nwm_length, 'geo_kge_length': geo_length})



def load_series(row):
    df = pd.DataFrame({"dates": row.reach_data["dates"], "values": row.reach_data["values"]})
    df = df.drop_duplicates(subset="dates", keep="first")
    df["dates"] = pd.to_datetime(df["dates"])
    df = df.set_index("dates")
    return df


def compute_one_kge(session, network, raw_id, usgs_df):
    try:
        try:
            if not raw_id:
                return {"kge": None, "length": None}
            id = int(raw_id)
        except:
            print(f"{network} Error: Invalid {network} ID or session connect error")
            return {f"{network} Error": f"Invalid {network} ID or session connect error"}
        row =  session.get(cacheTable, (network, id))
        if row is None or len(row.reach_data["dates"]) == 0:
            return {f"{network} Error": f"Missing data, check {network} ID"}
        df = load_series(row)
        shared_dates = df.index.intersection(usgs_df.index)
        df_shared = df.loc[shared_dates]
        usgs_df_shared = usgs_df.loc[shared_dates]
        if len(shared_dates) == 0:
            print(f"{network} Error: No {network} overlapping dates")
            return {f"{network} Error": f"No {network} overlapping dates"}
        kge = kge_rating(df_shared['values'].to_numpy(), usgs_df_shared['values'].to_numpy())
        kge_length = len(df_shared['values'])
        if math.isnan(kge):
            return {"kge": None, "length": kge_length}
    except Exception as e:
        print (f"{network} Error: " + repr(e))
        return {f"{network} Error": f"Unknown {network} Error"}
    
    return {"kge": kge, "length": kge_length}



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
        df_table = append_da_columns(df_table)
        df_table = append_verification_columns(df_table)
        csv_string = df_table.to_csv(index=False)
        time_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        my_response = HttpResponse(csv_string, content_type="text/csv")
        my_response["Content-Disposition"] = f'attachment; filename="reach_id_crosswalk_{time_now}_UTC.csv"'
    finally:
        raw_conn.close()
    return my_response


# --- DA Analysis -------------------------------------------------------------
# Advisory drainage-area check shown under the KGE pills and appended to the
# export CSV. It never touches gage_mapping and never blocks Save and Verify.

_usgs_da_table_ready = False


def cached_usgs_da_km2(Engine, usgs_ids):
    # {usgs_id: km2 or None}, from the usgs_drainage_area table, fetching from
    # USGS (in batches) only the gages not cached yet.
    global _usgs_da_table_ready
    if not _usgs_da_table_ready:
        # Created here rather than relying on init_primary_db, so databases
        # set up before this table existed get it without a syncstores run.
        UsgsDrainageArea.__table__.create(Engine, checkfirst=True)
        _usgs_da_table_ready = True
    Session = sessionmaker(bind=Engine)
    session = Session()
    try:
        wanted = set(usgs_ids)
        cached = {
            row.usgs_id: row.drainage_area_km2
            for row in session.query(UsgsDrainageArea).filter(UsgsDrainageArea.usgs_id.in_(wanted))
        }
        missing = sorted(wanted - cached.keys())
        if missing:
            found, failed = get_usgs_drainage_areas(missing, api_key=App.get_custom_setting('USGS API Token'))
            now = timezone.now()
            for usgs_id, sq_mi in found.items():
                km2 = sq_mi_to_km2(sq_mi)
                session.merge(UsgsDrainageArea(usgs_id=usgs_id, drainage_area_sq_mi=sq_mi, drainage_area_km2=km2, fetched_at=now))
                cached[usgs_id] = km2
            try:
                session.commit()
            except Exception as e:
                # e.g. another request cached the same gage first -- the values
                # we fetched are still good for this response.
                print("USGS drainage area cache write failed:", repr(e))
                session.rollback()
        return cached
    finally:
        session.close()


def _model_us_ds(network, reach_id):
    if not reach_id:
        return None, None
    reference = lookup_da_reference(network, [reach_id])
    if int(reach_id) not in reference.index:
        return None, None
    row = reference.loc[int(reach_id)]
    if network == 'NWM':
        return nwm_us_ds(row['totdasqkm'], row['areasqkm'])
    return geoglows_us_ds(row['USContArea'], row['DSContArea'])


@controller(
    name='da_analysis',
    url='da-analysis',
)
def da_analysis(request):
    try:
        raw_usgs = request.GET.get('usgs_id')
        nwm_id = request.GET.get('nwm_id')
        geo_id = request.GET.get('geo_id')
        nwm_id = int(nwm_id) if nwm_id else None
        geo_id = int(geo_id) if geo_id else None

        usgs_da = None
        if raw_usgs:
            usgs_id = f"USGS-{raw_usgs.removeprefix('USGS-')}"
            Engine = App.get_persistent_store_database('primary_db')
            usgs_da = cached_usgs_da_km2(Engine, [usgs_id]).get(usgs_id)

        if da_reference_ready():
            reference = 'ready'
            nwm_us, nwm_ds = _model_us_ds('NWM', nwm_id)
            geo_us, geo_ds = _model_us_ds('GEOGLOWS', geo_id)
        else:
            # First use on this server: download in the background, answer NO DATA now.
            start_da_reference_download()
            reference = 'downloading'
            nwm_us = nwm_ds = geo_us = geo_ds = None

        return JsonResponse({
            'reference': reference,
            'nwm': da_result(usgs_da, nwm_us, nwm_ds),
            'geoglows': da_result(usgs_da, geo_us, geo_ds),
        })
    except Exception as e:
        print("DA ERROR: " + repr(e))
        return JsonResponse({"Error": "DA analysis failed"})


def append_da_columns(df_table):
    # Export hook. DA is computed from the saved IDs on each row. Any failure
    # gives NO DATA columns rather than a failed export.
    try:
        Engine = App.get_persistent_store_database('primary_db')
        usgs_da = cached_usgs_da_km2(Engine, df_table['usgs_id'].tolist())
        ensure_da_reference_tables()
        nwm_reference = lookup_da_reference('NWM', df_table['nwm_feature_id'])
        geoglows_reference = lookup_da_reference('GEOGLOWS', df_table['geoglows_river_id'])
    except Exception as e:
        print("DA export ERROR: " + repr(e))
        usgs_da = {}
        nwm_reference = pd.DataFrame(columns=['totdasqkm', 'areasqkm'])
        geoglows_reference = pd.DataFrame(columns=['USContArea', 'DSContArea'])
    return build_da_columns(df_table, usgs_da, nwm_reference, geoglows_reference)


# --- Verification reason -----------------------------------------------------
# The NWM and GEOGLOWS reasons chosen in the Save and Verify popup are logged in
# verification_log by save_and_verify; the export appends each gage's latest.

VERIFICATION_EXPORT_COLUMNS = ['nwm_verification_reason', 'nwm_verification_comment', 'geoglows_verification_reason', 'geoglows_verification_comment']

_verification_log_table_ready = False


def ensure_verification_log_table(Engine):
    # Created on first use, so databases set up before this table existed get
    # it without a syncstores run.
    global _verification_log_table_ready
    if not _verification_log_table_ready:
        VerificationLog.__table__.create(Engine, checkfirst=True)
        upgrade_verification_log_table(Engine)
        _verification_log_table_ready = True


def upgrade_verification_log_table(Engine):
    # Databases that ran the single-reason version have verification_log with
    # one required reason/comment. Add the per-model columns and let the old
    # reason be empty, so new saves fit; old rows are kept as they are.
    columns = {column['name'] for column in inspect(Engine).get_columns('verification_log')}
    if 'reason' not in columns:
        return
    with Engine.begin() as conn:
        for column in ('nwm_reason', 'nwm_comment', 'geoglows_reason', 'geoglows_comment'):
            if column not in columns:
                conn.execute(text('ALTER TABLE verification_log ADD COLUMN IF NOT EXISTS ' + column + ' VARCHAR'))
        conn.execute(text('ALTER TABLE verification_log ALTER COLUMN reason DROP NOT NULL'))


def append_verification_columns(df_table):
    # Export hook: the NWM and GEOGLOWS reason/comment from each gage's latest
    # save. Blank for gages never saved since reasons were added; any failure
    # gives blank columns rather than a failed export.
    sql = ('SELECT DISTINCT ON (usgs_id) usgs_id, '
           'nwm_reason AS nwm_verification_reason, nwm_comment AS nwm_verification_comment, '
           'geoglows_reason AS geoglows_verification_reason, geoglows_comment AS geoglows_verification_comment '
           'FROM verification_log ORDER BY usgs_id, timestamp DESC, id DESC;')
    try:
        Engine = App.get_persistent_store_database('primary_db')
        ensure_verification_log_table(Engine)
        raw_conn = Engine.raw_connection()
        try:
            latest = pd.read_sql(sql, raw_conn)
        finally:
            raw_conn.close()
    except Exception as e:
        print("Verification reason export ERROR: " + repr(e))
        latest = pd.DataFrame(columns=['usgs_id'] + VERIFICATION_EXPORT_COLUMNS)
    latest = latest.set_index('usgs_id')
    df_table = df_table.copy()
    for column in VERIFICATION_EXPORT_COLUMNS:
        df_table[column] = df_table['usgs_id'].map(latest[column])
    return df_table
