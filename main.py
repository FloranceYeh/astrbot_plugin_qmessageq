from __future__ import annotations

import asyncio
import random
import re
from typing import Any

import astrbot.api.message_components as Comp
from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Node
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

from .faces import emoji_codepoint, hidden_faces, is_emoji_text, resolve_face_ids

MAX_FACES_PER_CALL = 20


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
        (``14``), a ``#``-prefixed numeric id, a numeric range ``a-b``, or an
        emoji. An emoji reacts with the literal emoji itself; a name/number
        reacts with the matching QQ face. Append ``cancel`` to remove the
        reaction, ``big`` to send a big emoji.

        A range is reacted to one by one, spaced by the configurable
        ``face_interval``; the bot first reports the expected duration and
        afterwards a summary when any reaction failed.

        NapCat uses ``set_msg_emoji_like``; other OneBot implementations that
        only provide ``set_msg_reaction`` are handled automatically.

        Examples:
            ``/face 微笑`` reacts with the smiling QQ face.
            ``/face 💢`` reacts with the literal 💢 emoji.
            ``/face 66-70`` reacts with faces 66 to 70, one per interval.
            ``/face 爱心 cancel`` removes the bot's heart reaction.
        """
        if not self._check_permission(event, "face_admin_only"):
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
        cancel = "cancel" in tokens
        is_big = "big" in tokens
        head = tokens[0]
        if head in ("cancel", "big"):
            yield event.plain_result(
                f"Missing face: specify the emoji first, e.g. `/face 爱心 {head}`.",
            )
            return
        if is_emoji_text(head):
            used = await self._apply_reaction(
                event,
                message_id=int(reply.id),
                emoji=str(emoji_codepoint(head)),
                set_=not cancel,
                is_big=is_big,
            )
            if used is None:
                yield event.plain_result(
                    "Failed to react with the literal emoji: the protocol "
                    "client does not support it.",
                )
                return
            event.should_call_llm(True)
            return
        face_ids = resolve_face_ids(head)
        if not face_ids:
            yield event.plain_result(
                f"Unknown face '{head}': use a face name, an English alias, "
                "a numeric id or a range `a-b`.",
            )
            return
        if len(face_ids) > MAX_FACES_PER_CALL:
            yield event.plain_result(
                f"Too many faces: at most {MAX_FACES_PER_CALL} per call.",
            )
            return

        if len(face_ids) == 1:
            used = await self._apply_reaction(
                event,
                message_id=int(reply.id),
                emoji=str(face_ids[0]),
                set_=not cancel,
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
        await self._send_direct(
            event,
            [{"type": "text", "data": {"text": (
                f"将依次回应 {len(face_ids)} 个表情，每个间隔 {interval:g} 秒，"
                f"预计约 {len(face_ids) * interval:g} 秒完成。"
            )}}],
        )
        failures = []
        for idx, face_id in enumerate(face_ids):
            used = await self._apply_reaction(
                event,
                message_id=int(reply.id),
                emoji=str(face_id),
                set_=not cancel,
                is_big=is_big,
            )
            if used is None:
                failures.append(face_id)
            if idx < len(face_ids) - 1 and interval > 0:
                await asyncio.sleep(interval)
        if failures:
            await self._send_direct(
                event,
                [{"type": "text", "data": {"text": (
                    f"有 {len(failures)} 个表情回应失败："
                    + " ".join(str(f) for f in failures)
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
        is sent literally as text (e.g. ``/hface 💢``); ``/hface list`` sends
        all hidden faces as a forward message; ``/hface text <内容>`` sends
        the content literally. ``<名称|ID>`` accepts a face name, an English
        alias, a raw numeric id, a ``#``-prefixed id, or a numeric range
        ``a-b``. Face ids follow NapCat's built-in table (see FACES.md).
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
            self_uin = str(
                getattr(event.message_obj, "self_id", None) or "10001"
            )
            nodes = [
                Node(content=[Comp.Face(id=qsid)], name=name, uin=self_uin)
                for qsid, name in hidden_faces()
            ]
            if not await self._send_forward_msg(event, nodes):
                yield event.plain_result("Failed to send the hidden face list.")
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
        if len(face_ids) > MAX_FACES_PER_CALL:
            yield event.plain_result(
                f"Too many faces: at most {MAX_FACES_PER_CALL} per call.",
            )
            return
        chain: list = [Comp.Face(id=face_id) for face_id in face_ids]
        text = " ".join(tokens[1:]).strip()
        if text:
            chain.append(Comp.Plain(text))
        yield event.chain_result(chain)

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
