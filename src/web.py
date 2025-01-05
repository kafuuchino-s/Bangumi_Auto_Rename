from nicegui import ui

from .main_page import main_page
from .utils.utils import no_scroll_bar


@ui.page('/')
def main():
    ui.add_head_html(no_scroll_bar)
    main_page()
