from tethys_sdk.base import TethysAppBase
from tethys_sdk.app_settings import CustomSetting

class App(TethysAppBase):
    """
    Tethys app class for Hydro Correlation Tool.
    """
    name = 'Hydro Correlation Tool'
    description = 'The Hydro Correlation Tool is a single-user scientific workbench for building and maintaining a high-quality cross-mapping table that links each active USGS streamflow gage to orresponding NWM v3 reach and GEOGLOWS v2 river.'
    package = 'hydro_correlation_tool'  # WARNING: Do not change this value
    index = 'home'
    icon = f'{package}/images/icon_gage_on_river.svg'
    root_url = 'hydro-correlation-tool'
    color = '#002E5D'
    tags = '"NWM","GEOGLOWS","USGS Gages"'
    enable_feedback = False
    feedback_emails = []

    def custom_settings(self):
        """
        Placeholder custom settings for when I add the MapBox token, headwater threshold, and data-API endpoints.
        """
        custom_settings = (
            CustomSetting(
                name='placeholder',
                type=CustomSetting.TYPE_INTEGER,
                description='placeholder.',
                required=False
            ),
        )
        return custom_settings