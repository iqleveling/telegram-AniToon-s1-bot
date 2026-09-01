"""Inline keyboards kept separate from handler logic."""

from __future__ import annotations

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb(rows):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
         for row in rows]
    )


def main_menu(owner: bool = False):
    rows = [
        [("➕ Add Channel", "add"), ("📢 My Channels", "channels")],
        [("❤️ Auto Reactions", "reaction_channels")],
        [("⚙️ Bot Settings", "settings")],
        [("ℹ️ Bot Info", "about"), ("❓ Help", "help")],
    ]
    if owner:
        rows.insert(3, [("👑 Bot Creator Settings", "owner")])
    return kb(rows)


def back(data="main"):
    return kb([[("⬅️ Back", data)]])


def add_channel():
    # Removed "Find / Select Channel" entry and updated labels to match admin flow
    return kb([
        [("➕ Add Channel or Chat", "add_username")],
        [("🔢 Add Using Chat ID", "add_id")],
        [("❓ How To Add Bot (Admin)", "add_how")],
        [("⬅️ Back", "main")],
    ])


def channel_settings(chat_id: int, enabled: bool):
    prefix = str(chat_id)
    return kb([
        [("👥 Join Requests", f"join:{prefix}"),
         ("⏱️ Approval Timing", f"timing:{prefix}")],
        [("❤️ Auto Reactions", f"reaction:{prefix}"),
         ("⏱️ Reaction Delay", f"rdelay:{prefix}")],
        [("💬 Welcome Message", f"welcome:{prefix}"),
         ("🎯 Target Types", f"targets:{prefix}")],
        [("🔴 Disable" if enabled else "🟢 Enable", f"toggle:{prefix}")],
        [("🗑️ Remove Channel", f"remove:{prefix}")],
        [("⬅️ Back", "channels")],
    ])


def join_menu(chat_id: int, mode: str):
    mark = {"auto": "🟢", "delayed": "⏱️", "decline": "🔴", "manual": "🟡"}
    return kb([
        [(f"Current: {mark.get(mode, '🟡')} {mode.upper()}", f"noop:{chat_id}")],
        [("🟢 Auto Accept", f"joinmode:{chat_id}:auto"),
         ("⏱️ Delayed Accept", f"joinmode:{chat_id}:delayed")],
        [("🔴 Auto Decline", f"joinmode:{chat_id}:decline"),
         ("🟡 Manual", f"joinmode:{chat_id}:manual")],
        [("⬅️ Back", f"channel:{chat_id}")],
    ])


def timing_menu(chat_id: int, reaction: bool = False):
    prefix = "rsetdelay" if reaction else "setdelay"
    if reaction:
        values = [0, 2, 5, 10, 30]
        labels = ["⚡ Immediately", "⏱️ 2 Seconds", "⏱️ 5 Seconds",
                  "⏱️ 10 Seconds", "⏱️ 30 Seconds"]
    else:
        values = [0, 5, 10, 30, 60, 300]
        labels = ["⚡ Immediately", "⏱️ 5 Seconds", "⏱️ 10 Seconds",
                  "⏱️ 30 Seconds", "⏱️ 1 Minute", "⏱️ 5 Minutes"]
    rows = [[(label, f"{prefix}:{chat_id}:{value}")]
            for label, value in zip(labels, values)]
    rows.append([("⚙️ Custom", f"{prefix}_custom:{chat_id}")])
    rows.append([("⬅️ Back", f"channel:{chat_id}" if not reaction
                 else f"reaction:{chat_id}")])
    return kb(rows)


def reaction_menu(chat_id: int, enabled: bool):
    return kb([
        [("➕ Add Emoji", f"radd:{chat_id}"),
         ("✏️ Edit Emoji", f"redit:{chat_id}")],
        [("❌ Remove Emoji", f"rremove:{chat_id}"),
         ("🔄 Reset", f"rreset:{chat_id}")],
        [("⏱️ Reaction Delay", f"rdelay:{chat_id}"),
         ("🎯 Target Types", f"targets:{chat_id}")],
        [("📋 Preview", f"preview:{chat_id}")],
        [("🔴 Disable" if enabled else "🟢 Enable", f"rtoggle:{chat_id}")],
        [("⬅️ Back", f"channel:{chat_id}")],
    ])


def welcome_menu(chat_id: int, enabled: bool):
    return kb([
        [("✅ Enable", f"welcomestate:{chat_id}:1"),
         ("❌ Disable", f"welcomestate:{chat_id}:0")],
        [("✏️ Edit Message", f"welcomeedit:{chat_id}"),
         ("👀 Preview", f"welcomepreview:{chat_id}")],
        [("⬅️ Back", f"channel:{chat_id}")],
    ])


def target_menu(chat_id: int, targets: list[str]):
    rows = []
    for key, label in (("text", "📝 Text Messages"), ("photo", "🖼️ Photo Messages"),
                       ("video", "🎥 Video Messages"), ("document", "📄 Document Messages"),
                       ("audio", "🎵 Audio Messages"), ("all", "✅ All Supported Types")):
        mark = "✅" if key in targets else "▫️"
        rows.append([(f"{mark} {label}", f"target:{chat_id}:{key}")])
    rows.append([("⬅️ Back", f"reaction:{chat_id}")])
    return kb(rows)


def owner_menu():
    return kb([
        [("📢 Force Subscribe", "force"), ("⚙️ Bot Configuration", "config")],
        [("👥 Bot Admins", "admins"), ("📣 Broadcast", "broadcast")],
        [("🛠️ Maintenance", "maintenance"), ("🔐 Security", "security")],
        [("📊 Bot Status", "botstatus"), ("🗄️ Database Status", "dbstatus")],
        [("🔑 Secrets Status", "secrets"), ("⬅️ Back", "main")],
    ])


def permissions_menu(chat_id: int, enabled: bool):
    """Menu for bot permissions/capabilities."""
    return kb([
        [("✅ Change Channel Info", f"perm:{chat_id}:change_info"),
         ("✅ Manage Messages", f"perm:{chat_id}:manage_messages")],
        [("✅ Manage Stories", f"perm:{chat_id}:manage_stories"),
         ("✅ Direct Messages", f"perm:{chat_id}:direct_messages")],
        [("✅ Invite Users via Link", f"perm:{chat_id}:invite_users"),
         ("✅ Manage Live Streams", f"perm:{chat_id}:live_streams")],
        [("✅ Add New Admins", f"perm:{chat_id}:add_admins"),
         ("✅ Ban Users", f"perm:{chat_id}:ban_users")],
        [("⬅️ Back", f"channel:{chat_id}")],
    ])
