import json
from typing import Optional

from .path import TASK_PATH, RECORD_PATH


def unpack_style(style_dict: dict):
    return '; '.join([f'{k}: {v}' for k, v in style_dict.items()])


def get_record(uuid: str) -> Optional[dict]:
    path = RECORD_PATH / f'{uuid}.json'
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return None


def get_task(uuid: str) -> Optional[dict]:
    path = TASK_PATH / f'{uuid}.json'
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return None


def write_task(uuid: str, data: dict) -> None:
    path = TASK_PATH / f'{uuid}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


no_scroll_bar = '''
<style>
body {
    -ms-overflow-style: none;  /* Internet Explorer 10+ */
    scrollbar-width: none;  /* Firefox */
}
body::-webkit-scrollbar {
    display: none;  /* Safari and Chrome */
}
html, body {
    max-width: 100%;
    max-height: 100%;
    overflow-x: hidden;
    overflow-y: hidden;
}
</style>
'''
