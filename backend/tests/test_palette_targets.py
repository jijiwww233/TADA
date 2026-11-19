from app.palette import Target, score, targets
from app.recommendation import parse_brief


def test_decomposed_two_colors_both_contribute_without_all_color_gate():
    # The original brief's old parser knows only 米白; the decomposed sentence knows 黑色.
    spec = parse_brief('米白黑海报')
    assert spec.colors == ('米白',)
    desired = targets('采用米白与黑色作为主色调。', spec.colors)
    assert [t.hex for t in desired] == ['#f5f0e6', '#000000']
    both = [{'hex': '#f5f0e6', 'ratio': .6}, {'hex': '#000000', 'ratio': .3}]
    only_beige = [{'hex': '#f5f0e6', 'ratio': .6}, {'hex': '#0000ff', 'ratio': .3}]
    assert score(both, desired) > score(only_beige, desired) > 0


def test_nearest_color_is_selected_before_share_and_main_penalizes_small_share():
    desired = (Target('#000000'),)
    swatches = [{'hex':'#000000','ratio':.05}, {'hex':'#eeeeee','ratio':.9}]
    assert abs(score(swatches, desired) - .05) < 1e-8
    assert score(swatches, (Target('#000000', True),)) < score(swatches, desired)
    assert score([{'hex':'#000000','ratio':.8}], (Target('#000000', True),)) > .6


def test_structured_hex_roles_dedupe_fallback_and_invalid_colors():
    parsed = targets([{'hex':'#F5F0E6','role':'accent'}, {'hex':'#000000','role':'main'},
                      {'hex':'garbage'}, {'hex':'#000000','role':'accent'}])
    assert parsed == (Target('#f5f0e6'), Target('#000000', True))
    assert targets('', ('米白',)) == (Target('#f5f0e6'),)
    assert score([{'hex':'bad','ratio':1}, {'hex':'#ffffff','ratio':float('nan')}], parsed) == 0
