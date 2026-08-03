import asyncio
import base64
import datetime
import io
import itertools
import json
import os
import random
import re
import wave
from typing import Any

import astrbot.api.message_components as Comp
from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image, Node, Record
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

from .faces import emoji_codepoint, hidden_faces, is_emoji_text, resolve_face_ids

_HFACE_LIST_LIMIT = 20
_FACE_REPEAT_LIMIT = 100
_FACE_RANGE_LIMIT = 20
_MAGIC_RE = re.compile(r"<\$[^>]*>")
_MAGIC_SEND_LIMIT = 4096
_MAGIC_CHUNK = 100
_AUDIO_FILE_EXTENSIONS = (
    ".aac",
    ".amr",
    ".ape",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".silk",
    ".slk",
    ".wav",
    ".wma",
)

_MAGIC_HELP = (
    "用法：/magic <字段>...，如 /magic 178（单字符=face ID）或 /magic 255 256 <c> <d>。\n"
    "多字符族 <$a b c d>：a=255/511（等价），b=256，c=表情包序号（1-80 密集），"
    "d=表情包内序号（1-40 密集）。\n"
    "每个字段支持十进制/0x 十六进制或 a-b 范围（多字段取笛卡尔积，每 100 个一条，"
    f"最多 {_MAGIC_SEND_LIMIT} 个）。\n"
    "也可引用含魔法表情的消息解析码点，或直接发 <$...> 模板。"
)


def parse_code_arg(text: str) -> int | None:
    """Parse a code argument as decimal or ``0x`` hex, ``1..0xFFFF``."""
    text = text.strip().lower()
    try:
        value = int(text, 16) if text.startswith("0x") else int(text)
    except ValueError:
        return None
    if not (1 <= value <= 0xFFFF) or 0xD800 <= value <= 0xDFFF:
        return None
    return value


def parse_field(text: str) -> list[int] | None:
    """Parse one magic field: a single code or an ``a-b`` range of codes."""
    if "-" in text:
        a, _, b = text.partition("-")
        low, high = parse_code_arg(a), parse_code_arg(b)
        if low is None or high is None or low > high:
            return None
        return list(range(low, high + 1))
    code = parse_code_arg(text)
    return [code] if code is not None else None


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


def find_attached_record(messages: list) -> Record | None:
    """Return an audio record attached directly or inside a reply chain."""
    for seg in messages:
        if isinstance(seg, Record):
            return seg
    for seg in messages:
        if isinstance(seg, Comp.Reply):
            for record in seg.chain or []:
                if isinstance(record, Record):
                    return record
    return None


def find_attached_audio_file(messages: list) -> File | None:
    """Return an attached or quoted file whose name/path looks like audio."""

    def is_audio_file(file: File) -> bool:
        candidates = (
            getattr(file, "name", ""),
            getattr(file, "file_", ""),
            getattr(file, "url", ""),
        )
        return any(
            str(candidate).lower().split("?", 1)[0].endswith(_AUDIO_FILE_EXTENSIONS)
            for candidate in candidates
            if candidate
        )

    for seg in messages:
        if isinstance(seg, File) and is_audio_file(seg):
            return seg
    for seg in messages:
        if isinstance(seg, Comp.Reply):
            for file in seg.chain or []:
                if isinstance(file, File) and is_audio_file(file):
                    return file
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


def is_unsupported_api_error(exc: Exception) -> bool:
    """Whether an aiocqhttp exception means the API itself is unsupported.

    Args:
        exc: The exception raised by ``event.bot.call_action``.

    Returns:
        ``True`` when the client rejected the action name as unknown.
    """
    if getattr(exc, "retcode", None) == 1404:
        return True
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'wording', '')} {exc}".lower()
    return any(
        kw in text for kw in ("不支持的api", "unsupported", "not implemented")
    )


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
    (``fake``), real ``@`` mentions (``at``), contact cards (``card``), pokes
    (``poke``), quoted message reactions (``face``), hidden face sending
    (``hface``), experimental location cards (``location``), audio sending
    (``voice``), voice-to-file conversion (``voicefile``) and an LLM ``at_user``
    tool that prepends an ``@`` to the bot's reply.
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        self._face_stop: set[str] = set()

    def _face_key(self, event: AstrMessageEvent) -> str:
        """A per-conversation key used to scope ``face stop`` signals."""
        is_group = bool(event.get_group_id())
        target = event.get_group_id() if is_group else event.get_sender_id()
        return f"{'g' if is_group else 'p'}:{target}"

    async def _sleep_interruptible(self, seconds: float, key: str) -> None:
        """Sleep, returning early when a stop request for ``key`` is set."""
        while seconds > 0 and key not in self._face_stop:
            step = min(0.2, seconds)
            await asyncio.sleep(step)
            seconds -= step

    def _check_permission(self, event: AstrMessageEvent, key: str) -> bool:
        """Whether the event sender may use a command.

        When ``user_id_whitelist`` is non-empty, it becomes the access rule for
        every command. Otherwise each command gates on its own
        ``<name>_admin_only`` config, falling back to the global ``admin_only``.
        """
        whitelist = self._user_id_whitelist()
        if whitelist:
            sender_id = str(event.get_sender_id() or "").strip()
            return sender_id in whitelist
        restricted = self.config.get(key, self.config.get("admin_only", True))
        return not restricted or event.is_admin()

    def _user_id_whitelist(self) -> set[str]:
        """Return normalized QQ IDs allowed to use commands.

        AstrBot normally supplies a list from the config UI, while accepting a
        comma/newline separated string makes hand-written configuration files
        work as expected too. An empty list disables the whitelist restriction.
        """
        raw = self.config.get("user_id_whitelist", [])
        if isinstance(raw, str):
            values = re.split(r"[\s,，、]+", raw)
        elif isinstance(raw, (list, tuple, set)):
            values = raw
        else:
            values = []
        return {
            str(value).strip()
            for value in values
            if str(value).strip()
        }

    def _permission_denied_message(self, event: AstrMessageEvent, key: str) -> str:
        """Explain whether a command was denied by the ID whitelist or admin gate."""
        whitelist = self._user_id_whitelist()
        sender_id = str(event.get_sender_id() or "").strip()
        if whitelist and sender_id not in whitelist:
            return "Permission denied: your user ID is not in the command whitelist."
        return "Permission denied: this command is admin-only."

    def _conversation_target(
        self, event: AstrMessageEvent
    ) -> tuple[bool, int, dict[str, Any]] | None:
        """Resolve the send target as ``(is_group, target_id, routing)``.

        Args:
            event: The message event; the target is its group or sender.

        Returns:
            ``(is_group, target_id, routing)`` where ``routing`` may carry
            ``self_id``, or ``None`` when the target cannot be resolved.
        """
        is_group = bool(event.get_group_id())
        target = event.get_group_id() if is_group else event.get_sender_id()
        if not target or not target.isdigit():
            return None
        routing: dict[str, Any] = {}
        self_id = getattr(event.message_obj, "self_id", None)
        if self_id:
            routing["self_id"] = self_id
        return is_group, int(target), routing

    async def _send_direct(self, event: AstrMessageEvent, message: list) -> bool:
        """Send a raw OneBot message to the conversation, returning success.

        Args:
            event: The message event; the target is its group or sender.
            message: OneBot message segment list.
        """
        target_info = self._conversation_target(event)
        if target_info is None:
            return False
        is_group, target, routing = target_info
        try:
            if is_group:
                await event.bot.send_group_msg(
                    group_id=target, message=message, **routing
                )
            else:
                await event.bot.send_private_msg(
                    user_id=target, message=message, **routing
                )
        except Exception as exc:
            logger.error("direct send failed: %s", exc)
            return False
        return True

    async def _send_forward_msg(self, event: AstrMessageEvent, nodes: list) -> bool:
        """Send a merge-forward message built from nodes, returning success.

        Args:
            event: The message event; the target is its group or sender.
            nodes: ``Node`` components to forward.
        """
        target_info = self._conversation_target(event)
        if target_info is None:
            return False
        is_group, target, routing = target_info
        payload: dict[str, Any] = {"messages": []}
        for node in nodes:
            payload["messages"].append(await node.to_dict())
        if is_group:
            payload["group_id"] = target
        else:
            payload["user_id"] = target
        payload.update(routing)
        try:
            await event.bot.call_action(
                "send_group_forward_msg" if is_group else "send_private_forward_msg",
                **payload,
            )
        except Exception as exc:
            logger.error("failed to send the forward message: %s", exc)
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
        if not self._check_permission(event, "himg_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "himg_admin_only")
            )
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

        target_info = self._conversation_target(event)
        if target_info is None:
            yield event.plain_result("Failed to resolve the conversation target.")
            return
        is_group, target, routing = target_info
        message = build_image_summary_message(file, text.strip())
        try:
            if is_group:
                await event.bot.send_group_msg(
                    group_id=target,
                    message=message,
                    **routing,
                )
            else:
                await event.bot.send_private_msg(
                    user_id=target,
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
        if not self._check_permission(event, "fake_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "fake_admin_only")
            )
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

        if not await self._send_forward_msg(event, nodes):
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
        if not self._check_permission(event, "at_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "at_admin_only")
            )
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
        """React to a quoted message with a specified QQ face or a literal emoji.

        Usage: reply to a message, then run ``/face <表情>``. ``<表情>`` can be
        a face name (``微笑``), an English alias (``smile``), a numeric id
        (``14``), a ``#``-prefixed numeric id, a numeric range ``a-b``, an
        emoji, or a repeat ``NxM``. A repeat toggles the same reaction
        set/cancel (``1x20`` points face 1 then cancels, 20 times), since a
        reaction can only appear once per account. An emoji reacts with the
        literal emoji itself; a name/number reacts with the matching QQ face.
        Append ``cancel`` to remove the reaction, ``big`` to send a big emoji.

        A range or repeat is executed one by one, spaced by the configurable
        ``face_interval``; the bot first reports the expected duration and
        afterwards a summary when any step failed. Run ``/face stop`` during a
        run to abort it early.

        NapCat uses ``set_msg_emoji_like``; other OneBot implementations that
        only provide ``set_msg_reaction`` are handled automatically.

        Examples:
            ``/face 微笑`` reacts with the smiling QQ face.
            ``/face 💢`` reacts with the literal 💢 emoji.
            ``/face 66-70`` reacts with faces 66 to 70, one per interval.
            ``/face 1x20`` blasts face 1 by toggling it 20 times.
            ``/face stop`` aborts a running range.
            ``/face 爱心 cancel`` removes the bot's heart reaction.
        """
        if not self._check_permission(event, "face_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "face_admin_only")
            )
            return
        tokens = [tok for tok in re.split(r"\s+", expr.strip()) if tok]
        if not tokens:
            yield event.plain_result(
                "Missing face: run `/face <表情>` while replying to a message.",
            )
            return
        if tokens[0] == "stop":
            self._face_stop.add(self._face_key(event))
            yield event.plain_result("已发送停止指令，范围回应将尽快中止。")
            event.should_call_llm(True)
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
        cancel = "cancel" in tokens
        is_big = "big" in tokens
        head = tokens[0]
        if head in ("cancel", "big"):
            yield event.plain_result(
                f"Missing face: specify the emoji first, e.g. `/face 爱心 {head}`.",
            )
            return
        match = re.match(r"^(.*)x(\d+)$", head, re.IGNORECASE)
        if match:
            base, count = match.group(1), max(int(match.group(2)), 1)
            if count > _FACE_REPEAT_LIMIT:
                yield event.plain_result(
                    f"Too many repeats: at most {_FACE_REPEAT_LIMIT}.",
                )
                return
        else:
            base, count = head, 1
        if is_emoji_text(base):
            codepoint = emoji_codepoint(base)
            if codepoint is None:
                yield event.plain_result(
                    "Unknown face: use a face name, an English alias, a numeric "
                    "id, a range `a-b` or a repeat `NxM`.",
                )
                return
            emojis = [str(codepoint)]
        else:
            face_ids = resolve_face_ids(base)
            if not face_ids:
                yield event.plain_result(
                    f"Unknown face '{head}': use a face name, an English alias, "
                    "a numeric id, a range `a-b` or a repeat `NxM`.",
                )
                return
            if len(face_ids) > _FACE_RANGE_LIMIT:
                yield event.plain_result(
                    f"Too many faces: QQ limits reactions to at most "
                    f"{_FACE_RANGE_LIMIT} per message.",
                )
                return
            emojis = [str(face_id) for face_id in face_ids]

        # repeat toggles a single face (set/cancel) so it stays visible
        toggle = count > 1 and len(emojis) == 1
        if toggle:
            ops = []
            for i in range(count):
                if i:
                    ops.append((emojis[0], False))
                ops.append((emojis[0], True))
        else:
            set_ = not cancel
            ops = [(emoji, set_) for emoji in emojis] * count

        if len(ops) == 1:
            emoji, set_ = ops[0]
            used = await self._apply_reaction(
                event,
                message_id=int(reply.id),
                emoji=emoji,
                set_=set_,
                is_big=is_big,
            )
            if used is None:
                yield event.plain_result(
                    "Failed to set the reaction: the protocol client does not "
                    "support any reaction API.",
                )
                return
            event.should_call_llm(True)
            return

        interval = max(float(self.config.get("face_interval", 1.0)), 0.0)
        if toggle:
            notice = (
                f"将对表情 {emojis[0]} 轰炸 {count} 次（点/取消交替，共 "
                f"{len(ops)} 次操作），间隔 {interval:g} 秒，预计约 "
                f"{len(ops) * interval:g} 秒完成。可用 `/face stop` 中止。"
            )
        else:
            notice = (
                f"将依次回应 {len(ops)} 个表情，每个间隔 {interval:g} 秒，"
                f"预计约 {len(ops) * interval:g} 秒完成。"
                "可用 `/face stop` 中止。"
            )
        await self._send_direct(
            event,
            [{"type": "text", "data": {"text": notice}}],
        )
        key = self._face_key(event)
        self._face_stop.discard(key)
        failures = []
        done = 0
        aborted = False
        for idx, (emoji, set_) in enumerate(ops):
            if key in self._face_stop:
                aborted = True
                break
            used = await self._apply_reaction(
                event,
                message_id=int(reply.id),
                emoji=emoji,
                set_=set_,
                is_big=is_big,
            )
            if used is None:
                failures.append(emoji)
            else:
                done += 1
            if idx < len(ops) - 1 and interval > 0:
                await self._sleep_interruptible(interval, key)
        self._face_stop.discard(key)
        if aborted:
            await self._send_direct(
                event,
                [{"type": "text", "data": {"text": f"已中止，已完成 {done} 次操作。"}}],
            )
        elif failures:
            await self._send_direct(
                event,
                [{"type": "text", "data": {"text": (
                    f"有 {len(failures)} 次操作失败："
                    + " ".join(failures)
                )}}],
            )
        event.should_call_llm(True)

    async def _apply_reaction(
        self,
        event: AstrMessageEvent,
        message_id: int,
        emoji: str,
        set_: bool,
        is_big: bool,
    ) -> str | None:
        """Set or clear a message reaction, returning the used action name.

        Tries NapCat's ``set_msg_emoji_like`` first, then falls back to the
        OneBot extended ``set_msg_reaction`` when the primary action is
        unsupported.

        Returns:
            The action name used, or ``None`` when every attempt failed.
        """
        for action in ("set_msg_emoji_like", "set_msg_reaction"):
            if action == "set_msg_emoji_like":
                payload: dict[str, Any] = {
                    "message_id": message_id,
                    "emoji_id": emoji,
                    "set": set_,
                }
            else:
                payload = {
                    "message_id": message_id,
                    "code": emoji,
                    "is_cancel": not set_,
                    "is_big": is_big,
                }
            try:
                await event.bot.call_action(action, **payload)
                return action
            except Exception as exc:
                logger.warning("face: %s failed: %s", action, exc)
                if not is_unsupported_api_error(exc):
                    return None
        return None

    @filter.command("hface")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def hface(self, event: AstrMessageEvent, expr: GreedyStr):
        """Send built-in QQ faces, including hidden ones, as a message.

        Usage: ``/hface <名称|ID> [文本]`` sends the matching QQ faces (plus
        optional text); ``/hface 1-5`` sends every face from 1 to 5; an emoji
        is sent literally as text (e.g. ``/hface 💢``); ``/hface list [a-b]``
        sends hidden faces 1..20 (or the given 1-based range, at most 20) as a
        forward message; ``/hface text <内容>`` sends the content literally.
        ``<名称|ID>`` accepts a face name, an English alias, a raw numeric id,
        a ``#``-prefixed id, or a numeric range ``a-b``. Face ids follow
        NapCat's built-in table (see FACES.md).
        """
        if not self._check_permission(event, "hface_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "hface_admin_only")
            )
            return
        tokens = [tok for tok in re.split(r"\s+", expr.strip()) if tok]
        if not tokens:
            yield event.plain_result(
                "Missing face: run `/hface <名称|ID>`, or `/hface list` to "
                "list hidden faces.",
            )
            return
        if tokens[0] == "list":
            error = await self._send_hidden_list(event, tokens[1:])
            if error:
                yield event.plain_result(error)
                return
            event.should_call_llm(True)
            return
        if tokens[0] == "text":
            text = " ".join(tokens[1:]).strip()
            if not text:
                yield event.plain_result("Missing text: run `/hface text <内容>`.")
                return
            yield event.chain_result([Comp.Plain(text)])
            return
        if is_emoji_text(tokens[0]):
            yield event.chain_result([Comp.Plain(" ".join(tokens).strip())])
            return
        face_ids = resolve_face_ids(tokens[0])
        if not face_ids:
            yield event.plain_result(
                f"Unknown face '{tokens[0]}': use a face name, an English "
                "alias, a numeric id or a range `a-b`.",
            )
            return
        message: list = [
            {"type": "face", "data": {"id": face_id}} for face_id in face_ids
        ]
        text = " ".join(tokens[1:]).strip()
        if text:
            message.append({"type": "text", "data": {"text": text}})
        if not await self._send_direct(event, message):
            yield event.plain_result(
                f"Failed to send face(s) {face_ids[:20]}"
                + ("..." if len(face_ids) > 20 else ""),
            )
            return
        event.should_call_llm(True)

    async def _send_hidden_list(
        self, event: AstrMessageEvent, args: list[str]
    ) -> str | None:
        """Send a page of hidden faces as a forward message.

        Args:
            event: The message event; the target is its group or sender.
            args: Optional ``a-b`` 1-based page over the hidden face list.

        Returns:
            An error message, or ``None`` on success.
        """
        hidden = hidden_faces()
        total = len(hidden)
        start, end = 1, min(_HFACE_LIST_LIMIT, total)
        if args:
            match = re.match(r"^(\d+)-(\d+)$", args[0])
            if match:
                start = max(int(match.group(1)), 1)
                end = min(int(match.group(2)), total)
                if end - start + 1 > _HFACE_LIST_LIMIT:
                    end = start + _HFACE_LIST_LIMIT - 1
            else:
                return "Invalid page: use `/hface list a-b`, e.g. `/hface list 21-40`."
        if start > end or start > total:
            return f"Empty page: hidden faces range from 1 to {total}."
        page = hidden[start - 1 : end]
        self_uin = str(getattr(event.message_obj, "self_id", None) or "10001")
        nodes = [
            Node(content=[Comp.Face(id=qsid)], name=name, uin=self_uin)
            for qsid, name in page
        ]
        nodes.insert(
            0,
            Node(
                content=[Comp.Plain(f"隐藏表情 {start}-{end} / 共 {total} 个")],
                name="QmessageQ",
                uin=self_uin,
            ),
        )
        if not await self._send_forward_msg(event, nodes):
            return "Failed to send the hidden face list."
        return None

    @staticmethod
    def _extract_forward_res_id(raw: str) -> str | None:
        """Extract a merge-forward ``resid`` from a ``com.tencent.multimsg`` card."""
        try:
            obj = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        detail = (obj.get("meta") or {}).get("detail")
        if isinstance(detail, dict):
            resid = detail.get("resid")
            if resid:
                return str(resid)
        return None

    @staticmethod
    def _json_card_summary(raw: str) -> str:
        """Extract a readable snippet from a QQ JSON card's ``data`` string."""
        try:
            obj = json.loads(raw)
        except (TypeError, ValueError):
            return ""
        if not isinstance(obj, dict):
            return ""
        for key in ("prompt", "title"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:60]
        meta = obj.get("meta")
        if isinstance(meta, dict):
            for sub in meta.values():
                if isinstance(sub, dict):
                    for key in ("title", "desc", "prompt"):
                        value = sub.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()[:60]
        return ""

    @classmethod
    def _format_segments(cls, segments: list) -> str:
        """Compact single-line rendering of OneBot segments."""
        if isinstance(segments, str):
            return segments
        parts = []
        for seg in segments or []:
            if not isinstance(seg, dict):
                continue
            stype = seg.get("type")
            data = seg.get("data") or {}
            if stype == "text":
                text = str(data.get("text", ""))
                if text.strip():
                    parts.append(text)
            elif stype == "face":
                parts.append(f"[表情:{data.get('id')}]")
            elif stype == "image":
                summary = data.get("summary")
                parts.append(f"[图:{summary}]" if summary else "[图]")
            elif stype == "at":
                parts.append(f"@{data.get('qq') or data.get('name') or ''}")
            elif stype == "forward":
                parts.append(f"[转发:{data.get('id')}]")
            elif stype == "json":
                raw = data.get("data") or ""
                if cls._extract_forward_res_id(raw):
                    parts.append("[转发聊天记录]")
                else:
                    summary = cls._json_card_summary(raw)
                    parts.append(f"[卡片:{summary}]" if summary else "[卡片]")
            elif stype == "record":
                parts.append("[语音]")
            elif stype == "video":
                parts.append("[视频]")
            elif stype == "file":
                name = data.get("name") or data.get("file") or ""
                parts.append(f"[文件:{name}]" if name else "[文件]")
            elif stype == "contact":
                parts.append(f"[名片:{data.get('id')}]")
            elif stype == "reply":
                parts.append(f"[回复:{data.get('id')}]")
            else:
                parts.append(f"[{stype}]")
        return " ".join(parts)

    def _collect_dict_segments(
        self,
        content_lines: list[str],
        forward_ids: list[str],
        segments: list,
    ) -> None:
        """Collect readable lines from OneBot dict segments."""
        for seg in segments or []:
            if not isinstance(seg, dict):
                continue
            stype = seg.get("type")
            data = seg.get("data") or {}
            if stype == "text":
                text = str(data.get("text", ""))
                if text.strip():
                    content_lines.append(f"文本: {text}")
            elif stype == "face":
                content_lines.append(f"表情ID: {data.get('id')}")
            elif stype == "image":
                summary = data.get("summary")
                url = data.get("url") or data.get("file") or ""
                if summary:
                    content_lines.append(f"图片summary: {summary}  url: {url}")
                else:
                    content_lines.append(f"图片: {url}")
            elif stype == "at":
                content_lines.append(
                    f"@{data.get('qq') or data.get('name') or ''}"
                )
            elif stype == "json":
                raw = data.get("data") or ""
                resid = self._extract_forward_res_id(raw)
                if resid:
                    forward_ids.append(resid)
                    content_lines.append(f"合并转发: res_id={resid}")
                else:
                    summary = self._json_card_summary(raw)
                    content_lines.append(
                        f"卡片: {summary}" if summary else "卡片"
                    )
            elif stype == "reply":
                content_lines.append(f"[回复:{data.get('id')}]")
            elif stype == "forward":
                res_id = str(data.get("id"))
                forward_ids.append(res_id)
                content_lines.append(f"合并转发: res_id={res_id}")
            else:
                content_lines.append(f"[{stype}]")

    def _collect_chain(
        self,
        content_lines: list[str],
        forward_ids: list[str],
        chain: list,
    ) -> None:
        """Collect readable lines from a Reply chain of components."""
        for seg in chain or []:
            if isinstance(seg, Comp.Plain):
                if seg.text.strip():
                    content_lines.append(f"文本: {seg.text}")
            elif isinstance(seg, Comp.Face):
                content_lines.append(f"表情ID: {seg.id}")
            elif isinstance(seg, Comp.Image):
                summary = getattr(seg, "summary", "") or ""
                url = seg.url or seg.file or ""
                if summary:
                    content_lines.append(f"图片summary: {summary}  url: {url}")
                else:
                    content_lines.append(f"图片: {url}")
            elif isinstance(seg, Comp.AtAll):
                content_lines.append("@全体成员")
            elif isinstance(seg, Comp.At):
                content_lines.append(f"@{seg.qq}")
            elif isinstance(seg, Comp.Forward):
                res_id = str(seg.id)
                forward_ids.append(res_id)
                content_lines.append(f"合并转发: res_id={res_id}")
            elif isinstance(seg, Comp.Record):
                continue
            else:
                content_lines.append(f"[{getattr(seg, 'type', '?')}]")

    @staticmethod
    def _format_file_size(value: Any) -> str:
        """Format a byte count returned by OneBot/NapCat."""
        try:
            size = int(value)
        except (TypeError, ValueError):
            return str(value or "")
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KiB ({size} B)"
        return f"{size / 1024 / 1024:.2f} MiB ({size} B)"

    @staticmethod
    def _wav_duration(record_info: dict) -> float | None:
        """Read WAV duration from NapCat's get_record result when accessible."""
        sources: list[Any] = []
        encoded = record_info.get("base64")
        if encoded:
            try:
                sources.append(io.BytesIO(base64.b64decode(encoded)))
            except (ValueError, TypeError):
                pass
        for key in ("file", "url"):
            path = record_info.get(key)
            if isinstance(path, str) and os.path.isfile(path):
                sources.append(path)
        for source in sources:
            try:
                with wave.open(source, "rb") as wav_file:
                    rate = wav_file.getframerate()
                    if rate > 0:
                        return wav_file.getnframes() / rate
            except (OSError, EOFError, wave.Error):
                continue
        return None

    async def _describe_record(
        self,
        event: AstrMessageEvent,
        message_id: int,
        data: dict,
        index: int,
    ) -> str:
        """Best-effort details for one voice segment in a parsed message."""
        file_ref = str(data.get("file") or data.get("file_id") or "")

        async def get_record_info() -> dict:
            if not file_ref:
                return {}
            try:
                result = await event.bot.call_action(
                    "get_record",
                    file=file_ref,
                    out_format="wav",
                )
                return result if isinstance(result, dict) else {}
            except Exception as exc:
                logger.debug("parse: get_record failed: %s", exc)
                return {}

        async def get_transcript() -> str:
            try:
                result = await event.bot.call_action(
                    "fetch_ptt_text",
                    message_id=message_id,
                )
            except Exception as exc:
                logger.debug("parse: fetch_ptt_text failed: %s", exc)
                return ""
            if isinstance(result, dict):
                return str(result.get("text") or "").strip()
            return ""

        record_info, transcript = await asyncio.gather(
            get_record_info(),
            get_transcript(),
        )
        merged = {**data, **record_info}
        lines = [f"语音 {index}:"]

        duration = data.get("duration")
        try:
            duration_value = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_value = None
        if duration_value is None:
            duration_value = self._wav_duration(record_info)
        if duration_value is not None:
            lines.append(f"时长: {duration_value:.2f} 秒")
        else:
            lines.append("时长: 协议端未提供，且无法读取转换后的 WAV")

        file_size = merged.get("file_size")
        if file_size not in (None, ""):
            lines.append(f"文件大小: {self._format_file_size(file_size)}")
        file_name = merged.get("file_name") or merged.get("name")
        if file_name:
            lines.append(f"文件名: {file_name}")
        if file_ref:
            lines.append(f"文件标识: {file_ref}")
        url = data.get("url") or record_info.get("url")
        if url and not str(url).startswith("base64://"):
            lines.append(f"URL/路径: {url}")
        if transcript:
            lines.append(f"语音转写: {transcript}")
        else:
            lines.append("语音转写: 不可用或识别失败")
        return "\n".join(lines)

    @filter.command("parse")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def parse(self, event: AstrMessageEvent):
        """Parse a quoted message: reactions, face ids, image summaries, forward details.

        Usage: reply to a message, then run ``/parse``. Sends a merge-forward
        message with one node per section: basic info, time, content (text /
        face ids / mentions), voice details, image summaries, emoji reactions
        and, for a merge-forward message, its node list. Content is read from
        the reply's own chain when the message can no longer be fetched
        (sender/time still come from the reply itself).
        """
        if not self._check_permission(event, "parse_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "parse_admin_only")
            )
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
        message_id = int(reply.id)
        info = None
        try:
            info = await event.bot.call_action("get_msg", message_id=message_id)
        except Exception as exc:
            logger.warning("parse: get_msg failed: %s", exc)

        self_uin = str(getattr(event.message_obj, "self_id", None) or "10001")
        nodes: list = []

        def section(label: str, text: str) -> None:
            nodes.append(Node(content=[Comp.Plain(text)], name=label, uin=self_uin))

        if info:
            sender = info.get("sender") or {}
            sender_name = sender.get("card") or sender.get("nickname") or "?"
            sender_qq = sender.get("user_id", "?")
            ts = info.get("time")
        else:
            sender_name = reply.sender_nickname or "?"
            sender_qq = reply.sender_id or "?"
            ts = reply.time
        section(
            "基本信息",
            "\n".join(
                [
                    f"消息ID: {message_id}",
                    f"发送者: {sender_name} ({sender_qq})",
                ]
            ),
        )
        if ts:
            section(
                "时间",
                f"{datetime.datetime.fromtimestamp(ts):%Y-%m-%d %H:%M:%S}",
            )

        content_lines: list[str] = []
        forward_ids: list[str] = []
        record_data: list[dict] = []
        message = info.get("message") if info else None
        if isinstance(message, list):
            record_data = [
                dict(seg.get("data") or {})
                for seg in message
                if isinstance(seg, dict) and seg.get("type") == "record"
            ]
            non_record_message = [
                seg
                for seg in message
                if not (isinstance(seg, dict) and seg.get("type") == "record")
            ]
            self._collect_dict_segments(
                content_lines,
                forward_ids,
                non_record_message,
            )
        else:
            self._collect_chain(content_lines, forward_ids, reply.chain)
            record_data = [
                {
                    "file": seg.file or "",
                    "url": seg.url or "",
                    "path": seg.path or "",
                    "text": seg.text or "",
                }
                for seg in reply.chain or []
                if isinstance(seg, Comp.Record)
            ]
        if content_lines:
            section("内容", "\n".join(content_lines))
        if record_data:
            record_details = await asyncio.gather(
                *(
                    self._describe_record(event, message_id, data, index)
                    for index, data in enumerate(record_data, start=1)
                )
            )
            section("语音", "\n\n".join(record_details))

        likes = info.get("emoji_likes_list") if info else None
        if likes:
            section(
                "回应",
                "\n".join(
                    f"emoji_id={like.get('emoji_id')} "
                    f"type={like.get('emoji_type')} x{like.get('likes_cnt')}"
                    for like in likes
                ),
            )

        for res_id in forward_ids:
            try:
                fwd = await event.bot.call_action("get_forward_msg", message_id=res_id)
            except Exception as exc:
                logger.warning("parse: get_forward_msg failed: %s", exc)
                section("转发", "获取失败（消息可能已过期）")
                continue
            fwd_nodes = fwd.get("messages") or []
            forward_lines = [f"共 {len(fwd_nodes)} 条 (res_id={res_id}):"]
            for i, node in enumerate(fwd_nodes[:30]):
                if not isinstance(node, dict):
                    continue
                data = node.get("data") or {}
                sender = node.get("sender") or {}
                nickname = (
                    data.get("nickname")
                    or data.get("name")
                    or sender.get("nickname")
                    or node.get("nickname")
                    or "?"
                )
                uid = (
                    data.get("user_id")
                    or data.get("uin")
                    or node.get("user_id")
                    or "?"
                )
                content = (
                    data.get("content")
                    or data.get("message")
                    or node.get("message")
                    or node.get("content")
                    or []
                )
                forward_lines.append(
                    f"[{i}] {nickname}({uid}): {self._format_segments(content)}"
                )
            if len(fwd_nodes) > 30:
                forward_lines.append(f"... 其余 {len(fwd_nodes) - 30} 条省略")
            section("转发", "\n".join(forward_lines))

        if not await self._send_forward_msg(event, nodes):
            yield event.plain_result("Failed to send the parse result.")
            return
        event.should_call_llm(True)

    @filter.command("magic")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def magic(self, event: AstrMessageEvent, expr: GreedyStr):
        """Output QQ magic-expression (魔法表情) templates.

        QQ renders ``<$...>`` templates embedded in text as magic emoji. Stateless
        generator: ``/magic <数字序列>`` builds ``<$chr(n1)chr(n2)...>`` (e.g.
        ``/magic 255 256 17 16`` -> ``<$ÿĀ\x11\x10>`` 摇手手); ``/magic <模板>``
        sends an arbitrary template; quoting a message prints each magic
        expression as ``magic <十进制码点> | <0x 十六进制码点>`` so it can be
        reproduced. Run without args for usage.
        """
        if not self._check_permission(event, "magic_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "magic_admin_only")
            )
            return
        reply = next(
            (seg for seg in event.get_messages() if isinstance(seg, Comp.Reply)),
            None,
        )
        if reply is not None and not expr.strip():
            source = (reply.message_str or "") + "".join(
                seg.text
                for seg in reply.chain or []
                if isinstance(seg, Comp.Plain)
            )
            templates = _MAGIC_RE.findall(source)
            if not templates:
                yield event.plain_result("引用的消息里没有魔法表情模板。")
                return
            lines = []
            for template in templates:
                codes = [ord(c) for c in template[2:-1]]
                dec = " ".join(str(c) for c in codes)
                hexes = " ".join(f"0x{c:02x}" for c in codes)
                lines.append(f"magic {dec} | {hexes}")
            yield event.plain_result("\n".join(lines))
            return

        arg = expr.strip()
        if not arg:
            yield event.plain_result(_MAGIC_HELP)
            return
        if _MAGIC_RE.search(arg):
            yield event.chain_result([Comp.Plain(arg)])
            return
        fields = []
        for token in arg.split():
            values = parse_field(token)
            if values is None:
                yield event.plain_result(_MAGIC_HELP)
                return
            fields.append(values)
        templates = [
            "<$" + "".join(chr(code) for code in combo) + ">"
            for combo in itertools.product(*fields)
        ]
        if len(templates) > _MAGIC_SEND_LIMIT:
            yield event.plain_result(
                f"Too many combinations: at most {_MAGIC_SEND_LIMIT}.",
            )
            return
        if len(templates) == 1:
            yield event.chain_result([Comp.Plain(templates[0])])
            return
        for i in range(0, len(templates), _MAGIC_CHUNK):
            await self._send_direct(
                event,
                [{"type": "text", "data": {"text": "".join(templates[i:i + _MAGIC_CHUNK])}}],
            )
        event.should_call_llm(True)

    @filter.command("card")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def card(self, event: AstrMessageEvent, contact_type: str, contact_id: str):
        """Send a real contact card (recommend friend/group).

        Usage: ``/card qq <QQ号>`` sends that user's business card,
        ``/card group <群号>`` sends the group's card. NapCat builds the card
        from the real contact's profile, so the ID must exist.
        """
        if not self._check_permission(event, "card_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "card_admin_only")
            )
            return
        kind = contact_type.lower()
        if kind not in ("qq", "user", "friend", "group"):
            yield event.plain_result("Invalid card type: use `qq` or `group`.")
            return
        if kind in ("user", "friend"):
            kind = "qq"
        if not contact_id.isdigit():
            yield event.plain_result("Invalid contact id.")
            return
        message = [{"type": "contact", "data": {"type": kind, "id": contact_id}}]
        if not await self._send_direct(event, message):
            yield event.plain_result("Failed to send the contact card.")
            return
        event.should_call_llm(True)

    @filter.command("poke")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def poke(self, event: AstrMessageEvent, target: GreedyStr):
        """Poke a friend or a group member through NapCat's packet API.

        Usage: ``/poke`` pokes the current friend in private chat or the command
        sender in a group. In a group, ``/poke <QQ|@|nickname>`` selects another
        member. NapCat must have packetBackend sending support available.
        """
        if not self._check_permission(event, "poke_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "poke_admin_only")
            )
            return

        target_info = self._conversation_target(event)
        if target_info is None:
            yield event.plain_result("Failed to resolve the current conversation.")
            return
        is_group, peer_id, routing = target_info

        target_qq = str(peer_id)
        if is_group:
            self_id = str(event.get_self_id() or "")
            mentioned = next(
                (
                    str(seg.qq)
                    for seg in event.get_messages()
                    if isinstance(seg, Comp.At)
                    and str(seg.qq).isdigit()
                    and str(seg.qq) != self_id
                ),
                "",
            )
            raw_target = target.strip()
            normalized = raw_target.lstrip("@").strip()
            if mentioned:
                target_qq = mentioned
            elif not normalized or normalized.lower() in ("me", "self", "我", "自己"):
                target_qq = str(event.get_sender_id() or "")
            elif normalized.isdigit():
                target_qq = normalized
            else:
                resolved = await self._resolve_member_qq(event, normalized)
                if resolved is None:
                    yield event.plain_result(
                        f"Invalid target '{raw_target}': use a QQ number, @ a "
                        "member, or enter an exact group nickname/card."
                    )
                    return
                target_qq = resolved

        if not target_qq.isdigit():
            yield event.plain_result("Failed to resolve the poke target's QQ ID.")
            return

        payload: dict[str, Any] = {"user_id": target_qq, **routing}
        if is_group:
            payload["group_id"] = str(peer_id)
        try:
            await event.bot.call_action("send_poke", **payload)
        except Exception as exc:
            logger.warning("poke: send_poke failed: %s", exc)
            error_text = str(exc).lower()
            if "packetbackend" in error_text or "发包能力" in error_text:
                yield event.plain_result(
                    "Failed to poke: NapCat packetBackend sending support is "
                    "unavailable."
                )
            elif is_unsupported_api_error(exc):
                yield event.plain_result(
                    "Failed to poke: this protocol client does not support send_poke."
                )
            else:
                yield event.plain_result("Failed to send the poke.")
            return
        event.should_call_llm(True)

    @filter.command("location")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def location(self, event: AstrMessageEvent, args: GreedyStr):
        """Send an experimental OneBot location segment.

        Usage: ``/location`` sends the default empty-shell card. Optionally use
        ``/location <lat> <lon> [title[||content]]``. Current NapCat builds a
        placeholder ShareLocation element and may ignore every supplied field;
        this command intentionally exposes that behavior for testing.
        """
        if not self._check_permission(event, "location_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "location_admin_only")
            )
            return

        lat = 0.0
        lon = 0.0
        title = "测试位置"
        content = ""
        parts = args.strip().split(maxsplit=2)
        if parts:
            if len(parts) < 2:
                yield event.plain_result(
                    "Usage: /location [<latitude> <longitude> [title[||content]]]"
                )
                return
            try:
                lat = float(parts[0])
                lon = float(parts[1])
            except ValueError:
                yield event.plain_result("Invalid coordinates: use decimal numbers.")
                return
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                yield event.plain_result(
                    "Invalid coordinates: latitude must be -90..90 and "
                    "longitude -180..180."
                )
                return
            if len(parts) > 2:
                title, separator, content = parts[2].partition("||")
                title = title.strip() or "测试位置"
                content = content.strip() if separator else ""

        message = [
            {
                "type": "location",
                "data": {
                    "lat": lat,
                    "lon": lon,
                    "title": title,
                    "content": content,
                },
            }
        ]
        if not await self._send_direct(event, message):
            yield event.plain_result("Failed to send the experimental location card.")
            return
        event.should_call_llm(True)

    @filter.command("voice")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def voice(self, event: AstrMessageEvent, source: GreedyStr):
        """Send an attached, quoted or remotely hosted audio file as QQ voice.

        Usage: attach/reply to a voice or audio file and run ``/voice``, or use
        ``/voice <http(s) audio URL>``. Attached voice is converted to base64;
        referenced audio files use their resolved URL/path. NapCat then converts
        supported input formats to Silk before sending.
        """
        if not self._check_permission(event, "voice_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "voice_admin_only")
            )
            return

        messages = event.get_messages()
        record = find_attached_record(messages)
        audio_file = find_attached_audio_file(messages)
        file = ""
        if record is not None:
            try:
                base64_data = await record.convert_to_base64()
            except Exception as exc:
                logger.warning("voice: failed to load the attached audio: %s", exc)
                yield event.plain_result("Failed to load the attached audio.")
                return
            file = f"base64://{base64_data}"
        elif audio_file is not None:
            try:
                file = await audio_file.get_file(allow_return_url=True)
            except Exception as exc:
                logger.warning("voice: failed to resolve the audio file: %s", exc)
                yield event.plain_result("Failed to load the referenced audio file.")
                return
            if not file:
                yield event.plain_result("Failed to load the referenced audio file.")
                return
        else:
            url = source.strip()
            if not re.fullmatch(r"https?://\S+", url):
                yield event.plain_result(
                    "Missing audio: attach/reply to a voice or audio file, or use "
                    "`/voice <audio URL>`."
                )
                return
            file = url

        if not await self._send_direct(
            event,
            [{"type": "record", "data": {"file": file}}],
        ):
            yield event.plain_result("Failed to send the voice message.")
            return
        event.should_call_llm(True)

    @filter.command("voicefile")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def voicefile(self, event: AstrMessageEvent):
        """Convert an attached or quoted QQ voice to a playable WAV file.

        NapCat resolves the received record, converts it through FFmpeg and
        returns Base64/path data; the result is then sent as a normal file.
        """
        if not self._check_permission(event, "voicefile_admin_only"):
            yield event.plain_result(
                self._permission_denied_message(event, "voicefile_admin_only")
            )
            return

        messages = event.get_messages()
        record = find_attached_record(messages)
        if record is None:
            yield event.plain_result(
                "Missing voice: attach or reply to a QQ voice message first."
            )
            return
        record_ref = next(
            (
                str(value)
                for value in (
                    getattr(record, "file", ""),
                    getattr(record, "url", ""),
                    getattr(record, "path", ""),
                )
                if value
            ),
            "",
        )
        if not record_ref:
            yield event.plain_result("Failed to resolve the quoted voice file.")
            return

        try:
            result = await event.bot.call_action(
                "get_record",
                file=record_ref,
                out_format="wav",
            )
        except Exception as exc:
            logger.warning("voicefile: get_record failed: %s", exc)
            yield event.plain_result(
                "Failed to convert the voice to WAV; check NapCat's FFmpeg "
                "configuration."
            )
            return
        if not isinstance(result, dict):
            yield event.plain_result("Failed to convert the voice: empty result.")
            return

        encoded = str(result.get("base64") or "")
        converted = (
            f"base64://{encoded}"
            if encoded
            else str(result.get("url") or result.get("file") or "")
        )
        if not converted:
            yield event.plain_result("Failed to read the converted WAV file.")
            return

        reply = next(
            (seg for seg in messages if isinstance(seg, Comp.Reply)),
            None,
        )
        suffix = f"_{reply.id}" if reply is not None and reply.id else ""
        name = f"voice{suffix}.wav"
        if not await self._send_direct(
            event,
            [{"type": "file", "data": {"file": converted, "name": name}}],
        ):
            yield event.plain_result("Failed to send the converted WAV file.")
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
        if not self._check_permission(event, "at_user_admin_only"):
            return self._permission_denied_message(event, "at_user_admin_only")
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
