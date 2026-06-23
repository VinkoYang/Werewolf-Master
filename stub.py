"""Stubs replacing PyWebIO components. get_actions() returns plain dicts
that server.py serialises to JSON and sends to the browser via Socket.IO."""


def _normalise_buttons(buttons: list) -> list:
    result = []
    for btn in buttons:
        if isinstance(btn, str):
            result.append({'label': btn, 'value': btn})
        else:
            result.append(btn)
    return result


def actions(name: str, buttons: list, help_text: str = '') -> dict:
    return {
        'type': 'actions',
        'name': name,
        'buttons': _normalise_buttons(buttons),
        'help_text': help_text,
    }


def radio(name: str, options: list, label: str = '', value=None, help_text: str = '') -> dict:
    return {
        'type': 'radio',
        'name': name,
        'options': options,
        'label': label,
        'value': value,
        'help_text': help_text,
    }
