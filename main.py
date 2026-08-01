from __future__ import annotations

import asyncio
import datetime
import json
import random
import re
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Node
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .faces import emoji_codepoint, hidden_faces, is_emoji_text, resolve_face_ids

_HFACE_LIST_LIMIT = 20
_FACE_REPEAT_LIMIT = 100
_MAGIC_RE = re.compile(r"<\$[^>]*>")
_MAGIC_CATALOG_FILE = "magic_templates.json"
_MAGIC_LIST_LIMIT = 30

_MAGIC_EXPRESSIONS: list[tuple[str, str]] = [
    ("摇手手", "<$ÿĀ\x11\x10>"),
    ("四叶草🍀", "<$ÿĀB >"),
    ("小爪爪", "<$ǿĀC\x01>"),
    ("升级版小爪爪", "<$ÿĀ\x11\">"),
    ("小心心", "<$ǿĀF\x13>"),
    ("小熊熊", "<$ÿĀD#>"),
    ("魔法棒🪄", "<$ǿĀB#>"),
]

_MAGIC_NAME_MAP: dict[str, str] = dict(_MAGIC_EXPRESSIONS)


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
    (``fake``), real ``@`` mentions (``at``), contact cards (``card``), quoted
    message reactions (``face``), hidden face sending (``hface``) and an LLM
    ``at_user`` tool that prepends an ``@`` to the bot's reply.
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        self._face_stop: set[str] = set()
        self._magic_learned: list[str] = []
        try:
            self._magic_file = (
                Path(StarTools.get_data_dir("astrbot_plugin_qmessageq"))
                / _MAGIC_CATALOG_FILE
            )
        except Exception as exc:
            logger.warning("magic: no data dir: %s", exc)
            self._magic_file = None
        self._load_magic_learned()

    def _magic_items(self) -> list[tuple[str | None, str]]:
        """Known + learned magic expressions as ``(name, template)`` pairs."""
        items = list(_MAGIC_EXPRESSIONS)
        items.extend((None, t) for t in self._magic_learned)
        return items

    def _load_magic_learned(self) -> None:
        if self._magic_file is None:
            return
        try:
            data = json.loads(self._magic_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            self._magic_learned = []
            return
        if isinstance(data, list):
            self._magic_learned = [
                str(t) for t in data if isinstance(t, str) and _MAGIC_RE.fullmatch(t)
            ]

    def _save_magic_learned(self) -> None:
        if self._magic_file is None:
            return
        try:
            self._magic_file.write_text(
                json.dumps(self._magic_learned, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("magic: failed to save catalog: %s", exc)

    def _learn_magic_templates(self, templates: list[str]) -> None:
        changed = False
        known = {t for _, t in _MAGIC_EXPRESSIONS}
        for t in templates:
            if t in known or t in self._magic_learned:
                continue
            self._magic_learned.append(t)
            changed = True
        if changed:
            self._save_magic_learned()

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

        Each command gates on its own ``<name>_admin_only`` config, falling back
        to the global ``admin_only`` when the specific key is not set.
        """
        restricted = self.config.get(key, self.config.get("admin_only", True))
        return not restricted or event.is_admin()

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
            yield event.plain_result("Permission denied: this command is admin-only.")
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
            yield event.plain_result("Permission denied: this command is admin-only.")
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
        chain: list = [Comp.Face(id=face_id) for face_id in face_ids]
        text = " ".join(tokens[1:]).strip()
        if text:
            chain.append(Comp.Plain(text))
        yield event.chain_result(chain)

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
            else:
                content_lines.append(f"[{getattr(seg, 'type', '?')}]")

    @filter.command("parse")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def parse(self, event: AstrMessageEvent):
        """Parse a quoted message: reactions, face ids, image summaries, forward details.

        Usage: reply to a message, then run ``/parse``. Sends a merge-forward
        message with one node per section: basic info, time, content (text /
        face ids / mentions), image summaries, emoji reactions and, for a
        merge-forward message, its node list. Content is read from the reply's
        own chain when the message can no longer be fetched (sender/time still
        come from the reply itself).
        """
        if not self._check_permission(event, "parse_admin_only"):
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
        message = info.get("message") if info else None
        if isinstance(message, list):
            self._collect_dict_segments(content_lines, forward_ids, message)
        else:
            self._collect_chain(content_lines, forward_ids, reply.chain)
        if content_lines:
            section("内容", "\n".join(content_lines))

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

        QQ renders ``<$...>`` templates embedded in text as magic emoji. The
        catalog is auto-learned from real messages (exact bytes, including
        invisible characters). Usage: ``/magic list`` shows the catalog in a
        forward message; ``/magic <编号>`` sends the matching entry; ``/magic
        <模板>`` sends an arbitrary template and learns it; reply to a message
        containing magic expressions to re-send and learn them.
        """
        if not self._check_permission(event, "magic_admin_only"):
            yield event.plain_result("Permission denied: this command is admin-only.")
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
            self._learn_magic_templates(templates)
            yield event.chain_result([Comp.Plain("".join(templates))])
            return

        arg = expr.strip()
        if not arg:
            yield event.plain_result(
                "Missing content: run `/magic <名称|编号>`, `/magic list`, "
                "`/magic <模板>`, or reply to a message.",
            )
            return
        if arg == "list":
            await self._send_magic_list(event)
            event.should_call_llm(True)
            return
        items = self._magic_items()
        if arg.isdigit() and 1 <= int(arg) <= len(items):
            yield event.chain_result([Comp.Plain(items[int(arg) - 1][1])])
            return
        if arg in _MAGIC_NAME_MAP:
            yield event.chain_result([Comp.Plain(_MAGIC_NAME_MAP[arg])])
            return
        if _MAGIC_RE.search(arg):
            self._learn_magic_templates([arg])
            yield event.chain_result([Comp.Plain(arg)])
            return
        yield event.plain_result(
            f"未知的魔法表情 '{arg}'：用 `/magic list` 查看目录，或直接发 `<$...>` 模板。",
        )

    async def _send_magic_list(self, event: AstrMessageEvent) -> None:
        """Send all known magic expressions as a forward message."""
        items = self._magic_items()
        if not items:
            yield event.plain_result("魔法表情目录为空。")
            return
        self_uin = str(getattr(event.message_obj, "self_id", None) or "10001")
        nodes = []
        for i, (name, template) in enumerate(items[:_MAGIC_LIST_LIMIT], start=1):
            label = f"#{i} {name}" if name else f"#{i}"
            nodes.append(
                Node(content=[Comp.Plain(template)], name=label, uin=self_uin)
            )
        if len(items) > _MAGIC_LIST_LIMIT:
            nodes.append(
                Node(
                    content=[Comp.Plain(f"... 其余 {len(items) - _MAGIC_LIST_LIMIT} 个省略")],
                    name="QmessageQ",
                    uin=self_uin,
                ),
            )
        if not await self._send_forward_msg(event, nodes):
            yield event.plain_result("Failed to send the magic expression list.")

    @filter.command("card")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def card(self, event: AstrMessageEvent, contact_type: str, contact_id: str):
        """Send a real contact card (recommend friend/group).

        Usage: ``/card qq <QQ号>`` sends that user's business card,
        ``/card group <群号>`` sends the group's card. NapCat builds the card
        from the real contact's profile, so the ID must exist.
        """
        if not self._check_permission(event, "card_admin_only"):
            yield event.plain_result("Permission denied: this command is admin-only.")
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
