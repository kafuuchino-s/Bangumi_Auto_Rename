from typing import Any, Dict, List, Union, Optional

from nicegui import ui
from nicegui.events import (
    Handler,
    ClickEventArguments,
    ValueChangeEventArguments,
)

from .color import MAINC


class RedDropDownButton(ui.dropdown_button):
    def __init__(
        self,
        text: str = '',
        *,
        value: bool = False,
        on_value_change: Optional[Handler[ValueChangeEventArguments]] = None,
        on_click: Optional[Handler[ClickEventArguments]] = None,
        icon: Optional[str] = None,
        auto_close: Optional[bool] = False,
        split: Optional[bool] = False,
    ):
        super().__init__(
            text,
            value=value,
            on_value_change=on_value_change,
            on_click=on_click,
            color=MAINC,
            icon=icon,
            auto_close=auto_close,
            split=split,
        )
        self._props['rounded'] = True


class RedButton(ui.button):
    def __init__(self, text: str, on_click=None, icon=None):
        super().__init__(text, on_click=on_click, icon=icon, color=MAINC)
        self._props['rounded'] = True


class RedToogle(ui.toggle):
    def __init__(
        self,
        options: Union[List, Dict],
        *,
        value: Any = None,
        on_change: Optional[Handler[ValueChangeEventArguments]] = None,
        clearable: bool = False,
    ):
        super().__init__(
            options,
            value=value,
            on_change=on_change,
            clearable=clearable,
        )
        self._props['toggle-color'] = MAINC
