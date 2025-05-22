import logging

from homeassistant.const import UnitOfTemperature
from homeassistant.util.unit_conversion import TemperatureConverter


def simple_celsius_to_fahrenheit(temp: float) -> float:
    return temp * 9 / 5 + 32


def convert_temp(hass, node_units: str, temp: float) -> float:
    # Temperatures are converted to the units of the HA
    # instance, so do the same for comparison
    unit = (
        UnitOfTemperature.CELSIUS if node_units == "C" else UnitOfTemperature.FAHRENHEIT
    )
    return TemperatureConverter.convert(temp, unit, hass.config.units.temperature_unit)


def round_temp(hass, temp: float) -> float:
    # HA uses different precisions for Fahrenheit (whole
    # integers) vs Celsius (tenths)
    if hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS:
        return round(temp, 1)
    return round(temp)


def assert_log_message(
    caplog, name: str, levelno: int, message: str, phase="call"
) -> None:
    def _find_message(r: logging.LogRecord) -> bool:
        return r.name == name and r.levelno == levelno and r.message == message

    assert any(
        # Ignoring typing due to https://github.com/python/mypy/issues/12682
        filter(_find_message, caplog.get_records(phase))
    )


def assert_no_log_errors(caplog, phase="call") -> None:
    errors = [
        record
        for record in caplog.get_records("call")
        if record.levelno >= logging.ERROR
    ]
    assert not errors
