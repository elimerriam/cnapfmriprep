import pytest

from cnapfmriprep.errors import ValidationError
from cnapfmriprep.topup import phase_encoding_vector


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("i", (1, 0, 0)), ("i-", (-1, 0, 0)), ("j", (0, 1, 0)), ("j-", (0, -1, 0)), ("k", (0, 0, 1)), ("k-", (0, 0, -1))],
)
def test_phase_encoding_vector(direction, expected) -> None:
    assert phase_encoding_vector(direction) == expected


def test_invalid_direction() -> None:
    with pytest.raises(ValidationError):
        phase_encoding_vector("AP")
