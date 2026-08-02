from core.config import normalize_ui_layout


def test_normalize_ui_layout_clamps_and_filters():
    result = normalize_ui_layout({
        'sidenav_width': 9999,
        'inspector_width': '320',
        'logs_height': 50,
        'logs_hidden': True,
        'table_columns': {
            'models-library': {
                'model': 420,
                'family': 'bad',
                'scale': 0,
            },
        },
    })
    assert result['sidenav_width'] == 480
    assert result['inspector_width'] == 320
    assert result['logs_height'] == 80
    assert result['logs_hidden'] is True
    assert result['table_columns']['models-library'] == {'model': 420}


def test_normalize_ui_layout_navigation_state():
    invalid = normalize_ui_layout({
        'active_view': 'invalid',
        'inspector_tab': 'other',
        'settings_panel': 'missing',
    })
    assert 'active_view' not in invalid
    assert 'inspector_tab' not in invalid
    assert 'settings_panel' not in invalid

    result = normalize_ui_layout({
        'active_view': 'catalog',
        'inspector_collapsed': True,
        'inspector_tab': 'load',
        'settings_panel': 'hw-gpus',
    })
    assert result['active_view'] == 'catalog'
    assert result['inspector_collapsed'] is True
    assert result['inspector_tab'] == 'load'
    assert result['settings_panel'] == 'hw-gpus'
