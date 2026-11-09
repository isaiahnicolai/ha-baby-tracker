"""Core PR 27: the last bath sensor, the Bath button, and the log_activity action."""

from typing import Any

import probatio
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pybabytracker import OtherActivity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.baby_tracker.const import DOMAIN

from . import BABY_TWO, NOW, tick
from .factories import BABY_ONE_ID, DEVICE_A, at
from .fake_server import FakeServer
from .helpers import device_id, posted

LAST_BATH = "sensor.baby_one_last_bath"
BATH_BUTTON = "button.baby_one_bath"
BATH_ID = "A0000000-0000-4000-8000-0000000000BA"
TUMMY_TIME_ID = "EasyLogSeed3559dca0-8cbf-11e2-9e96-0800200c9a66"
# A "Bath" activity type added in the app, as an iPhone embeds it, with invented values.
BATH = {
    "BCObjectType": "OtherActivityDescription",
    "objectID": BATH_ID,
    "name": "Bath",
    "newFlage": "false",
    "timestamp": "1970-01-01 00:00:00 +0000",
}
# The same type as an Android phone embeds it: no BCObjectType, and its own timestamp.
ANDROID_BATH = {
    "objectID": BATH_ID,
    "name": "Bath",
    "newFlage": False,
    "timestamp": "2026-04-01 00:00:00 +0000",
}


@pytest.fixture(autouse=True)
def _start_at_now(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock at NOW before anything is set up."""
    freezer.move_to(NOW)


def _activity(server: FakeServer, number: int, minute: float, desc: Any, **fields: Any) -> None:
    server.add_record(
        DEVICE_A,
        "OtherActivity",
        f"B0000000-0000-4000-8000-{number:012d}",
        when=at(minute),
        desc=desc,
        duration=fields.pop("duration", 0),
        **fields,
    )


def _state(hass: HomeAssistant, entity_id: str) -> Any:
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} does not exist"
    return state


async def _log(hass: HomeAssistant, activity: Any, **extra: Any) -> None:
    data = {"device_id": device_id(hass, BABY_ONE_ID), "activity": activity, **extra}
    await hass.services.async_call(DOMAIN, "log_activity", data, blocking=True)


async def test_the_last_bath_is_the_newest_activity_named_bath(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer
) -> None:
    assert _state(hass, LAST_BATH).state == "unknown"
    _activity(server, 1, 30, BATH, note="Bubbles")
    _activity(server, 2, 60, ANDROID_BATH, duration=10, note="Hair wash")
    _activity(server, 3, 90, {"objectID": TUMMY_TIME_ID, "name": "Tummy Time"}, duration=15)
    _activity(server, 4, 120, BATH, baby=BABY_TWO)
    await tick(hass)

    state = _state(hass, LAST_BATH)
    assert state.state == at(60).isoformat()
    assert state.attributes["duration_minutes"] == 10
    assert state.attributes["note"] == "Hair wash"
    assert state.attributes["device_class"] == "timestamp"


@pytest.mark.parametrize("name", ["bath", " BATH ", "Bath"])
async def test_a_bath_type_matches_in_any_case(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer, name: str
) -> None:
    _activity(server, 1, 30, {**BATH, "name": name})
    await tick(hass)

    assert _state(hass, LAST_BATH).state == at(30).isoformat()


async def test_the_bath_button_logs_a_bath_now_of_the_accounts_bath_type(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer
) -> None:
    _activity(server, 1, 30, BATH)
    await tick(hass)

    await hass.services.async_call("button", "press", {"entity_id": BATH_BUTTON}, blocking=True)

    [(_, _, record)] = posted(server)
    assert isinstance(record, OtherActivity)
    assert record.activity_name == "Bath"
    assert record.desc == BATH
    assert (record.duration, record.note, record.time) == (0, "Added from Home Assistant", NOW)
    assert _state(hass, LAST_BATH).state == NOW.isoformat()


async def test_the_bath_button_without_a_bath_type_says_to_add_one_in_the_app(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer
) -> None:
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call("button", "press", {"entity_id": BATH_BUTTON}, blocking=True)

    assert raised.value.translation_key == "unknown_activity"
    assert raised.value.translation_placeholders == {"activity": "Bath"}
    assert posted(server) == []


async def test_log_activity_logs_a_built_in_type_with_minutes_and_a_note(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer
) -> None:
    await _log(hass, "tummy time", minutes=15, note="On the play mat")

    [(_, _, record)] = posted(server)
    assert isinstance(record, OtherActivity)
    assert record.activity_name == "Tummy Time"
    assert record.desc is not None and record.desc["objectID"] == TUMMY_TIME_ID
    assert (record.duration, record.note) == (15, "On the play mat")


async def test_log_activity_at_a_given_time(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer
) -> None:
    _activity(server, 1, 30, BATH)
    await tick(hass)

    await _log(hass, "Bath", time="2026-10-26T12:00:00+00:00")

    [(_, _, record)] = posted(server)
    assert record.time is not None
    assert record.time.isoformat() == "2026-10-26T12:00:00+00:00"
    assert _state(hass, LAST_BATH).state == "2026-10-26T12:00:00+00:00"


async def test_log_activity_refuses_an_unknown_type(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer
) -> None:
    with pytest.raises(ServiceValidationError) as raised:
        await _log(hass, "Swimming")

    assert raised.value.translation_key == "unknown_activity"
    assert raised.value.translation_placeholders == {"activity": "Swimming"}
    assert posted(server) == []


async def test_log_activity_names_the_ids_when_two_types_share_a_name(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer
) -> None:
    other = "A0000000-0000-4000-8000-0000000000BB"
    _activity(server, 1, 30, BATH)
    _activity(server, 2, 40, {**BATH, "objectID": other})
    await tick(hass)

    with pytest.raises(ServiceValidationError) as raised:
        await _log(hass, "Bath")

    assert raised.value.translation_key == "ambiguous_selection"
    placeholders = raised.value.translation_placeholders or {}
    assert set(placeholders["ids"].split(", ")) == {BATH_ID, other}


@pytest.mark.parametrize("minutes", [-1, 1.5, 1441, "ten", True])
async def test_log_activity_refuses_bad_minutes(
    hass: HomeAssistant, init_integration: MockConfigEntry, server: FakeServer, minutes: Any
) -> None:
    with pytest.raises(probatio.Invalid):
        await _log(hass, "Tummy Time", minutes=minutes)
    assert posted(server) == []
