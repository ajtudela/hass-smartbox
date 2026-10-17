"""Tests for the shared entity helpers."""

from homeassistant.exceptions import HomeAssistantError
import pytest
from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError

from custom_components.smartbox.entity import wrap_action_errors


class _FakeEntity:
    entity_id = "switch.test"

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    @wrap_action_errors
    async def act(self, value: int, *, flag: bool = False) -> tuple[int, bool]:
        if self._error is not None:
            raise self._error
        return value, flag


async def test_wrap_action_errors_returns_the_result():
    """A successful action passes its arguments and result through."""
    assert await _FakeEntity().act(3, flag=True) == (3, True)


@pytest.mark.parametrize(
    "error",
    [SmartboxError("boom"), InvalidAuthError("denied"), APIUnavailableError("down")],
)
async def test_wrap_action_errors_converts_library_errors(error):
    """Library errors surface as HomeAssistantError naming the entity."""
    with pytest.raises(HomeAssistantError, match=r"switch.test") as excinfo:
        await _FakeEntity(error).act(1)
    assert excinfo.value.__cause__ is error


async def test_wrap_action_errors_keeps_other_errors():
    """Errors that are not from the library are not hidden."""
    with pytest.raises(KeyError):
        await _FakeEntity(KeyError("missing")).act(1)
