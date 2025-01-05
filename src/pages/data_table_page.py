import json

from nicegui import ui

from ..utils.path import TASK_PATH

columns = [
    {
        "name": "_id",
        "label": "编号",
        "field": "_id",
        "required": True,
        "align": "center",
    },
    {
        "name": "col1",
        "label": "文件夹名称",
        "field": "col1",
        "required": True,
        "align": "center",
    },
    {
        "name": "col2",
        "label": "番剧名称",
        "field": "col2",
        "required": True,
        "align": "center",
    },
    {
        "name": "col3",
        "label": "季度",
        "field": "col3",
        "required": True,
        "align": "center",
    },
]


@ui.refreshable
def create_table():
    rows = []
    for i in list(TASK_PATH.iterdir()):
        with open(i, 'r', encoding='utf-8') as f:
            task_data = json.load(f)

        rows.append(
            {
                "_id": task_data['uuid'],
                "col1": task_data['path'],
                "col2": task_data['name'],
                "col3": task_data['season_id'],
                "id": task_data['uuid'],
            }
        )

    table = (
        ui.table(columns=columns, rows=rows)
        .classes('w-full h-full')
        .style('max-height: 85%')
    )
    table.add_slot(
        'header',
        r'''
        <q-tr :props="props">
            <q-th auto-width />
            <q-th v-for="col in props.cols" :key="col.name" :props="props">
                {{ col.label }}
            </q-th>
        </q-tr>
    ''',
    )
    table.add_slot(
        'body',
        r'''
        <q-tr :props="props">
            <q-td auto-width>
                <q-btn size="sm" color="accent" round dense
                    @click="props.expand = !props.expand"
                    :icon="props.expand ? 'remove' : 'add'" />
            </q-td>
            <q-td v-for="col in props.cols" :key="col.name" :props="props">
                {{ col.value }}
            </q-td>
        </q-tr>
        <q-tr v-show="props.expand" :props="props">
            <q-td colspan="100%">
                <div class="text-left">This is {{ props.row.name }}.</div>
            </q-td>
        </q-tr>
    ''',
    )
