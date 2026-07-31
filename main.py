from __future__ import annotations

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


class QmessageQToolbox(Star):
    """Multi-function QQ message toolbox for aiocqhttp (NapCat/OneBot).

    Provides image summary steganography (``himg``), forged forward messages
    (``fake``), real/pseudo ``@`` mentions (``at``/``fakeat``) and an LLM
    ``at_user`` tool that prepends an ``@`` to the bot's reply.
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}

    def _check_permission(self, event: AstrMessageEvent) -> bool:
        """Whether the event sender is allowed to use admin-only commands."""
        return not self.config.get("admin_only", True) or event.is_admin()

    def _check_llm_tool_permission(self, event: AstrMessageEvent) -> bool:
        """Whether the event sender is allowed to use the admin-only LLM tool."""
        return not self.config.get("llm_tool_admin_only", True) or event.is_admin()

    @filter.command("himg")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def himg(self, event: AstrMessageEvent, text: GreedyStr):
        """Hide a text into an image's summary field and resend the image.

        Usage: ``/himg <text>`` with an attached image, or
        ``/himg <text> <image_url>``.
        """
        if not self._check_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return

        image = next(
            (seg for seg in event.get_messages() if isinstance(seg, Image)),
            None,
        )
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
        """Send a merge-forward message with a forged sender.

        Usage: ``/fake <nickname> <qq> <text>``, optionally with an attached
        image which is forwarded as sent by the forged user.
        """
        if not self._check_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return
        if not uin.isdigit():
            yield event.plain_result("Invalid QQ number.")
            return

        node_content: list = []
        image = next(
            (seg for seg in event.get_messages() if isinstance(seg, Image)),
            None,
        )
        if image is not None:
            node_content.append(image)
        if content.strip():
            node_content.append(Comp.Plain(content))
        if not node_content:
            yield event.plain_result("Nothing to forward: add text or an image.")
            return

        node = Node(content=node_content, name=name, uin=uin)
        yield event.chain_result([node])

    @filter.command("at")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def at(self, event: AstrMessageEvent, qq: str, text: GreedyStr):
        """Send a real @ mention (or @everyone) followed by optional text.

        Usage: ``/at <qq|all> <text>``.
        """
        if not self._check_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return

        chain: list = []
        if qq == "all":
            if not event.get_group_id():
                yield event.plain_result("@everyone is only valid in group chats.")
                return
            chain.append(Comp.AtAll())
        elif qq.isdigit():
            chain.append(Comp.At(qq=qq))
        else:
            yield event.plain_result("Invalid qq: use a QQ number or 'all'.")
            return
        if text.strip():
            chain.append(Comp.Plain(text))
        yield event.chain_result(chain)

    @filter.command("fakeat")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def fakeat(self, event: AstrMessageEvent, name: str, text: GreedyStr):
        """Send a pseudo @ mention that renders like an @ but notifies nobody.

        Usage: ``/fakeat <nickname> <text>``. Uses an ``at`` segment pointing to
        a non-existent QQ number (0); rendering may vary by client.
        """
        if not self._check_permission(event):
            yield event.plain_result("Permission denied: this command is admin-only.")
            return
        chain = [Comp.At(qq="0"), Comp.Plain(f"{name} {text}".strip())]
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
        if not self._check_llm_tool_permission(event):
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
