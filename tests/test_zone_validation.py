from app.zone_validation import _segments_intersect, validate_polygon

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


# _segments_intersect's collinear-touching branches (the algorithm's
# textbook edge cases beyond a plain transversal crossing) are exercised
# directly -- no ordinary polygon naturally produces these degenerate
# collinear overlaps, so the public validate_polygon() surface alone can't
# reach them.
def test_segments_intersect_when_one_endpoint_touches_the_other_segment():
    # seg2's own start point (5, 0) lies inside seg1's range [0, 10].
    assert _segments_intersect(((0.0, 0.0), (10.0, 0.0)), ((5.0, 0.0), (15.0, 0.0))) is True


def test_segments_intersect_when_the_other_endpoint_touches_the_first_segment():
    # seg2's end point (5, 0) lies inside seg1's range [0, 10], with its
    # start point (15, 0) outside it -- the mirror of the case above.
    assert _segments_intersect(((0.0, 0.0), (10.0, 0.0)), ((15.0, 0.0), (5.0, 0.0))) is True


def test_segments_intersect_when_one_segment_fully_contains_the_other():
    # seg2 spans past both ends of seg1 on the same line -- neither of
    # seg2's own endpoints lies inside seg1's range, but seg1's start
    # point does lie inside seg2's.
    assert _segments_intersect(((0.0, 0.0), (10.0, 0.0)), ((-5.0, 0.0), (15.0, 0.0))) is True


def test_collinear_non_overlapping_segments_do_not_intersect():
    assert _segments_intersect(((0.0, 0.0), (10.0, 0.0)), ((20.0, 0.0), (30.0, 0.0))) is False
