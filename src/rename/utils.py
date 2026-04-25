IGNORE_DIR = ['cd', 'scan']
IGNORE_SUFFIX = ['.rar', '.zip', '.7z', '.webp', '.jpg', '.png']
# 宣传内容标签（Season 0 处理时跳过，不在 TMDB 中）
PROMO_TAGS = [
    'NCOP',           # 无字幕片头
    'NCED',           # 无字幕片尾
    'Creditless',     # 无字幕版 OP/ED（如 Creditless ED, Creditless OP）
    'Non Telop',      # 无字幕版（日语说法，同 Creditless）
    'PV',             # 宣传视频
    'CM',             # 电视广告
    'Menu',           # BD/DVD 菜单
    'Trailer',        # 预告片
    'Preview',        # 预览
    'Digest',         # 摘要/总集篇
    'Interview',      # 访谈
    'Cast Talk',      # 声优访谈
    'Making',         # 花絮
    'MV',             # 音乐视频
    'Teaser',         # 先导预告
    'Logo',           # 标志动画
    'Spot',           # 电视广告
    'Web Preview',    # 网络预告
    'Creditless OP',  # 无字幕 OP
    'Creditless ED',  # 无字幕 ED
    'Non-Credit OP',  # 无字幕 OP
    'Non-Credit ED',  # 无字幕 ED
    'ノンクレジットOP',  # 日文：无字幕 OP
    'ノンクレジットED',  # 日文：无字幕 ED
    'ノンテロップOP',  # 日文：无字幕 OP
    'ノンテロップED',  # 日文：无字幕 ED
    'ノクレジットOP',  # 常见错写/字幕组写法
    'ノクレジットED',  # 常见错写/字幕组写法
    'メニュー',         # 日文：菜单
    '予告',             # 预告
    '番宣',             # 宣传
    '劇場マナーCM',     # 剧场礼仪广告
]
# 特典文件夹名称（用于收集 Season 0 文件）
SPECIAL_FOLDER_NAMES = [
    'sps', 'sp', 'extras', 'extra', 'bonus', 'oad', 'ova',
    '特典', '映像特典', 'specials', 'bd menu', 'pv & cm',
]
VIDEO_SUFFIX = [
    '.mp4',
    # '.mka',   # 一般来说无需单独分析外挂音轨
    '.mkv',
    '.avi',
    '.wmv',
    '.flv',
    '.mov',
    '.mpg',
    '.mpeg',
    '.m4v',
    '.rm',
    '.rmvb',
]
