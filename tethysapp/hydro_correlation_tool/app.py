from tethys_sdk.base import TethysAppBase
from tethys_sdk.app_settings import CustomSetting

class App(TethysAppBase):
    """
    Tethys app class for Hydro Correlation Tool.
    """
    name = 'Hydro Correlation Tool'
    description = 'The Hydro Correlation Tool is a single-user scientific workbench for building and maintaining a high-quality cross-mapping table that links each active USGS streamflow gage to corresponding NWM v3 reach and GEOGLOWS v2 river.'
    package = 'hydro_correlation_tool'  # WARNING: Do not change this value
    index = 'home'
    icon = f'{package}/images/icon_gage_on_river.svg'
    root_url = 'hydro-correlation-tool'
    color = '#002E5D'
    tags = '"NWM","GEOGLOWS","USGS Gages"'
    enable_feedback = False
    feedback_emails = []

    def custom_settings(self):
        custom_settings = (
            CustomSetting(
                name='MapBox PK Token',
                type=CustomSetting.TYPE_STRING,
                description='The key for the mapbox vector tiles.',
                required=False
            ),
            CustomSetting(
                name='USGS API Token',
                type=CustomSetting.TYPE_STRING,
                description='The key for the USGS data.',
                required=False
            ),
        )
        return custom_settings