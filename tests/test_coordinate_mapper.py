import pytest

from IT2026.IT2026.coordinate_mapper import CoordinateMapper


def make_mapper():
    mapper = CoordinateMapper.__new__(CoordinateMapper)
    mapper.virtual_screen_x = -1920
    mapper.virtual_screen_y = 0
    mapper.virtual_screen_width = 3840
    mapper.virtual_screen_height = 1080
    return mapper


def test_normalize_coordinate_clamps_out_of_bounds_canvas_points():
    mapper = make_mapper()

    assert mapper.normalize_coordinate(-50, 1200, 1000, 1000) == (0.0, 1.0)


def test_normalize_coordinate_rejects_empty_canvas_dimensions():
    mapper = make_mapper()

    with pytest.raises(ValueError, match="canvas_width and canvas_height must be positive"):
        mapper.normalize_coordinate(10, 10, 0, 100)


@pytest.mark.parametrize(
    "normalized_x, normalized_y, expected",
    [
        (0.0, 0.0, (-1920, 0)),
        (0.5, 0.5, (0, 540)),
        (1.0, 1.0, (1919, 1079)),
        (1.5, -0.5, (1919, 0)),
    ],
)
def test_denormalize_coordinate_maps_and_clamps_virtual_desktop(normalized_x, normalized_y, expected):
    mapper = make_mapper()

    assert mapper.denormalize_coordinate(normalized_x, normalized_y) == expected
