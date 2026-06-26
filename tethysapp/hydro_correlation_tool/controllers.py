from tethys_sdk.routing import controller
from .app import App
from tethys_sdk.gizmos import MapView, MVView

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
            'CartoDB',
            {'CartoDB': {'style': 'dark'}},
            'OpenStreetMap',
            'ESRI'
        ],
        view=MVView(
            projection='EPSG:4326',
            center=[-95,37.5],
            zoom=5,
            maxZoom=18,
            minZoom=2
        )
    )

    context = {
        'map_view': map_view
    }

    return App.render(request, 'home.html', context)
