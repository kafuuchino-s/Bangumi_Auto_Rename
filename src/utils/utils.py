def unpack_style(style_dict: dict):
    return '; '.join([f'{k}: {v}' for k, v in style_dict.items()])


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
