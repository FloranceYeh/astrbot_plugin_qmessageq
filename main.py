from __future__ import annotations

import random
import re
from typing import Any

import astrbot.api.message_components as Comp
from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Node
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr


def build_image_summary_message(file: str, summary: str) -> list[dict]:
    """Build a OneBot image message carrying a custom summary text.

    NapCat/LLOneBot render the ``summary`` field of an image segment as the
    image description, so the receiver sees the text instead of a bare
    ``[图片]`` placeholder.

    Args:
        file: Image source, e.g. ``base64://...``, ``http(s)://...`` or a path.
        summary: Text embedded into the image's summary field.

    Returns:
        A OneBot message list containing a single image segment.
    """
    return [{"type": "image", "data": {"file": file, "summary": summary}}]


def resolve_member_qq(members: list, target: str) -> str | None:
    """Resolve a QQ number from group members by exact nickname/card match.

    Args:
        members: Group members exposing ``nickname`` and ``user_id`` attributes.
        target: Exact nickname or group card to look up.

    Returns:
        The matched QQ number as a string, or ``None`` when nothing matches.
    """
    for member in members:
        nickname = getattr(member, "nickname", None)
        if nickname and str(nickname).strip() == target.strip():
            return str(getattr(member, "user_id", ""))
    return None


def find_attached_image(messages: list) -> Image | None:
    """Return an image attached to the message, including one in a reply chain.

    Args:
        messages: The message component list of an event.

    Returns:
        The first ``Image`` found at the top level, falling back to an image
        inside a ``Reply`` chain, or ``None`` when there is none.
    """
    for seg in messages:
        if isinstance(seg, Image):
            return seg
    for seg in messages:
        if isinstance(seg, Comp.Reply):
            for img in seg.chain or []:
                if isinstance(img, Image):
                    return img
    return None


_AVATAR_SUFFIX_RE = re.compile(r"(?:^|\s)(?:头像|ava)=(\S+)$")


def split_avatar_marker(text: str) -> tuple[str, str]:
    """Strip a trailing ``头像=<qq>`` / ``ava=<qq>`` marker from node text.

    Args:
        text: The raw node text, e.g. ``"你好 ava=10001"``.

    Returns:
        A ``(text, qq)`` pair with the marker removed from ``text``. The marker
        value must be a QQ number; NapCat derives a node's avatar from its
        ``user_id`` and cannot render custom avatar URLs.
    """
    match = _AVATAR_SUFFIX_RE.search(text)
    if match:
        return text[: match.start()].strip(), match.group(1)
    return text, ""


_FACE_ID_BY_NAME: dict[str, int] = {
    "惊讶": 0, "撇嘴": 1, "色": 2, "发呆": 3, "得意": 4, "流泪": 5, "害羞": 6,
    "闭嘴": 7, "睡": 8, "大哭": 9, "尴尬": 10, "发怒": 11, "调皮": 12, "呲牙": 13,
    "微笑": 14, "难过": 15, "酷": 16, "抓狂": 17, "吐": 18, "偷笑": 19, "可爱": 20,
    "白眼": 21, "傲慢": 22, "饥饿": 23, "困": 24, "惊恐": 25, "流汗": 26, "憨笑": 27,
    "悠闲": 28, "奋斗": 29, "咒骂": 30, "疑问": 31, "嘘": 32, "晕": 33, "折磨": 34,
    "衰": 35, "骷髅": 36, "敲打": 37, "再见": 38, "擦汗": 39, "抠鼻": 40, "鼓掌": 41,
    "糗大了": 42, "坏笑": 43, "左哼哼": 44, "右哼哼": 45, "哈欠": 46, "鄙视": 47,
    "委屈": 48, "快哭了": 49, "阴险": 50, "亲亲": 51, "吓": 52, "可怜": 53,
    "菜刀": 54, "西瓜": 55, "啤酒": 56, "篮球": 57, "乒乓": 58, "咖啡": 59, "饭": 60,
    "猪头": 61, "玫瑰": 62, "凋谢": 63, "嘴唇": 64, "爱心": 65, "心碎": 66, "蛋糕": 67,
    "闪电": 68, "炸弹": 69, "刀": 70, "足球": 71, "瓢虫": 72, "便便": 73, "月亮": 74,
    "太阳": 75, "礼物": 76, "拥抱": 77, "强": 78, "弱": 79, "握手": 80, "胜利": 81,
    "抱拳": 82, "勾引": 83, "拳头": 84, "差劲": 85, "爱你": 86, "NO": 87, "OK": 88,
    "爱情": 89, "飞吻": 90, "跳跳": 91, "发抖": 92, "怄火": 93, "转圈": 94, "磕头": 95,
    "回头": 96, "跳绳": 97, "挥手": 98, "激动": 99, "街舞": 100, "献吻": 101,
    "左太极": 102, "右太极": 103,
}

_FACE_ALIASES: dict[str, int] = {
    "smile": 14, "happy": 14, "sad": 15, "cry": 9, "angry": 11, "love": 65,
    "heart": 65, "broken_heart": 66, "ok": 88, "no": 87, "strong": 78,
    "weak": 79, "clap": 41, "wave": 98, "sleep": 8, "kiss": 51, "rose": 62,
}

_FACE_EMOJI_MAP: dict[str, int] = {
    "😀": 14, "😃": 14, "😄": 14, "😁": 14, "😆": 14, "😊": 14, "🙂": 14,
    "☺": 14, "😉": 14, "😜": 43, "😝": 43, "🤪": 43, "😏": 43, "😛": 43,
    "😎": 16, "🤓": 16, "😂": 13, "🤣": 13, "😅": 39, "😓": 26, "😥": 26,
    "😰": 39, "😢": 5, "😭": 9, "🥺": 53, "😔": 15, "🙁": 15, "☹": 15,
    "😞": 15, "😕": 15, "😐": 10, "😑": 10, "😶": 10, "😠": 11, "😡": 11,
    "😤": 30, "😳": 25, "😱": 25, "😨": 25, "🤔": 31, "🧐": 31, "😴": 8,
    "💤": 8, "😋": 53, "🤤": 53, "🥰": 65, "😍": 65, "🤩": 65, "😘": 90,
    "😙": 51, "😚": 51, "😗": 51, "😇": 20, "🤗": 77, "😈": 50, "👿": 11,
    "🤮": 18, "😵": 33, "🤯": 25, "💢": 11, "💦": 26, "💧": 26, "👍": 78,
    "👌": 78, "✅": 78, "🙆": 78, "👎": 79, "🙅": 79, "❌": 79, "👏": 41,
    "🙌": 41, "🙏": 82, "✌": 81, "🤞": 81, "👊": 84, "🤝": 80, "💪": 78,
    "❤": 65, "🧡": 65, "💛": 65, "💚": 65, "💙": 65, "💜": 65, "🖤": 65,
    "🤍": 65, "🤎": 65, "💗": 65, "💓": 65, "💕": 65, "💞": 65, "💖": 65,
    "💝": 65, "💔": 66, "💘": 65, "💋": 64, "👄": 64, "🎉": 41, "🎊": 41,
    "🥳": 41, "🌹": 62, "🌷": 62, "🌻": 62, "🌸": 62, "🌺": 62, "💐": 62,
    "🎂": 67, "🍰": 67, "💣": 69, "💩": 73, "🍉": 55, "🍺": 56, "🏀": 57,
    "🏓": 58, "☕": 59, "🍚": 60, "🐷": 61, "⚡": 68, "🔪": 70, "⚽": 71,
    "🐞": 72, "🌙": 74, "☀": 75, "🎁": 76, "💀": 36, "🔥": 93, "👀": 25,
    "🙈": 50, "🙉": 50, "🙊": 50,
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
        token: A face name (``微笑``), an English alias (``smile``), an emoji
            (``😀``), a raw numeric id (``14``) or a ``#``-prefixed numeric id
            (``#14``).

    Returns:
        The QQ face id as an ``int``, or ``None`` when nothing matches.
    """
    token = normalize_emoji_token(token.strip())
    if token.startswith("#"):
        token = token[1:]
    if token.isdigit():
        return int(token)
    face_id = _FACE_ID_BY_NAME.get(token)
    if face_id is None:
        face_id = _FACE_ALIASES.get(token.lower())
    if face_id is None:
        face_id = _FACE_EMOJI_MAP.get(token)
    if face_id is None and len(token) > 1:
        face_id = _FACE_EMOJI_MAP.get(token[0])
    return face_id


def effective_node_qq(uin: str, avatar_marker: str) -> str | None:
    """The node's effective QQ after an ``头像=/ava=`` override.

    Args:
        uin: The QQ from the node's own field.
        avatar_marker: The raw marker value, empty when absent.

    Returns:
        The QQ to use as the node's ``user_id`` (marker wins), or ``None`` when
        the marker is present but not a pure QQ number.
    """
    if not avatar_marker:
        return uin
    if not avatar_marker.isdigit():
        return None
    return avatar_marker


class QmessageQToolbox(Star):
    """Multi-function QQ message toolbox for aiocqhttp (NapCat/OneBot).

    Provides image summary steganography (``himg``), forged forward messages
    (``fake``), real ``@`` mentions (``at``), contact cards (``card``), quoted
    message reactions (``face``) and an LLM ``at_user`` tool that prepends an
    ``@`` to the bot's reply.
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}

    def _check_permission(self, event: AstrMessageEvent) -> bool:
        """Whether the event sender is allowed to use admin-only commands."""
        return not self.config.get("admin_only", True) or event.is_admin()

    def _check_at_permission(self, event: AstrMessageEvent) -> bool:
        """Whether the event sender is allowed to use the admin-only at command."""
        return not self.config.get("at_admin_only", True) or event.is_admin()

    def _check_face_permission(self, event: AstrMessageEvent) -> bool:
        """Whether the event sender is allowed to use the admin-only face command."""
        return not self.config.get("face_admin_only", True) or event.is_admin()

    async def _send_direct(self, event: AstrMessageEvent, message: list) -> bool:
        """Send a raw OneBot message to the conversation, returning success.

        Args:
            event: The message event; the target is its group or sender.
            message: OneBot message segment list.
        """
        is_group = bool(event.get_group_id())
        target = event.get_group_id() if is_group else event.get_sender_id()
        if not target or not target.isdigit():
            return False
        routing: dict[str, Any] = {}
        self_id = getattr(event.message_obj, "self_id", None)
        if self_id:
            routing["self_id"] = self_id
        try:
            if is_group:
                await event.bot.send_group_msg(
                    group_id=int(target), message=message, **routing
                )
            else:
                await event.bot.send_private_msg(
                    user_id=int(target), message=message, **routing
                )
        except Exception as exc:
            logger.error("direct send failed: %s", exc)
            return False
        return True

    async def _resolve_member_qq(
        self, event: AstrMessageEvent, target: str
    ) -> str | None:
        """Resolve an exact group nickname/card to a QQ number, else ``None``."""
        try:
            group = await event.get_group()
        except Exception as exc:
            logger.warning("failed to fetch the group: %s", exc)
            group = None
        if group is None:
            return None
        return resolve_member_qq(list(group.members or []), target)

    @filter.command("himg")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def himg(self, event: AstrMessageEvent, text: GreedyStr):
        """Hide a text into an image's summary field and resend the image.

        Usage: ``/himg <text>`` with an attached image (including a quoted
        image), or ``/himg <text> <image_url>``.
        """
        if not self._check_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return

        image = find_attached_image(event.get_messages())
        url = ""
        if image is None:
            parts = text.split()
            if parts and re.match(r"^https?://\S+$", parts[-1]):
                url = parts.pop()
                text = " ".join(parts).strip()
        if image is None and not url:
            yield event.plain_result(
                "Missing image: attach an image or append an image URL.",
            )
            return
        if not text.strip():
            yield event.plain_result("Missing hidden text.")
            return

        if image is not None:
            try:
                base64_data = await image.convert_to_base64()
            except Exception as exc:
                logger.warning("himg: failed to convert the attached image: %s", exc)
                yield event.plain_result("Failed to load the attached image.")
                return
            file = f"base64://{base64_data}"
        else:
            file = url

        is_group = bool(event.get_group_id())
        target = event.get_group_id() if is_group else event.get_sender_id()
        if not target or not target.isdigit():
            yield event.plain_result("Failed to resolve the conversation target.")
            return

        routing: dict[str, Any] = {}
        self_id = getattr(event.message_obj, "self_id", None)
        if self_id:
            routing["self_id"] = self_id
        message = build_image_summary_message(file, text.strip())
        try:
            if is_group:
                await event.bot.send_group_msg(
                    group_id=int(target),
                    message=message,
                    **routing,
                )
            else:
                await event.bot.send_private_msg(
                    user_id=int(target),
                    message=message,
                    **routing,
                )
        except Exception as exc:
            logger.error("himg: failed to send the hidden image message: %s", exc)
            yield event.plain_result("Failed to send the hidden image message.")
            return
        event.should_call_llm(True)

    @filter.command("fake")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def fake(
        self, event: AstrMessageEvent, name: str, uin: str, content: GreedyStr
    ):
        """Send a merge-forward message with forged senders.

        Usage: ``/fake <昵称> <QQ> <文本>`` for a single node, or chain multiple
        nodes with ``||`` to fake a whole chat log::

            /fake 张三 10001 第一条 || 李四 10002 第二条 || 张三 10001 第三条

        Append ``头像=<qq>`` (alias ``ava=``) to a node's text to use that QQ
        number's avatar; NapCat renders the avatar of a node's ``user_id`` and
        cannot display custom avatar URLs.
        An attached image is forwarded as sent by the first forged user.
        """
        if not self._check_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return
        if not uin.isdigit():
            yield event.plain_result("Invalid QQ number.")
            return

        image = find_attached_image(event.get_messages())
        segments = [seg.strip() for seg in content.split("||") if seg.strip()]
        first_text = segments[0] if segments else ""
        first_text, first_avatar = split_avatar_marker(first_text)
        first_uin = effective_node_qq(uin, first_avatar)
        if first_uin is None:
            yield event.plain_result(
                "Invalid custom avatar: only a QQ number is supported "
                "(NapCat cannot render custom avatar URLs).",
            )
            return
        nodes: list = []

        first_content: list = []
        if image is not None:
            first_content.append(image)
        if first_text:
            first_content.append(Comp.Plain(first_text))
        if not first_content:
            yield event.plain_result("Nothing to forward: add text or an image.")
            return
        nodes.append(Node(content=first_content, name=name, uin=first_uin))

        for seg in segments[1:]:
            parts = seg.split(maxsplit=2)
            if len(parts) < 2:
                yield event.plain_result(
                    "Invalid node: expected `<昵称> <QQ> <文本>`, separated by `||`.",
                )
                return
            n_name, n_uin, n_text = (
                parts[0],
                parts[1],
                parts[2] if len(parts) > 2 else "",
            )
            if not n_uin.isdigit():
                yield event.plain_result(f"Invalid QQ number in node: {n_uin}")
                return
            n_text, n_avatar = split_avatar_marker(n_text)
            n_uin_eff = effective_node_qq(n_uin, n_avatar)
            if n_uin_eff is None:
                yield event.plain_result(
                    f"Invalid custom avatar '{n_avatar}': only a QQ number is "
                    "supported (NapCat cannot render custom avatar URLs).",
                )
                return
            node_content: list = []
            if n_text.strip():
                node_content.append(Comp.Plain(n_text))
            if not node_content:
                yield event.plain_result("Nothing to forward: add text to each node.")
                return
            nodes.append(Node(content=node_content, name=n_name, uin=n_uin_eff))

        is_group = bool(event.get_group_id())
        target = event.get_group_id() if is_group else event.get_sender_id()
        if not target or not target.isdigit():
            yield event.plain_result("Failed to resolve the conversation target.")
            return

        payload: dict[str, Any] = {"messages": []}
        for node in nodes:
            payload["messages"].append(await node.to_dict())
        if is_group:
            payload["group_id"] = int(target)
        else:
            payload["user_id"] = int(target)
        self_id = getattr(event.message_obj, "self_id", None)
        if self_id:
            payload["self_id"] = self_id
        try:
            if is_group:
                await event.bot.call_action("send_group_forward_msg", **payload)
            else:
                await event.bot.call_action("send_private_forward_msg", **payload)
        except Exception as exc:
            logger.error("fake: failed to send the forward message: %s", exc)
            yield event.plain_result("Failed to send the forward message.")
            return
        event.should_call_llm(True)

    @filter.command("at")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def at(self, event: AstrMessageEvent, qq: str, text: GreedyStr):
        """Send real @ mentions (or @everyone) followed by optional text.

        Usage: ``/at <qq|all|random> <text>``. ``<qq>`` accepts a QQ number, an
        exact group nickname/card, ``all`` for @everyone, or ``random [count]``
        to @ a few random members.
        """
        if not self._check_at_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return

        chain: list = []
        if qq == "all":
            if not event.get_group_id():
                yield event.plain_result("@everyone is only valid in group chats.")
                return
            chain.append(Comp.AtAll())
        elif qq in ("random", "r"):
            if not event.get_group_id():
                yield event.plain_result("Random @ is only valid in group chats.")
                return
            count = 1
            parts = text.split(maxsplit=1) if text.strip() else []
            if parts and parts[0].isdigit():
                count = int(parts[0])
                text = parts[1] if len(parts) > 1 else ""
            try:
                group = await event.get_group()
            except Exception as exc:
                logger.warning("at: failed to fetch the group: %s", exc)
                group = None
            members = list(getattr(group, "members", None) or [])
            if not members:
                yield event.plain_result("Failed to load group members.")
                return
            self_id = getattr(event.message_obj, "self_id", None)
            candidates = [
                m
                for m in members
                if str(getattr(m, "user_id", ""))
                and str(getattr(m, "user_id", "")) != str(self_id)
            ]
            if not candidates:
                yield event.plain_result("No other group members to @.")
                return
            for member in random.sample(candidates, min(count, len(candidates))):
                chain.append(Comp.At(qq=str(getattr(member, "user_id", ""))))
        elif qq.isdigit():
            chain.append(Comp.At(qq=qq))
        else:
            resolved = await self._resolve_member_qq(event, qq)
            if resolved is None:
                yield event.plain_result(
                    f"Invalid target '{qq}': use a QQ number, an exact group "
                    "nickname/card, 'all', or 'random'.",
                )
                return
            chain.append(Comp.At(qq=resolved))
        if text.strip():
            chain.append(Comp.Plain(text))
        yield event.chain_result(chain)

    @filter.command("face")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def face(self, event: AstrMessageEvent, expr: GreedyStr):
        """React to a quoted message with a specified QQ face.

        Usage: reply to a message, then run ``/face <表情>``. ``<表情>`` can be
        a face name (``微笑``), an English alias (``smile``), an emoji (``😀``),
        a raw numeric id (``14``) or a ``#``-prefixed numeric id. Append
        ``cancel`` to remove the reaction, ``big`` to send a big emoji.

        Examples:
            ``/face 微笑`` reacts with the smiling face.
            ``/face 😀`` reacts with the same smile.
            ``/face 爱心 cancel`` removes the bot's heart reaction.
            ``/face cancel`` removes all of the bot's reactions on the message.
        """
        if not self._check_face_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return
        reply = next(
            (seg for seg in event.get_messages() if isinstance(seg, Comp.Reply)),
            None,
        )
        if reply is None:
            yield event.plain_result(
                "Missing quoted message: reply to a message first.",
            )
            return

        tokens = [tok for tok in re.split(r"\s+", expr.strip()) if tok]
        if not tokens:
            yield event.plain_result(
                "Missing face: run `/face <表情>` while replying to a message.",
            )
            return
        cancel = tokens[0] == "cancel" or "cancel" in tokens[1:]
        face_id = None
        if tokens[0] != "cancel":
            face_id = resolve_face_id(tokens[0])
            if face_id is None:
                yield event.plain_result(
                    f"Unknown face '{tokens[0]}': use a face name, an English "
                    "alias, an emoji or a numeric id.",
                )
                return

        try:
            await event.bot.call_action(
                "set_msg_reaction",
                message_id=int(reply.id),
                code=str(face_id) if face_id is not None else None,
                is_cancel=cancel,
                is_big="big" in tokens,
            )
        except Exception as exc:
            logger.error("face: failed to set the reaction: %s", exc)
            yield event.plain_result(
                "Failed to set the reaction (NapCat may not support this action).",
            )
            return
        event.should_call_llm(True)

    @filter.command("card")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def card(self, event: AstrMessageEvent, contact_type: str, contact_id: str):
        """Send a real contact card (recommend friend/group).

        Usage: ``/card qq <QQ号>`` sends that user's business card,
        ``/card group <群号>`` sends the group's card. NapCat builds the card
        from the real contact's profile, so the ID must exist.
        """
        if not self._check_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return
        kind = contact_type.lower()
        if kind in ("qq", "user", "friend"):
            kind = "qq"
        elif kind == "group":
            pass
        else:
            yield event.plain_result("Invalid card type: use `qq` or `group`.")
            return
        if not contact_id.isdigit():
            yield event.plain_result("Invalid contact id.")
            return
        message = [{"type": "contact", "data": {"type": kind, "id": contact_id}}]
        if not await self._send_direct(event, message):
            yield event.plain_result("Failed to send the contact card.")
            return
        event.should_call_llm(True)

    @filter.llm_tool(name="at_user")
    async def at_user(
        self, event: AstrMessageEvent, target: str, at_all: bool = False
    ) -> str:
        """Prepend a real @ mention (or @everyone) to the next reply.

        Args:
            target(string): The user to mention. Accepts an exact group nickname or
                group card, a QQ number, or "all" for @everyone. Only meaningful in
                group chats.
            at_all(boolean): When true, @everyone is used and target is ignored.
        """
        if not self._check_permission(event):
            return "Permission denied: at_user is restricted to admins."
        if at_all or target.strip().lower() == "all":
            if not event.get_group_id():
                return "Failed: @everyone is only available in group chats."
            event.set_extra("pending_at", {"at_all": True})
            return "Recorded: the next reply will start with an @everyone mention."
        qq = target.strip()
        if not qq.isdigit():
            try:
                group = await event.get_group()
            except Exception as exc:
                logger.warning("at_user: failed to fetch the group: %s", exc)
                group = None
            if group is not None:
                resolved = resolve_member_qq(list(group.members or []), qq)
                if resolved:
                    qq = resolved
        if not qq.isdigit():
            return (
                f"Failed to resolve target '{target}': it is neither a valid QQ number "
                "nor an exact group nickname/card."
            )
        event.set_extra("pending_at", {"qq": qq, "name": target.strip()})
        return f"Recorded: the next reply will start with an @ mention of {target} (QQ {qq})."

    @filter.on_decorating_result()
    async def apply_pending_at(self, event: AstrMessageEvent) -> None:
        """Prepend the pending @ mention recorded by the at_user tool."""
        pending = event.get_extra("pending_at")
        if not pending:
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        segment = (
            Comp.AtAll()
            if pending.get("at_all")
            else Comp.At(qq=pending.get("qq", "0"), name=pending.get("name", ""))
        )
        result.chain.insert(0, segment)
        event.set_extra("pending_at", None)
