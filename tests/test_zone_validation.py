from app.zone_validation import validate_polygon

SQUARE = [(51.0, -0.1), (51.0, 0.1), (51.2, 0.1), (51.2, -0.1)]
TRIANGLE = [(51.0, -0.1), (51.2, -0.1), (51.1, 0.1)]
# A concave ("L-shaped", 6-vertex, non-self-intersecting) polygon -- a
# valid real-world zone shape that a naive intersection check could
# wrongly flag if it didn't correctly skip adjacent-edge pairs.
L_SHAPE = [
    (51.0, -0.2), (51.0, 0.0), (51.1, 0.0), (51.1, -0.1), (51.2, -0.1), (51.2, -0.2),
]
# A "bowtie": edges 0-1 and 2-3 cross in the middle.
BOWTIE = [(51.0, -0.1), (51.2, 0.1), (51.0, 0.1), (51.2, -0.1)]


def test_valid_square_has_no_errors():
    assert validate_polygon(SQUARE) == []


def test_valid_triangle_has_no_errors():
    assert validate_polygon(TRIANGLE) == []


def test_valid_concave_polygon_has_no_errors():
    assert validate_polygon(L_SHAPE) == []


def test_fewer_than_three_vertices_is_rejected():
    errors = validate_polygon([(51.0, -0.1), (51.2, 0.1)])
    assert len(errors) == 1
    assert "at least 3 vertices" in errors[0]


def test_empty_polygon_is_rejected():
    errors = validate_polygon([])
    assert len(errors) == 1
    assert "at least 3 vertices" in errors[0]


def test_out_of_range_latitude_is_rejected():
    errors = validate_polygon([(91.0, -0.1), (51.2, 0.1), (51.0, 0.2)])
    assert any("latitude" in e for e in errors)


def test_out_of_range_longitude_is_rejected():
    errors = validate_polygon([(51.0, -190.0), (51.2, 0.1), (51.0, 0.2)])
    assert any("longitude" in e for e in errors)


def test_duplicate_consecutive_vertex_is_rejected():
    errors = validate_polygon([(51.0, -0.1), (51.0, -0.1), (51.2, 0.1)])
    assert any("duplicate consecutive vertex" in e for e in errors)


def test_self_intersecting_bowtie_is_rejected():
    errors = validate_polygon(BOWTIE)
    assert any("self-intersecting" in e for e in errors)


def test_never_raises_on_malformed_input():
    # A validator's whole job is handling bad input without crashing --
    # this must report problems, never throw.
    assert validate_polygon([(0.0, 0.0)]) == ["needs at least 3 vertices, got 1"]
