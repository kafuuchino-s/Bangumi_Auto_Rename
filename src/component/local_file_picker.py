import platform
from pathlib import Path
from typing import Optional

from nicegui import ui, events

from ..config.config_manager import cm
from ..element.red import RedButton, RedToogle


class local_file_picker(ui.dialog):

    def __init__(
        self,
        directory: str,
        *,
        upper_limit: Optional[str] = None,
        multiple: bool = False,
        show_hidden_files: bool = False,
    ) -> None:
        """这是一个简单的文件选择器，允许您从运行 NiceGUI 的本地文件系统中选择文件。

        参数：
            directory: 起始目录。
            upper_limit: 限制的最高目录（None 表示无限制，默认值为与起始目录相同）。
            multiple: 是否允许选择多个文件。
            show_hidden_files: 是否显示隐藏文件。
        """
        super().__init__()

        self.path: Path
        self.upper_limit: Path | None
        self.show_hidden_files: bool
        self.drives_toggle: RedToogle | None = None
        self.grid: ui.aggrid

        self.path = Path(directory).expanduser()
        if upper_limit is None:
            self.upper_limit = None
        else:
            self.upper_limit = Path(
                directory if upper_limit == ... else upper_limit
            ).expanduser()
        self.show_hidden_files = show_hidden_files

        _s = 'flex-wrap: nowrap; min-width: 400px; max-width: 730px'
        self.classes('flex w-full').style(_s)
        with (
            self,
            ui.card().classes('flex w-full').style(_s),
        ):
            with ui.column(align_items='center') as c1:
                c1.classes('flex w-full')
                c1.style('flex-wrap: nowrap; flex-shrink: 0; flex-basis: auto')
                with ui.row().classes('w-full nowrap') as r1:
                    r1.style('flex-wrap: nowrap')
                    self.add_drives_toggle()
                    rs = 'multiple' if multiple else 'single'
                    self.grid = (
                        ui.aggrid(
                            {
                                'columnDefs': [
                                    {
                                        'field': 'name',
                                        'headerName': 'File',
                                    }
                                ],
                                'rowSelection': rs,
                            },
                            html_columns=[0],
                        )
                        .classes('w-1/2')
                        .style(_s)
                        .on('cellDoubleClicked', self.handle_double_click)
                    )
                with ui.row().classes('w-full justify-end'):
                    RedButton('取消', on_click=self.close).props('outline')
                    RedButton('确定', on_click=self._handle_ok)
        ui.timer(0, self.update_grid, once=True)

    def add_drives_toggle(self) -> None:
        drives_toggle: RedToogle | None = None
        if platform.system() == 'Windows':
            import win32api

            drives = win32api.GetLogicalDriveStrings().split('\000')[:-1]

            # drives = [str(i) for i in Path(drives[0]).iterdir()]
            drives_toggle = RedToogle(
                drives, value=drives[0], on_change=self.update_drive
            )
        elif platform.system() == 'Darwin':  # macOS
            import os

            # On macOS, we can list mount points in '/Volumes'
            drives = [
                drive
                for drive in os.listdir('/Volumes')
                if os.path.isdir(os.path.join('/Volumes', drive))
            ]
            drives_toggle = RedToogle(
                drives, value=drives[0], on_change=self.update_drive
            )

        elif platform.system() == 'Linux':
            import os

            docker_path = cm.get_config('docker_mnt')
            drives = [
                drive
                for drive in os.listdir(docker_path)
                if os.path.isdir(os.path.join(docker_path, drive))
            ]
            # drives = [f'{i}' for i in drives]
            drives_toggle = RedToogle(drives, value=drives[0])
            drives_toggle.on_value_change(
                lambda e: self.update_drive(docker_path),
            )
        if drives_toggle is None:
            return
        self.drives_toggle = drives_toggle
        self.drives_toggle.classes(add="column", remove="row inline")
        # self.drives_toggle.classes('w-1/2')

    def update_drive(self, main: Optional[str] = None) -> None:
        if self.drives_toggle is None:
            return
        if main is None:
            path_str = str(self.drives_toggle.value)
        else:
            path_str = f'{main}/{self.drives_toggle.value}'
        self.path = Path(path_str).expanduser()
        ui.timer(0, self.update_grid, once=True)

    def update_grid(self) -> None:
        paths = list(self.path.glob('*'))
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if not self.show_hidden_files:
            paths = [p for p in paths if not p.name.startswith('.')]
        paths.sort(key=lambda p: p.name.lower())
        paths.sort(key=lambda p: not p.is_dir())
        self.grid.options['rowData'] = [
            {
                'name': f'📁 {p.name}' if p.is_dir() else p.name,
                'path': str(p),
            }
            for p in paths
        ]
        if (self.upper_limit is None and self.path != self.path.parent) or (
            self.upper_limit is not None and self.path != self.upper_limit
        ):
            self.grid.options['rowData'].insert(
                0,
                {
                    'name': '📁 <strong>..</strong>',
                    'path': str(self.path.parent),
                },
            )
        self.grid.update()

    def handle_double_click(self, e: events.GenericEventArguments) -> None:
        self.path = Path(e.args['data']['path'])
        if self.path.is_dir():
            self.update_grid()
        else:
            self.submit([str(self.path)])

    async def _handle_ok(self):
        rows = await self.grid.get_selected_rows()
        if not rows:
            self.submit([str(self.path)])
        else:
            self.submit([r['path'] for r in rows])
