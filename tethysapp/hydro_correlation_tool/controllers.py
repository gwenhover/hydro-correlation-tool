from tethys_sdk.routing import controller
from .app import App
from tethys_sdk.gizmos import MapView, MVView
from django.http import JsonResponse
from .fetchers import get_usgs_daily_discharge, get_geoglows_retrospective


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
    }

    return App.render(request, 'home.html', context)

@controller(
    name='get_gage_info',
    url='gage-info',
)
def get_gage_info(request):
    usgs_id = request.GET.get('usgs_id')
    gage_data = get_usgs_daily_discharge(
        usgs_id, '2020-01-01', '2020-12-31',
        api_key=App.get_custom_setting('USGS API Token'),
    )
    return JsonResponse(gage_data)

@controller(
    name='get_reach_info',
    url='reach-info',
)
def get_reach_info(request):
    river_id = request.GET.get('river_id')
    network = request.GET.get('network')
    if network == 'GEOGLOWS':
        reach_data = get_geoglows_retrospective(
            river_id, '2020-01-01', '2020-12-31'
        )
    else:
        reach_data = {'placeholder': []}
    return JsonResponse(reach_data)