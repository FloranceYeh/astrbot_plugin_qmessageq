"""内置 QQ 表情数据与解析工具。

表情编号以 NapCat（NTQQ sysface）的 QSid 为准，来源为 NapCat 自带的
``face_config.json``（见插件目录下的 FACES.md 大全）。
"""

import re
import unicodedata


_FACE_ID_BY_NAME: dict[str, int] = {
    "龙年快乐": 392, "新年中龙": 393, "超级赞": 364, "芒狗": 366, "好兄弟": 362, "抛媚眼": 397, "狼狗": 396,
    "亲亲": 360, "狗狗笑哭": 361, "狗狗可怜": 363, "狗狗生气": 365, "狗狗疑问": 367, "tui": 399, "超级ok": 398,
    "忙": 373, "祝贺": 370, "超级鼓掌": 375, "奥特笑哭": 368, "彩虹": 369, "冒泡": 371, "气呼呼": 372,
    "波波流泪": 374, "emo": 382, "企鹅爱心": 383, "超级转圈": 401, "快乐": 400, "真棒": 380, "路过": 381,
    "企鹅流泪": 379, "跺脚": 376, "企鹅笑哭": 378, "嗨": 377, "出去玩": 403, "别说话": 402, "太头秃": 390,
    "太沧桑": 391, "太头疼": 388, "太赞了": 389, "呜呜呜": 386, "太气了": 385, "晚安": 384, "太好笑": 387,
    "摇起来": 413, "好运来": 405, "闪亮登场": 404, "姐是女王": 406, "么么哒": 410, "一起嗨": 411, "我听听": 407,
    "臭美": 408, "开心": 412, "送你花花": 409, "新年大龙": 394, "微笑": 14, "撇嘴": 1, "色": 2,
    "发呆": 3, "得意": 4, "害羞": 6, "闭嘴": 7, "睡": 8, "大哭": 9, "流泪": 5,
    "尴尬": 10, "发怒": 11, "调皮": 12, "呲牙": 13, "惊讶": 0, "难过": 15, "酷": 16,
    "冷汗": 96, "抓狂": 18, "吐": 19, "偷笑": 20, "可爱": 21, "白眼": 22, "傲慢": 23,
    "饥饿": 24, "困": 25, "惊恐": 26, "流汗": 27, "憨笑": 28, "悠闲": 29, "奋斗": 30,
    "咒骂": 31, "疑问": 32, "嘘": 33, "晕": 34, "折磨": 35, "衰": 36, "骷髅": 37,
    "敲打": 38, "再见": 39, "擦汗": 97, "抠鼻": 98, "鼓掌": 99, "糗大了": 100, "坏笑": 101,
    "左哼哼": 102, "右哼哼": 103, "哈欠": 104, "鄙视": 105, "委屈": 106, "快哭了": 107, "阴险": 108,
    "右亲亲": 305, "左亲亲": 109, "吓": 110, "可怜": 111, "眨眼睛": 172, "笑哭": 182, "doge": 179,
    "泪奔": 173, "无奈": 174, "托腮": 212, "卖萌": 175, "斜眼笑": 178, "喷血": 177, "小纠结": 176,
    "我最美": 183, "脑阔疼": 262, "沧桑": 263, "捂脸": 264, "辣眼睛": 265, "哦哟": 266, "头秃": 267,
    "问号脸": 268, "暗中观察": 269, "emm": 270, "吃瓜": 271, "呵呵哒": 272, "汪汪": 277, "喵喵": 307,
    "牛气冲天": 306, "无眼笑": 281, "敬礼": 282, "狂笑": 283, "面无表情": 284, "摸鱼": 285, "摸锦鲤": 293,
    "魔鬼笑": 286, "哦": 287, "睁眼": 289, "期待": 294, "拜谢": 297, "元宝": 298, "牛啊": 299,
    "胖三斤": 300, "嫌弃": 323, "举牌牌": 332, "豹富": 336, "拜托": 353, "耶": 355, "666": 356,
    "尊嘟假嘟": 354, "咦": 352, "裂开": 357, "虎虎生威": 334, "大展宏兔": 347, "右拜年": 303, "左拜年": 302,
    "拿到红包": 295, "打call": 311, "变形": 312, "仔细分析": 314, "菜汪": 317, "崇拜": 318, "比心": 319,
    "庆祝": 320, "吃糖": 324, "惊吓": 325, "花朵脸": 337, "我想开了": 338, "舔屏": 339, "打招呼": 341,
    "酸Q": 342, "我方了": 343, "大怨种": 344, "红包多多": 345, "你真棒棒": 346, "戳一戳": 181, "太阳": 74,
    "月亮": 75, "敲敲": 351, "坚强": 349, "贴贴": 350, "略略略": 395, "篮球": 114, "骰子": 358,
    "包剪锤": 359, "生气": 326, "蛋糕": 53, "拥抱": 49, "爱心": 66, "玫瑰": 63, "凋谢": 64,
    "幽灵": 187, "爆筋": 146, "示爱": 116, "心碎": 67, "咖啡": 60, "羊驼": 185, "鞭炮": 137,
    "烟花": 333, "赞": 76, "OK": 124, "抱拳": 118, "握手": 78, "勾引": 119, "胜利": 79,
    "拳头": 120, "差劲": 121, "踩": 77, "NO": 123, "点赞": 201, "我酸了": 273, "猪头": 46,
    "菜刀": 112, "刀": 56, "手枪": 169, "茶": 171, "便便": 59, "喝彩": 144, "棒棒糖": 147,
    "西瓜": 89, "喝奶": 148, "炸弹": 55, "发抖": 41, "转圈": 125, "爱情": 42, "跳跳": 43,
    "怄火": 86, "挥手": 129, "拍桌": 226, "飞吻": 85, "糊脸": 215, "啵啵": 214, "抱抱": 222,
    "托脸": 203, "颤抖": 235, "生日快乐": 241, "偷看": 237, "舔一舔": 218, "掐一掐": 233, "佛系": 232,
    "扇脸": 238, "扯一扯": 217, "撩一撩": 225, "嘲讽": 230, "不开心": 194, "飙泪": 210, "大笑": 193,
    "吃": 204, "求求": 200, "敲开心": 290, "开枪": 224, "干杯": 229, "顶呱呱": 221, "蹭一蹭": 219,
    "拍手": 227, "拍头": 216, "哼": 231, "扔狗": 244, "暴击": 223, "甩头": 243, "我不看": 211,
    "让我康康": 292, "喷脸": 240, "惊喜": 180, "爱你": 122, "无聊": 202, "汗": 278, "好闪": 301,
    "请": 288, "拒绝": 322, "呃": 198, "福萝卜": 348, "害怕": 206, "原谅": 239, "续标识": 424,
    "划龙舟": 415, "中龙舟": 416, "大龙舟": 417, "求放过": 425, "偷感": 427, "玩火": 426, "火车": 419,
    "中火车": 420, "大火车": 421, "蛇年快乐": 429, "蛇身": 430, "蛇尾": 431, "收到": 428, "粽于等到你": 422,
    "复兴号": 423, "灵蛇献瑞": 432,
}

_HIDDEN_FACE_NAMES: frozenset = frozenset(
    "新年中龙 超级赞 芒狗 好兄弟 抛媚眼 狼狗 亲亲 狗狗笑哭 狗狗可怜 狗狗生气 狗狗疑问 tui 超级ok "
    "忙 祝贺 超级鼓掌 奥特笑哭 彩虹 冒泡 气呼呼 波波流泪 emo 企鹅爱心 超级转圈 快乐 真棒 路过 "
    "企鹅流泪 跺脚 企鹅笑哭 嗨 出去玩 别说话 太头秃 太沧桑 太头疼 太赞了 呜呜呜 太气了 晚安 太好笑 "
    "摇起来 好运来 闪亮登场 姐是女王 么么哒 一起嗨 我听听 臭美 开心 送你花花 新年大龙 骰子 包剪锤 "
    "喝奶 炸弹 拍桌 糊脸 啵啵 抱抱 托脸 颤抖 生日快乐 偷看 舔一舔 掐一掐 佛系 扇脸 扯一扯 撩一撩 "
    "嘲讽 不开心 飙泪 大笑 吃 求求 敲开心 开枪 干杯 顶呱呱 蹭一蹭 拍手 拍头 哼 扔狗 暴击 甩头 "
    "我不看 让我康康 喷脸 惊喜 爱你 无聊 汗 好闪 请 拒绝 呃 福萝卜 害怕 原谅 中龙舟 大龙舟 "
    "中火车 大火车 蛇身 蛇尾 粽于等到你 复兴号 灵蛇献瑞".split()
)

_FACE_ALIASES: dict[str, int] = {
    "smile": 14, "happy": 14, "sad": 15, "cry": 9, "angry": 11, "love": 66,
    "heart": 66, "broken_heart": 67, "ok": 124, "no": 123, "strong": 76,
    "weak": 77, "clap": 99, "wave": 129, "sleep": 8, "kiss": 109, "rose": 63,
    "laugh": 182, "cool": 16, "shocked": 26, "thumbsup": 76, "dice": 358,
}

_VS16 = "\ufe0f"
_ZWJ_CHAR = "\u200d"
_SKIN_TONE_CHARS = frozenset(
    "\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff"
)


def normalize_emoji_token(token: str) -> str:
    """Strip variation selectors, ZWJ and skin-tone modifiers from an emoji.

    Args:
        token: A raw emoji string, e.g. ``"❤️"``, ``"👍🏿"`` or ``"❤🔥"``.

    Returns:
        The normalized emoji, e.g. ``"❤"``, ``"👍"`` or ``"❤🔥"``.
    """
    token = token.replace(_VS16, "").replace(_ZWJ_CHAR, "")
    return "".join(ch for ch in token if ch not in _SKIN_TONE_CHARS)


def resolve_face_id(token: str) -> int | None:
    """Resolve a face token to a QQ face id, or ``None`` when unknown.

    Args:
        token: A face name (``微笑``), an English alias (``smile``), a raw
            numeric id (``14``) or a ``#``-prefixed numeric id (``#14``).

    Returns:
        The QQ face id as an ``int``, or ``None`` when nothing matches.
    """
    token = normalize_emoji_token(token.strip())
    if token.startswith("#"):
        token = token[1:]
    face_id = _FACE_ID_BY_NAME.get(token)
    if face_id is None and token.isdigit():
        face_id = int(token)
    if face_id is None:
        face_id = _FACE_ALIASES.get(token.lower())
    return face_id


def is_emoji_text(token: str) -> bool:
    """Whether the token is a literal emoji (not a face name or number).

    Args:
        token: The raw token, e.g. ``"💢"`` or ``"❤️"``.

    Returns:
        ``True`` when the normalized token consists only of symbol characters.
    """
    token = normalize_emoji_token(token.strip())
    return bool(token) and all(
        unicodedata.category(ch).startswith("S") for ch in token
    )


def emoji_codepoint(token: str) -> int | None:
    """The unicode codepoint of the first emoji grapheme, else ``None``.

    NapCat's ``set_msg_emoji_like`` reacts with a literal unicode emoji when
    the ``emoji_id`` is its decimal codepoint (e.g. ``💢`` -> ``128162``).
    """
    token = normalize_emoji_token(token.strip())
    if not token:
        return None
    return ord(token[0])


_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def resolve_face_ids(token: str) -> list[int] | None:
    """Resolve a face token into one or more face ids.

    Supports a single face (name / English alias / numeric id / ``#``-prefixed
    numeric id) or a numeric range ``a-b`` that expands to every id in between
    (e.g. ``358-362`` yields ``[358, 359, 360, 361, 362]``).

    Args:
        token: The raw face token.

    Returns:
        A list of face ids, or ``None`` when nothing matches.
    """
    match = _RANGE_RE.match(token)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        step = 1 if low <= high else -1
        return list(range(low, high + step, step))
    face_id = resolve_face_id(token)
    return [face_id] if face_id is not None else None


def hidden_faces() -> list[tuple[int, str]]:
    """The hidden faces as ``(id, name)`` pairs sorted by id."""
    return sorted((_FACE_ID_BY_NAME[name], name) for name in _HIDDEN_FACE_NAMES)
