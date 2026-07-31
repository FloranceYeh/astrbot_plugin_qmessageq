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


_AVATAR_SUFFIX_RE = re.compile(r"(?:^|\s)头像=(\S+)$")


def split_avatar_marker(text: str) -> tuple[str, str]:
    """Strip a trailing ``头像=<url>`` marker from a node's text.

    Args:
        text: The raw node text, e.g. ``"你好 头像=https://..."``.

    Returns:
        A ``(text, avatar_url)`` pair with the marker removed from ``text``.
    """
    match = _AVATAR_SUFFIX_RE.search(text)
    if match:
        return text[: match.start()].strip(), match.group(1)
    return text, ""


class AvatarNode(Node):
    """A forward-message node with an optional custom avatar URL.

    NapCat / LLOneBot render the extra ``avatar`` node field as the sender's
    avatar inside merged forward messages.
    """

    avatar: str = ""

    def __init__(self, content: list, avatar: str = "", **kwargs) -> None:
        super().__init__(content=content, avatar=avatar, **kwargs)

    async def to_dict(self) -> dict:
        data = await super().to_dict()
        if self.avatar:
            data["data"]["avatar"] = self.avatar
        return data


class QmessageQToolbox(Star):
    """Multi-function QQ message toolbox for aiocqhttp (NapCat/OneBot).

    Provides image summary steganography (``himg``), forged forward messages
    (``fake``), real ``@`` mentions (``at``) and an LLM ``at_user`` tool that
    prepends an ``@`` to the bot's reply.
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

    @filter.command("fake")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def fake(
        self, event: AstrMessageEvent, name: str, uin: str, content: GreedyStr
    ):
        """Send a merge-forward message with forged senders.

        Usage: ``/fake <昵称> <QQ> <文本>`` for a single node, or chain multiple
        nodes with ``||`` to fake a whole chat log::

            /fake 张三 10001 第一条 || 李四 10002 第二条 || 张三 10001 第三条

        Append ``头像=<url>`` to a node's text to override its sender avatar.
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
        nodes: list = []

        first_content: list = []
        if image is not None:
            first_content.append(image)
        if first_text:
            first_content.append(Comp.Plain(first_text))
        if not first_content:
            yield event.plain_result("Nothing to forward: add text or an image.")
            return
        nodes.append(
            AvatarNode(
                content=first_content, name=name, uin=uin, avatar=first_avatar
            )
        )

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
            node_content: list = []
            if n_text.strip():
                node_content.append(Comp.Plain(n_text))
            if not node_content:
                yield event.plain_result("Nothing to forward: add text to each node.")
                return
            nodes.append(
                AvatarNode(
                    content=node_content, name=n_name, uin=n_uin, avatar=n_avatar
                )
            )

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
