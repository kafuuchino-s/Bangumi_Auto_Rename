import json
from collections.abc import Mapping
from typing import cast

from .path import RECORD_PATH, TASK_PATH

JsonDict = dict[str, object]


def unpack_style(style_dict: Mapping[str, object]) -> str:
    return '; '.join([f'{k}: {v}' for k, v in style_dict.items()])


def get_record(uuid: str) -> JsonDict | None:
    path = RECORD_PATH / f'{uuid}.json'
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return cast(JsonDict, data) if isinstance(data, dict) else None
    else:
        return None


def custom_sort(key: str) -> tuple[int, str]:
    if key == 'is_anime':
        return (0, key)
    elif key == 'is_movie':
        return (1, key)
    else:
        return (2, key)


def get_task(uuid: str) -> JsonDict:
    path = TASK_PATH / f'{uuid}.json'
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
            if not isinstance(raw_data, dict):
                return {}
            data = cast(JsonDict, raw_data)
            if 'is_movie' not in data:
                data['is_movie'] = None
            sorted_keys = sorted(data.keys(), key=custom_sort)
            sorted_dict = {key: data[key] for key in sorted_keys}
            return sorted_dict
    else:
        return {}


def write_task(uuid: str, data: Mapping[str, object]) -> None:
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
