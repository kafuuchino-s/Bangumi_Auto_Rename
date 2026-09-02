from src.api.serializers import mask_secrets
from src.config.config_manager import CONFIG_DEFAULT, cm
from src.pages.config_field_spec import spec_by_key
from src.subtitle.providers import (
    CombinedSubtitleProvider,
    MoviePilotProvider,
    build_subtitle_provider,
)


def test_moviepilot_subtitle_config_defaults_are_inactive_and_secret() -> None:
    assert CONFIG_DEFAULT["subtitle_auto_fetch_provider"] == "acgrip"
    assert CONFIG_DEFAULT["subtitle_auto_fetch_moviepilot_api_token"] == ""
    assert CONFIG_DEFAULT["subtitle_auto_fetch_moviepilot_save_path"] == ""

    specs = spec_by_key()
    provider = specs["subtitle_auto_fetch_provider"]
    assert provider["options"] == [
        "acgrip",
        "moviepilot",
        "acgrip_moviepilot",
    ]
    token_spec = specs["subtitle_auto_fetch_moviepilot_api_token"]
    assert token_spec["control"] == "secret"
    assert mask_secrets(
        {"subtitle_auto_fetch_moviepilot_api_token": "private"}
    ) == {"subtitle_auto_fetch_moviepilot_api_token": "*******"}


def test_provider_factory_builds_moviepilot_and_combined_modes() -> None:
    common = {
        "subtitle_auto_fetch_moviepilot_api_token": "test-token",
        "subtitle_auto_fetch_moviepilot_save_path": "H:/Subtitle Staging",
    }
    with cm.temporary_config(
        {**common, "subtitle_auto_fetch_provider": "moviepilot"}
    ):
        assert isinstance(build_subtitle_provider(), MoviePilotProvider)
    with cm.temporary_config(
        {**common, "subtitle_auto_fetch_provider": "acgrip_moviepilot"}
    ):
        assert isinstance(build_subtitle_provider(), CombinedSubtitleProvider)
