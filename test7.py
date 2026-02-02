import os
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Any, Optional, Union
from telethon import TelegramClient, events
from telethon.tl.functions.channels import GetParticipantRequest, GetChannelsRequest
from telethon.tl.functions.messages import GetDialogsRequest, GetMessagesRequest
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import (
    Channel, Chat, User, InputPeerChannel, InputMediaUploadedPhoto,
    InputMediaPhoto, InputPhoto, InputDocument, Document, Photo
)
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage,
    MessageMediaContact, MessageMediaGeo, MessageMediaVenue,
    MessageMediaGame, MessageMediaInvoice, MessageMediaPoll
)
from telethon.tl.types import MessageService, MessageActionChatAddUser
from telethon.tl.types import DocumentAttributeSticker, DocumentAttributeCustomEmoji
from telethon.errors import UserNotParticipantError, FloodWaitError, ChannelPrivateError, ChatWriteForbiddenError, ChatAdminRequiredError
from telegram import Update, Bot as TgBot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from dotenv import load_dotenv

# Discord integration imports
import aiohttp
import requests
import discord
from discord import Webhook, SyncWebhook

load_dotenv()

# ========= YOUR DETAILS =========
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ========= DISCORD CONFIGURATION =========
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # Optional: for webhook method

# ========= GLOBAL DATA =========
client = TelegramClient("session", api_id, api_hash)
tg_bot = TgBot(BOT_TOKEN)
user_settings = {}
blacklist = ["contact", "message", "admin","vip","client","feedback",'account','management',"equity","premium"]

# Discord routes management
discord_routes = {}  # Format: {user_id: {source_channel: [discord_channel_ids]}}

# Enhanced media support configuration
SUPPORTED_MEDIA_TYPES = {
    "photo": ["jpg", "jpeg", "png", "gif", "bmp", "webp"],
    "video": ["mp4", "mov", "avi", "mkv", "webm"],
    "audio": ["mp3", "m4a", "ogg", "wav", "flac"],
    "document": ["pdf", "doc", "docx", "xls", "xlsx", "txt", "csv", "json"],
    "sticker": ["tgs", "webp", "png", "jpg", "jpeg", "gif","webm"],
}

# Media type display names
MEDIA_TYPE_DISPLAY_NAMES = {
    "photo": "🖼️ Photos",
    "video": "🎥 Videos", 
    "audio": "🎵 Audio",
    "document": "📄 Documents",
    "sticker": "⭐ Stickers",
    "text": "📝 Text Messages"
}

# File paths for logging and persistence
ERRORS_LOG = "errors.txt"
ACTIVITIES_LOG = "activities.txt"
MEDIA_LOG = "media_forwarding.txt"
SETTINGS_FILE = "user_settings.json"
MESSAGE_TRACKING_FILE = "message_tracking.json"
DISCORD_ROUTES_FILE = "discord_routes.json"
MESSAGE_MAPPINGS_FILE = "message_mappings.json"  # SQLite database for message mappings
DISCORD_MESSAGE_MAPPINGS_FILE = "discord_message_mappings.json"  # NEW: For Discord message tracking

# Media statistics
media_forwarding_stats = {}

# Discord integration states
discord_route_states = {}

# ========= ENHANCED ROUTE MANAGEMENT STATES =========
route_creation_states = {}
route_management_states = {}
manual_route_states = {}
channel_selection_states = {}
channel_management_states = {}
keyword_management_states = {}
media_filter_states = {}


# ========= ENHANCED MESSAGE MAPPING SYSTEM (JSON) =========

def setup_message_mappings_file():
    """Initialize JSON file for message mappings"""
    try:
        if not os.path.exists(MESSAGE_MAPPINGS_FILE):
            with open(MESSAGE_MAPPINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
            log_activity("Message mappings JSON file initialized")
        else:
            log_activity("Message mappings JSON file already exists")
    except Exception as e:
        log_error("Failed to initialize message mappings JSON file", e)

def load_message_mappings() -> Dict:
    """Load message mappings from JSON file"""
    try:
        if os.path.exists(MESSAGE_MAPPINGS_FILE):
            with open(MESSAGE_MAPPINGS_FILE, "r", encoding="utf-8") as f:
                mappings = json.load(f)
            return mappings
        else:
            return {}
    except Exception as e:
        log_error(f"Failed to load message mappings from {MESSAGE_MAPPINGS_FILE}", e)
        return {}

def save_message_mappings(mappings: Dict) -> None:
    """Save message mappings to JSON file"""
    try:
        with open(MESSAGE_MAPPINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(mappings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_error(f"Failed to save message mappings to {MESSAGE_MAPPINGS_FILE}", e)

def update_message_mapping(user_id_str: str, source_chat_id: int, source_message_id: int, 
                          destination_chat_key: str, destination_message_id: int) -> None:
    """Update message mapping for tracking source->destination messages using JSON"""
    try:
        mappings = load_message_mappings()
        
        # Initialize user structure if not exists
        if user_id_str not in mappings:
            mappings[user_id_str] = {}
        
        # Initialize source chat structure if not exists
        source_chat_str = str(source_chat_id)
        if source_chat_str not in mappings[user_id_str]:
            mappings[user_id_str][source_chat_str] = {}
        
        # Initialize source message structure if not exists
        source_msg_str = str(source_message_id)
        if source_msg_str not in mappings[user_id_str][source_chat_str]:
            mappings[user_id_str][source_chat_str][source_msg_str] = {}
        
        # Update the destination mapping
        mappings[user_id_str][source_chat_str][source_msg_str][destination_chat_key] = destination_message_id
        
        save_message_mappings(mappings)
        
        log_activity(f"Message mapping updated: user {user_id_str}, source {source_chat_id}:{source_message_id} -> dest {destination_chat_key}:{destination_message_id}")
        
    except Exception as e:
        log_error(f"Error updating message mapping for user {user_id_str}", e)

def get_destination_message_ids(user_id_str: str, source_chat_id: int, source_message_id: int) -> Dict[str, int]:
    """Get all destination message IDs for a source message from JSON"""
    try:
        mappings = load_message_mappings()
        
        source_chat_str = str(source_chat_id)
        source_msg_str = str(source_message_id)
        
        # Navigate through the nested structure
        user_mappings = mappings.get(user_id_str, {})
        chat_mappings = user_mappings.get(source_chat_str, {})
        message_mappings = chat_mappings.get(source_msg_str, {})
        
        return message_mappings
        
    except Exception as e:
        log_error(f"Error getting destination message IDs for user {user_id_str}", e)
        return {}

def remove_message_mapping(user_id_str: str, source_chat_id: int, source_message_id: int) -> None:
    """Remove message mapping when source message is deleted"""
    try:
        mappings = load_message_mappings()
        
        source_chat_str = str(source_chat_id)
        source_msg_str = str(source_message_id)
        
        # Check if the mapping exists
        if (user_id_str in mappings and 
            source_chat_str in mappings[user_id_str] and 
            source_msg_str in mappings[user_id_str][source_chat_str]):
            
            # Remove the specific message mapping
            del mappings[user_id_str][source_chat_str][source_msg_str]
            
            # Clean up empty structures
            if not mappings[user_id_str][source_chat_str]:
                del mappings[user_id_str][source_chat_str]
            if not mappings[user_id_str]:
                del mappings[user_id_str]
            
            save_message_mappings(mappings)
            
            log_activity(f"Message mapping removed: user {user_id_str}, source {source_chat_id}:{source_message_id}")
            
    except Exception as e:
        log_error(f"Error removing message mapping for user {user_id_str}", e)

# ========= DISCORD MESSAGE MAPPING SYSTEM =========

def setup_discord_message_mappings_file():
    """Initialize JSON file for Discord message mappings"""
    try:
        if not os.path.exists(DISCORD_MESSAGE_MAPPINGS_FILE):
            with open(DISCORD_MESSAGE_MAPPINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
            log_activity("Discord message mappings JSON file initialized")
        else:
            log_activity("Discord message mappings JSON file already exists")
    except Exception as e:
        log_error("Failed to initialize Discord message mappings JSON file", e)

def load_discord_message_mappings() -> Dict:
    """Load Discord message mappings from JSON file"""
    try:
        if os.path.exists(DISCORD_MESSAGE_MAPPINGS_FILE):
            with open(DISCORD_MESSAGE_MAPPINGS_FILE, "r", encoding="utf-8") as f:
                mappings = json.load(f)
            return mappings
        else:
            return {}
    except Exception as e:
        log_error(f"Failed to load Discord message mappings from {DISCORD_MESSAGE_MAPPINGS_FILE}", e)
        return {}

def save_discord_message_mappings(mappings: Dict) -> None:
    """Save Discord message mappings to JSON file"""
    try:
        with open(DISCORD_MESSAGE_MAPPINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(mappings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_error(f"Failed to save Discord message mappings to {DISCORD_MESSAGE_MAPPINGS_FILE}", e)

def update_discord_message_mapping(user_id_str: str, source_chat_id: int, source_message_id: int, 
                                  discord_channel_id: str, discord_message_id: str) -> None:
    """Update Discord message mapping for tracking source->Discord messages"""
    try:
        mappings = load_discord_message_mappings()
        
        # Initialize user structure if not exists
        if user_id_str not in mappings:
            mappings[user_id_str] = {}
        
        # Initialize source chat structure if not exists
        source_chat_str = str(source_chat_id)
        if source_chat_str not in mappings[user_id_str]:
            mappings[user_id_str][source_chat_str] = {}
        
        # Initialize source message structure if not exists
        source_msg_str = str(source_message_id)
        if source_msg_str not in mappings[user_id_str][source_chat_str]:
            mappings[user_id_str][source_chat_str][source_msg_str] = {}
        
        # Update the Discord mapping
        mappings[user_id_str][source_chat_str][source_msg_str][discord_channel_id] = discord_message_id
        
        save_discord_message_mappings(mappings)
        
        log_activity(f"Discord message mapping updated: user {user_id_str}, source {source_chat_id}:{source_message_id} -> Discord {discord_channel_id}:{discord_message_id}")
        
    except Exception as e:
        log_error(f"Error updating Discord message mapping for user {user_id_str}", e)

def get_discord_message_mappings(user_id_str: str, source_chat_id: int, source_message_id: int) -> Dict[str, str]:
    """Get all Discord message mappings for a source message"""
    try:
        mappings = load_discord_message_mappings()
        
        source_chat_str = str(source_chat_id)
        source_msg_str = str(source_message_id)
        
        # Navigate through the nested structure
        user_mappings = mappings.get(user_id_str, {})
        chat_mappings = user_mappings.get(source_chat_str, {})
        message_mappings = chat_mappings.get(source_msg_str, {})
        
        return message_mappings
        
    except Exception as e:
        log_error(f"Error getting Discord message mappings for user {user_id_str}", e)
        return {}

def remove_discord_message_mapping(user_id_str: str, source_chat_id: int, source_message_id: int) -> None:
    """Remove Discord message mapping when source message is deleted"""
    try:
        mappings = load_discord_message_mappings()
        
        source_chat_str = str(source_chat_id)
        source_msg_str = str(source_message_id)
        
        # Check if the mapping exists
        if (user_id_str in mappings and 
            source_chat_str in mappings[user_id_str] and 
            source_msg_str in mappings[user_id_str][source_chat_str]):
            
            # Remove the specific message mapping
            del mappings[user_id_str][source_chat_str][source_msg_str]
            
            # Clean up empty structures
            if not mappings[user_id_str][source_chat_str]:
                del mappings[user_id_str][source_chat_str]
            if not mappings[user_id_str]:
                del mappings[user_id_str]
            
            save_discord_message_mappings(mappings)
            
            log_activity(f"Discord message mapping removed: user {user_id_str}, source {source_chat_id}:{source_message_id}")
            
    except Exception as e:
        log_error(f"Error removing Discord message mapping for user {user_id_str}", e)

# ========= PERFORMANCE MONITORING SYSTEM =========
class DeletionPerformance:
    """Track deletion performance metrics"""
    def __init__(self):
        self.deletion_times = []
        self.success_count = 0
        self.error_count = 0
        self.permission_errors = 0
    
    def record_deletion(self, success: bool, deletion_time: float, is_permission_error: bool = False):
        """Record deletion performance"""
        self.deletion_times.append(deletion_time)
        if success:
            self.success_count += 1
        else:
            self.error_count += 1
            if is_permission_error:
                self.permission_errors += 1
    
    def get_stats(self) -> Dict:
        """Get performance statistics"""
        if not self.deletion_times:
            return {}
        
        return {
            "total_deletions": len(self.deletion_times),
            "success_rate": (self.success_count / len(self.deletion_times)) * 100,
            "average_time": sum(self.deletion_times) / len(self.deletion_times),
            "min_time": min(self.deletion_times),
            "max_time": max(self.deletion_times),
            "permission_errors": self.permission_errors,
            "other_errors": self.error_count - self.permission_errors
        }

# Global performance tracker
deletion_performance = DeletionPerformance()

# ========= DISCORD MESSAGE EDIT HANDLER =========
async def edit_discord_message(discord_channel_id: str, discord_message_id: str, new_text: str) -> bool:
    """Edit an existing Discord message"""
    if not DISCORD_TOKEN:
        log_error("No Discord token configured for message editing", None)
        return False

    url = f"https://discord.com/api/v10/channels/{discord_channel_id}/messages/{discord_message_id}"

    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json"
    }

    # Truncate message if too long for Discord
    if len(new_text) > 2000:
        new_text = new_text[:1997] + "..."

    payload = {
        "content": new_text
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    log_activity(f"Discord message edited successfully: {discord_channel_id}/{discord_message_id}")
                    return True
                else:
                    text = await resp.text()
                    log_error(f"Discord message edit failed {resp.status}", text)
                    return False
    except Exception as e:
        log_error("Discord edit error", e)
        return False

async def update_discord_messages(user_id_str: str, source_chat_id: int, source_message_id: int, 
                                 new_text: str, discord_messages: Dict[str, str]) -> None:
    """Update corresponding Discord messages when source message is edited"""
    try:
        if not discord_messages:
            return
        
        updated_count = 0
        error_count = 0
        
        # Create edit tasks for ALL Discord destinations in parallel
        edit_tasks = []
        
        for discord_channel_id, discord_message_id in discord_messages.items():
            task = asyncio.create_task(
                edit_discord_message(discord_channel_id, discord_message_id, new_text)
            )
            edit_tasks.append(task)
        
        # Wait for all edits to complete
        results = await asyncio.gather(*edit_tasks, return_exceptions=True)
        
        # Count results
        for result in results:
            if isinstance(result, Exception):
                error_count += 1
            elif result is True:
                updated_count += 1
            else:
                error_count += 1
        
        log_activity(f"📊 Discord edits: User {user_id_str}: Updated {updated_count}/{len(discord_messages)} Discord messages (Errors: {error_count})")
        
    except Exception as e:
        log_error(f"Error in update_discord_messages for user {user_id_str}", e)

# ========= ENHANCED MESSAGE EDIT HANDLER WITH DISCORD SUPPORT =========
@client.on(events.MessageEdited)
async def handle_message_edited(event):
    """Handle message edits and update corresponding messages in target channels (Telegram and Discord)"""
    try:
        edit_start_time = time.time()
        
        # Get the chat where edit occurred
        try:
            chat = await event.get_chat()
        except Exception as e:
            log_error("Could not get chat from edit event", e)
            return
            
        edited_message_id = event.message.id
        new_text = event.message.message or ""
        
        log_activity(f"✏️ MESSAGE EDIT: Channel {getattr(chat, 'id', 'unknown')} - Message ID: {edited_message_id}")
        
        # Refresh settings to get current user configurations
        fresh_settings = load_settings()
        discord_routes = load_discord_routes()
        
        # Process for each user CONCURRENTLY for faster processing
        user_tasks = []
        
        # Process Discord routes
        for user_id_str, routes in discord_routes.items():
            try:
                # Check if this chat is a source channel for any of the user's Discord routes
                for source_channel, targets in routes.items():
                    if stored_value_matches_chat(source_channel, chat):
                        # Get Discord message mappings for this source message
                        discord_messages = get_discord_message_mappings(user_id_str, chat.id, edited_message_id)
                        
                        if discord_messages:
                            # Create concurrent edit tasks for Discord
                            task = asyncio.create_task(
                                update_discord_messages(
                                    user_id_str, chat.id, edited_message_id, new_text, discord_messages
                                )
                            )
                            user_tasks.append(task)
                        break
            except Exception as e:
                log_error(f"Error processing Discord message edit for user {user_id_str}", e)
        
        # Process Telegram routes
        for user_id_str, settings in fresh_settings.items():
            try:
                routes = settings.get("routes", {})
                
                # Check if this chat is a source channel for any of the user's routes
                for source_channel, targets in routes.items():
                    if stored_value_matches_chat(source_channel, chat):
                        
                        # Create concurrent edit tasks for each target
                        task = asyncio.create_task(
                            update_corresponding_messages(
                                user_id_str, chat.id, edited_message_id, new_text, targets, event
                            )
                        )
                        user_tasks.append(task)
                        break
            except Exception as e:
                log_error(f"Error processing message edit for user {user_id_str}", e)
        
        # Wait for all edit tasks to complete
        if user_tasks:
            await asyncio.gather(*user_tasks, return_exceptions=True)
        
        edit_time = time.time() - edit_start_time
        log_activity(f"✅ EDIT COMPLETE: Processed message {edited_message_id} in {edit_time:.3f} seconds")
       
    except Exception as e:
        log_error("Critical error in message edit handler", e)

async def update_corresponding_messages(user_id_str: str, source_chat_id: int, source_message_id: int, 
                                      new_text: str, targets: List[str], event) -> None:
    """Update corresponding messages in target channels when source message is edited"""
    try:
        message_start_time = time.time()
        
        # Get all destination message IDs for this source message
        destination_messages = get_destination_message_ids(user_id_str, source_chat_id, source_message_id)
        
        if not destination_messages:
            log_activity(f"❌ No message mappings found for edit - user {user_id_str}, source {source_chat_id}:{source_message_id}")
            return
        
        updated_count = 0
        error_count = 0
        permission_errors = 0
        
        # Create edit tasks for ALL destinations in parallel
        edit_tasks = []
        entity_cache = {}  # Cache entities to avoid duplicate lookups
        
        for target_chat_key, destination_message_id in destination_messages.items():
            task = asyncio.create_task(
                update_single_message(
                    target_chat_key, destination_message_id, new_text, user_id_str, 
                    source_message_id, entity_cache, event
                )
            )
            edit_tasks.append(task)
        
        # Wait for all edits to complete
        results = await asyncio.gather(*edit_tasks, return_exceptions=True)
        
        # Count results
        for result in results:
            if isinstance(result, Exception):
                error_count += 1
                if isinstance(result, (ChannelPrivateError, ChatAdminRequiredError, ChatWriteForbiddenError)):
                    permission_errors += 1
            elif result is True:
                updated_count += 1
            else:
                error_count += 1
        
        message_time = time.time() - message_start_time
        log_activity(f"📊 User {user_id_str}: Updated {updated_count}/{len(destination_messages)} messages in {message_time:.3f}s (Errors: {error_count}, Permissions: {permission_errors})")
        
    except Exception as e:
        log_error(f"Error in update_corresponding_messages for user {user_id_str}", e)

async def update_single_message(target_chat_key: str, destination_message_id: int, new_text: str,
                              user_id_str: str, source_message_id: int, 
                              entity_cache: dict, event) -> bool:
    """Update a single message with new text/content"""
    try:
        # Check cache first
        if target_chat_key not in entity_cache:
            try:
                # Check if target_chat_key is a numeric ID or a username
                if target_chat_key.lstrip('-').isdigit():
                    # It's a numeric channel ID
                    target_chat_id = int(target_chat_key)
                    target_entity = await client.get_entity(target_chat_id)
                else:
                    # It's a username
                    if not target_chat_key.startswith('@'):
                        target_chat_key_with_at = '@' + target_chat_key
                    else:
                        target_chat_key_with_at = target_chat_key
                    target_entity = await client.get_entity(target_chat_key_with_at)
                
                entity_cache[target_chat_key] = target_entity
            except Exception as resolve_error:
                log_error(f"Cannot resolve channel {target_chat_key} for user {user_id_str}", resolve_error)
                return False
        else:
            target_entity = entity_cache[target_chat_key]
        
        # Check if the message has media
        if event.message.media:
            # For media messages, we can only edit the caption
            await client.edit_message(target_entity, destination_message_id, text=new_text)
        else:
            # For text messages, edit the entire message
            await client.edit_message(target_entity, destination_message_id, text=new_text)
        
        log_activity(f"✅ MESSAGE UPDATED: User {user_id_str} - Target {target_chat_key}:{destination_message_id} (Source: {source_message_id})")
        return True
        
    except (ChannelPrivateError, ChatAdminRequiredError, ChatWriteForbiddenError) as e:
        log_error(f"❌ PERMISSION ERROR: Cannot edit message in {target_chat_key} for user {user_id_str}. Bot may lack edit permissions.", e)
        return False
    except Exception as e:
        log_error(f"❌ EDIT ERROR: Error updating message in {target_chat_key} for user {user_id_str}", e)
        return False

# ========= ENHANCED INSTANT MESSAGE DELETION HANDLER =========
@client.on(events.MessageDeleted)
async def handle_message_deleted(event):
    """Enhanced message deletion handler with performance monitoring and instant deletion"""
    try:
        deletion_start_time = time.time()  
        # Get the chat where deletion occurred
        try:
            chat = await event.get_chat()
        except Exception as e:
            log_error("Could not get chat from deletion event", e)
            return
            
        deleted_message_ids = event.deleted_ids
        
        if not deleted_message_ids:
            return
        
        log_activity(f"🚨 INSTANT DELETION: Channel {getattr(chat, 'id', 'unknown')} - Message IDs: {deleted_message_ids}")
        
        
        # Refresh settings to get current user configurations
        fresh_settings = load_settings()
        discord_routes = load_discord_routes()
        
        # Process for each user CONCURRENTLY for faster processing
        user_tasks = []
    # ---------- FIRST BLOCK ----------
        for user_id, routes in discord_routes.items():

            for source_channel, targets in routes.items():   # FIX: targets comes from this loop

                if stored_value_matches_chat(source_channel, chat):
                    # print(source_channel, chat, sep="::")

                    for deleted_id in deleted_message_ids:
                        try:
                            task1 = asyncio.create_task(
                                delete_corresponding_messages_instant(
                                    user_id, chat.id, deleted_id, targets
                                )
                            )
                            user_tasks.append(task1)
                        except Exception as e:
                            log_error("Error creating task in discord_routes block", e)

                    break   # keep this (only break the inner loop, not the outer one)


        # ---------- SECOND BLOCK ----------
        for user_id_str, settings in fresh_settings.items():
            try:
                routes = settings.get("routes", {})

                for source_channel, targets in routes.items():

                    if stored_value_matches_chat(source_channel, chat):
                        # print(source_channel, chat, sep="::")

                        for deleted_id in deleted_message_ids:
                            task = asyncio.create_task(
                                delete_corresponding_messages_instant(
                                    user_id_str, chat.id, deleted_id, targets
                                )
                            )
                            user_tasks.append(task)

                        break   # break inner loop only

            except Exception as e:
                log_error(f"Error processing message deletion for user {user_id_str}", e)

        
        # Wait for all deletion tasks to complete
        if user_tasks:
            await asyncio.gather(*user_tasks, return_exceptions=True)
        
        deletion_time = time.time() - deletion_start_time
        log_activity(f"✅ DELETION COMPLETE: Processed {len(deleted_message_ids)} messages in {deletion_time:.3f} seconds")
       
    except Exception as e:
        log_error("Critical error in message deletion handler", e)

async def delete_corresponding_messages_instant(user_id_str: str, source_chat_id: int, source_message_id: int, targets: List[str]) -> None:
    """INSTANT deletion of corresponding messages with parallel processing"""
    try:
        message_start_time = time.time()
        
        # Get all destination message IDs for this source message (Telegram)
        destination_messages = get_destination_message_ids(user_id_str, source_chat_id, source_message_id)
        
        # Get all Discord message mappings for this source message
        discord_messages = get_discord_message_mappings(user_id_str, source_chat_id, source_message_id)
        
        if not destination_messages and not discord_messages:
            log_activity(f"❌ No message mappings found for user {user_id_str}, source {source_chat_id}:{source_message_id}")
            return
        
        deleted_count = 0
        error_count = 0
        permission_errors = 0
        
        # Create deletion tasks for ALL Telegram destinations in parallel
        deletion_tasks = []
        entity_cache = {}  # Cache entities to avoid duplicate lookups
        
        # Delete Telegram messages
        for target_chat_key, destination_message_id in destination_messages.items():
            print("deleted")
            task = asyncio.create_task(
                delete_single_telegram_message_instant(
                    target_chat_key, destination_message_id, user_id_str, 
                    source_message_id, entity_cache
                )
            )
            deletion_tasks.append(task)
        

        # Delete Discord messages
        for discord_channel_id, discord_message_id in discord_messages.items():
        
            task = asyncio.create_task(
                delete_single_discord_message_instant(
                    discord_channel_id, discord_message_id, user_id_str, 
                    source_message_id
                )
            )
            deletion_tasks.append(task)
        
        # Wait for all deletions to complete
        results = await asyncio.gather(*deletion_tasks, return_exceptions=True)
        
        # Count results
        for result in results:
            if isinstance(result, Exception):
                error_count += 1
                if isinstance(result, (ChannelPrivateError, ChatAdminRequiredError, ChatWriteForbiddenError)):
                    permission_errors += 1
            elif result is True:
                deleted_count += 1
            else:
                error_count += 1
        
        # Remove the mappings regardless of deletion success
        remove_message_mapping(user_id_str, source_chat_id, source_message_id)
        remove_discord_message_mapping(user_id_str, source_chat_id, source_message_id)
        
        message_time = time.time() - message_start_time
        log_activity(f"📊 User {user_id_str}: Deleted {deleted_count}/{len(destination_messages) + len(discord_messages)} messages in {message_time:.3f}s (Errors: {error_count}, Permissions: {permission_errors})")
        
        # Record performance
        deletion_performance.record_deletion(
            success=(deleted_count > 0),
            deletion_time=message_time,
            is_permission_error=(permission_errors > 0)
        )
        
    except Exception as e:
        log_error(f"Error in delete_corresponding_messages_instant for user {user_id_str}", e)

async def delete_single_telegram_message_instant(target_chat_key: str, destination_message_id: int, 
                                      user_id_str: str, source_message_id: int, 
                                      entity_cache: dict) -> bool:
    """Delete a single Telegram message with optimized entity caching and error handling"""
    try:
        # Check cache first
        if target_chat_key not in entity_cache:
            try:
                # Check if target_chat_key is a numeric ID or a username
                if target_chat_key.lstrip('-').isdigit():
                    # It's a numeric channel ID
                    target_chat_id = int(target_chat_key)
                    target_entity = await client.get_entity(target_chat_id)
                else:
                    # It's a username
                    if not target_chat_key.startswith('@'):
                        target_chat_key_with_at = '@' + target_chat_key
                    else:
                        target_chat_key_with_at = target_chat_key
                    target_entity = await client.get_entity(target_chat_key_with_at)
                
                entity_cache[target_chat_key] = target_entity
            except Exception as resolve_error:
                log_error(f"Cannot resolve channel {target_chat_key} for user {user_id_str}", resolve_error)
                return False
        else:
            target_entity = entity_cache[target_chat_key]
        
        # Delete the message in destination channel
        await client.delete_messages(target_entity, [destination_message_id], revoke=True)
        
        log_activity(f"✅ INSTANT DELETE: User {user_id_str} - Target {target_chat_key}:{destination_message_id} (Source: {source_message_id})")
        return True
        
    except (ChannelPrivateError, ChatAdminRequiredError, ChatWriteForbiddenError) as e:
        log_error(f"❌ PERMISSION ERROR: Cannot delete in {target_chat_key} for user {user_id_str}. Bot may lack delete permissions.", e)
        return False
    except Exception as e:
        log_error(f"❌ DELETE ERROR: Error deleting message in {target_chat_key} for user {user_id_str}", e)
        return False

async def delete_single_discord_message_instant(
    discord_channel_id: str, 
    discord_message_id: str,
    user_id_str: str, 
    source_message_id: int
) -> bool:

    if not DISCORD_TOKEN:
        log_error("No Discord token configured for deletion", None)
        return False

    url = f"https://discord.com/api/v10/channels/{discord_channel_id}/messages/{discord_message_id}"

    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=headers) as resp:

                print("Discord DELETE status:", resp.status)

                if resp.status == 204:
                    print("Message deleted successfully")
                    return True
                else:
                    text = await resp.text()
                    print("Discord delete failed:", resp.status, text)
                    log_error(f"Discord message delete failed {resp.status}", text)
                    return False

    except Exception as e:
        print("DISCORD DELETE ERROR:", e)
        log_error("Discord delete error", e)
        return False

def cleanup_orphaned_mappings() -> None:
    """Clean up orphaned message mappings (users/channels that no longer exist)"""
    try:
        mappings = load_message_mappings()
        discord_mappings = load_discord_message_mappings()
        fresh_settings = load_settings()
        
        deleted_count = 0
        users_to_remove = []
        
        # Clean up Telegram message mappings
        for user_id_str, user_mappings in mappings.items():
            # Check if user still exists in settings
            if user_id_str not in fresh_settings:
                users_to_remove.append(user_id_str)
                deleted_count += sum(len(chat_mappings) for chat_mappings in user_mappings.values())
                continue
            
            # Check if source channels still exist in user's routes
            user_routes = fresh_settings[user_id_str].get("routes", {})
            source_channels = set(user_routes.keys())
            
            chats_to_remove = []
            for source_chat_str, chat_mappings in user_mappings.items():
                channel_found = False
                for route_source in source_channels:
                    # Handle both string and integer comparisons
                    if route_source.lstrip("-").isdigit() and int(route_source) == int(source_chat_str):
                        channel_found = True
                        break
                    elif route_source == source_chat_str:
                        channel_found = True
                        break
                
                if not channel_found:
                    chats_to_remove.append(source_chat_str)
                    deleted_count += len(chat_mappings)
            
            # Remove orphaned channels
            for chat_str in chats_to_remove:
                del user_mappings[chat_str]
        
        # Remove orphaned users
        for user_id_str in users_to_remove:
            del mappings[user_id_str]
        
        if deleted_count > 0:
            save_message_mappings(mappings)
            log_activity(f"Cleaned up {deleted_count} orphaned Telegram message mappings")
        
        # Clean up Discord message mappings
        deleted_discord_count = 0
        users_to_remove_discord = []
        
        for user_id_str, user_mappings in discord_mappings.items():
            # Check if user still exists in settings
            if user_id_str not in fresh_settings:
                users_to_remove_discord.append(user_id_str)
                deleted_discord_count += sum(len(chat_mappings) for chat_mappings in user_mappings.values())
                continue
            
            # Check if source channels still exist in user's routes
            user_routes = fresh_settings[user_id_str].get("routes", {})
            source_channels = set(user_routes.keys())
            
            chats_to_remove = []
            for source_chat_str, chat_mappings in user_mappings.items():
                channel_found = False
                for route_source in source_channels:
                    # Handle both string and integer comparisons
                    if route_source.lstrip("-").isdigit() and int(route_source) == int(source_chat_str):
                        channel_found = True
                        break
                    elif route_source == source_chat_str:
                        channel_found = True
                        break
                
                if not channel_found:
                    chats_to_remove.append(source_chat_str)
                    deleted_discord_count += len(chat_mappings)
            
            # Remove orphaned channels
            for chat_str in chats_to_remove:
                del user_mappings[chat_str]
        
        # Remove orphaned users
        for user_id_str in users_to_remove_discord:
            del discord_mappings[user_id_str]
        
        if deleted_discord_count > 0:
            save_discord_message_mappings(discord_mappings)
            log_activity(f"Cleaned up {deleted_discord_count} orphaned Discord message mappings")
    
    except Exception as e:
        log_error("Error cleaning up orphaned message mappings", e)

# ========= DISCORD ROUTE MANAGEMENT =========
def load_discord_routes() -> Dict:
    """Load Discord routes from JSON file"""
    try:
        if os.path.exists(DISCORD_ROUTES_FILE):
            with open(DISCORD_ROUTES_FILE, "r", encoding="utf-8") as f:
                routes = json.load(f)
            log_activity(f"Discord routes loaded from {DISCORD_ROUTES_FILE}")
            return routes
        else:
            log_activity(f"No existing Discord routes file found.")
            return {}
    except Exception as e:
        log_error(f"Failed to load Discord routes from {DISCORD_ROUTES_FILE}", e)
        return {}

def save_discord_routes(routes: Dict) -> None:
    """Save Discord routes to JSON file"""
    try:
        with open(DISCORD_ROUTES_FILE, "w", encoding="utf-8") as f:
            json.dump(routes, f, indent=2, ensure_ascii=False)
        log_activity(f"Discord routes saved to {DISCORD_ROUTES_FILE}")
    except Exception as e:
        log_error(f"Failed to save Discord routes to {DISCORD_ROUTES_FILE}", e)

def get_user_discord_routes(user_id: int) -> Dict:
    """Get Discord routes for a specific user"""
    routes = load_discord_routes()
    return routes.get(str(user_id), {})

def save_user_discord_routes(user_id: int, user_routes: Dict) -> None:
    """Save Discord routes for a specific user"""
    routes = load_discord_routes()
    routes[str(user_id)] = user_routes
    save_discord_routes(routes)

async def start_discord_route_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start Discord route management interface"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_routes = get_user_discord_routes(user_id)
        
        text = (
            "🔗 <b>Discord Route Management</b>\n\n"
            "Manage routes to forward messages from Telegram channels to Discord channels.\n\n"
            f"📊 Current Routes: <b>{sum(len(channels) for channels in user_routes.values())}</b>\n\n"
            "💡 <b>How it works:</b>\n"
            "• Messages from specified Telegram channels will be forwarded to Discord channels\n"
            "• Text, images, and basic media are supported\n"
            "• You need Discord bot token with required permissions\n"
            "• The bot must have access to the Discord channels\n"
            "• 🗑️ <b>Message deletion sync is supported!</b> - When a message is deleted in Telegram, it will also be deleted in Discord"
            "• ✏️ <b>Message editing sync is supported!</b> - When a message is edited in Telegram, it will also be edited in Discord"
        )
        
        keyboard = get_discord_management_keyboard()
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error starting Discord route management for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading Discord routes.", get_navigation_keyboard())

def get_discord_management_keyboard():
    """Create keyboard for Discord route management"""
    keyboard = [
        [InlineKeyboardButton("➕ Add Discord Route", callback_data="add_discord_route")],
        [InlineKeyboardButton("📋 View Discord Routes", callback_data="view_discord_routes")],
        [InlineKeyboardButton("🗑️ Delete Discord Route", callback_data="delete_discord_route")],
        [InlineKeyboardButton("⚙️ Discord Settings", callback_data="discord_settings")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_add_discord_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the process of adding a Discord route"""
    user_id = update.callback_query.from_user.id
    
    try:
        # Check if Discord credentials are set
        if not DISCORD_TOKEN and not DISCORD_WEBHOOK_URL:
            text = (
                "❌ <b>Discord Integration Not Configured</b>\n\n"
                "To use Discord integration, you need to set up Discord credentials in your .env file:\n\n"
                "Required environment variables (choose one):\n"
                "• <code>DISCORD_TOKEN</code> - Your Discord Bot Token\n"
                "• <code>DISCORD_WEBHOOK_URL</code> - Discord Webhook URL\n\n"
                "💡 <b>How to get these:</b>\n"
                "1. Create a Discord Bot at https://discord.com/developers/applications\n"
                "2. Get the Bot Token from the Bot section\n"
                "3. Invite the bot to your server with required permissions\n"
                "4. Add the token to your .env file\n\n"
                "OR create a webhook:\n"
                "1. Go to Discord channel settings\n"
                "2. Create webhook and copy the URL\n"
                "3. Add to .env as DISCORD_WEBHOOK_URL\n\n"
                "⚠️ <b>Important:</b> For message deletion and editing sync to work, you MUST use DISCORD_TOKEN (webhooks cannot delete or edit messages)\n\n"
                "Once configured, restart the bot and try again."
            )
            await safe_edit_message(update, text, get_discord_management_keyboard())
            return
        
        # Check if we have DISCORD_TOKEN for deletion functionality
        if not DISCORD_TOKEN:
            text = (
                "⚠️ <b>Limited Discord Functionality</b>\n\n"
                "You're using a webhook for Discord integration, which has limitations:\n\n"
                "✅ <b>What works:</b>\n"
                "• Forwarding messages from Telegram to Discord\n"
                "• Sending text and images\n\n"
                "❌ <b>What doesn't work:</b>\n"
                "• Message deletion sync (requires bot token)\n"
                "• Message editing sync (requires bot token)\n\n"
                "💡 <b>To enable full functionality:</b>\n"
                "1. Switch to using DISCORD_TOKEN instead of webhook\n"
                "2. This enables message deletion and editing synchronization\n"
                "3. The bot will delete/edit Discord messages when Telegram messages are deleted/edited\n\n"
                "Do you want to continue with webhook-only mode?"
            )
            
            keyboard = [
                [InlineKeyboardButton("✅ Continue with Webhook", callback_data="continue_webhook_discord")],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu_discord_routes")]
            ]
            await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
            return
        
        user_settings_data = get_user_settings_fresh(user_id)
        available_channels = user_settings_data.get("available_channels", {})
        
        if not available_channels:
            text = (
                "❌ <b>No Channels Available</b>\n\n"
                "You need to select Telegram channels first before creating Discord routes.\n\n"
                "💡 <b>How to proceed:</b>\n"
                "1. Use 📋 Select Channels to choose Telegram channels\n"
                "2. Save your selection\n"
                "3. Then come back here to add Discord routes"
            )
            await safe_edit_message(update, text, get_discord_management_keyboard())
            return
        
        discord_route_states[user_id] = {
            "step": "selecting_source",
            "available_channels": available_channels,
            "source_channel": None,
            "discord_channel_id": None
        }
        
        text = (
            "🔍 <b>Select Source Channel for Discord</b>\n\n"
            "Choose the Telegram channel where messages will come from:\n\n"
            f"📊 Available Channels: <b>{len(available_channels)}</b>\n\n"
            "💡 <b>Sync Features:</b> When a message is deleted or edited in this Telegram channel, it will also be deleted/edited in the Discord channel."
        )
        
        keyboard = create_discord_source_selection_keyboard(available_channels, 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error starting Discord route addition for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while setting up Discord route.", get_navigation_keyboard())

async def handle_continue_webhook_discord(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Continue with webhook-only Discord mode"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings_data = get_user_settings_fresh(user_id)
        available_channels = user_settings_data.get("available_channels", {})
        
        if not available_channels:
            text = (
                "❌ <b>No Channels Available</b>\n\n"
                "You need to select Telegram channels first before creating Discord routes.\n\n"
                "💡 <b>How to proceed:</b>\n"
                "1. Use 📋 Select Channels to choose Telegram channels\n"
                "2. Save your selection\n"
                "3. Then come back here to add Discord routes"
            )
            await safe_edit_message(update, text, get_discord_management_keyboard())
            return
        
        discord_route_states[user_id] = {
            "step": "selecting_source",
            "available_channels": available_channels,
            "source_channel": None,
            "discord_channel_id": None,
            "webhook_only": True
        }
        
        text = (
            "🔍 <b>Select Source Channel for Discord (Webhook Mode)</b>\n\n"
            "Choose the Telegram channel where messages will come from:\n\n"
            f"📊 Available Channels: <b>{len(available_channels)}</b>\n\n"
            "⚠️ <b>Webhook Mode Limitations:</b>\n"
            "• Message deletion sync is not available\n"
            "• Message editing sync is not available\n"
            "• Only forwarding of new messages works"
        )
        
        keyboard = create_discord_source_selection_keyboard(available_channels, 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error continuing webhook Discord mode for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while setting up Discord route.", get_navigation_keyboard())

def create_discord_source_selection_keyboard(available_channels: Dict, page: int = 0, items_per_page: int = 8):
    """Create keyboard for selecting source channel for Discord route"""
    keyboard = []
    
    channels_list = list(available_channels.items())
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_channels = channels_list[start_idx:end_idx]
    
    for channel_key, channel_info in page_channels:
        display_name = f"📢 {channel_info['title']}"
        if channel_info['username']:
            display_name += f" (@{channel_info['username']})"
        else:
            display_name += f" (ID: {channel_info['id']})"
        
        if len(display_name) > 40:
            display_name = display_name[:37] + "..."
        
        callback_data = f"discord_select_source_{channel_key}_{page}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=callback_data)])
    
    navigation_buttons = []
    total_pages = (len(channels_list) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"discord_source_page_{page-1}"))
    
    if end_idx < len(channels_list):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"discord_source_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_discord_routes")])
    
    return InlineKeyboardMarkup(keyboard)

async def handle_discord_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_key: str, page: int) -> None:
    """Handle source channel selection for Discord route"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in discord_route_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = discord_route_states[user_id]
    available_channels = state["available_channels"]
    
    if channel_key not in available_channels:
        await update.callback_query.answer("Channel not found")
        return
    
    channel_info = available_channels[channel_key]
    state["source_channel"] = channel_key
    state["step"] = "entering_discord_channel"
    
    channel_display = f"@{channel_info['username']}" if channel_info['username'] else f"{channel_info['title']} (ID: {channel_info['id']})"
    
    # Check if we're in webhook-only mode
    webhook_warning = ""
    if state.get("webhook_only"):
        webhook_warning = "\n\n⚠️ <b>Webhook Mode:</b> Message deletion and editing sync will not be available."
    else:
        webhook_warning = "\n\n✅ <b>Bot Token Mode:</b> Message deletion and editing sync are enabled!"
    
    text = (
        f"✅ <b>Source Channel Selected:</b> {channel_display}\n\n"
        "🔗 <b>Enter Discord Channel ID</b>\n\n"
        "Please send me the Discord Channel ID where messages should be forwarded:\n\n"
        "💡 <b>How to find Channel ID:</b>\n"
        "1. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode)\n"
        "2. Right-click on the channel and select 'Copy ID'\n"
        "3. Paste the numeric Channel ID here\n\n"
        "Example: <code>123456789012345678</code>\n\n"
        f"{webhook_warning}\n\n"
        "Send the Discord Channel ID now:"
    )
    
    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data="menu_discord_routes")],
        [InlineKeyboardButton("🔙 Back to Channel Selection", callback_data="discord_back_to_sources")]
    ]
    
    await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
    await update.callback_query.answer(f"Selected: {channel_info['title']}")

async def handle_discord_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Discord Channel ID input"""
    user_id = update.message.from_user.id
    channel_id_input = update.message.text.strip()
    
    if user_id not in discord_route_states:
        return
    
    state = discord_route_states[user_id]
    
    if state["step"] != "entering_discord_channel":
        return
    
    if not channel_id_input or not channel_id_input.isdigit():
        await safe_reply(update, 
            "❌ Invalid Discord Channel ID. Please enter a numeric Channel ID.\n\n"
            "Example: <code>123456789012345678</code>\n\n"
            "Please try again:",
            get_navigation_keyboard()
        )
        return
    
    state["discord_channel_id"] = channel_id_input
    state["discord_channel_name"] = f"Discord Channel {channel_id_input}"
        
    await complete_discord_route_creation(update, context, user_id)

async def complete_discord_route_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Complete the Discord route creation"""
    try:
        state = discord_route_states[user_id]
        source_channel = state["source_channel"]
        discord_channel_id = state["discord_channel_id"]
        discord_channel_name = state.get("discord_channel_name", "Unknown Channel")
        is_webhook_only = state.get("webhook_only", False)
        
        if not source_channel or not discord_channel_id:
            await safe_reply(update, "❌ Missing information. Please start again.", get_navigation_keyboard())
            return
        
        # Save the Discord route
        user_routes = get_user_discord_routes(user_id)
        
        if source_channel not in user_routes:
            user_routes[source_channel] = []
        
        if discord_channel_id not in [c["channel_id"] for c in user_routes[source_channel]]:
            user_routes[source_channel].append({
                "channel_id": discord_channel_id,
                "channel_name": discord_channel_name,
                "created_at": datetime.now().isoformat(),
                "webhook_only": is_webhook_only  # Store whether this route is webhook-only
            })
            
            save_user_discord_routes(user_id, user_routes)
            
            # Get channel info for display
            available_channels = state["available_channels"]
            channel_info = available_channels.get(source_channel, {})
            channel_display = f"@{channel_info.get('username', '')}" if channel_info.get('username') else f"{channel_info.get('title', 'Unknown')} (ID: {channel_info.get('id', '')})"
            
            # Add deletion sync info
            sync_info = ""
            if is_webhook_only:
                sync_info = "❌ <b>Deletion & Editing Sync:</b> Not available (webhook mode)"
            else:
                sync_info = "✅ <b>Deletion & Editing Sync:</b> Enabled (bot token mode)"
            
            text = (
                f"✅ <b>Discord Route Added Successfully!</b>\n\n"
                f"<b>Telegram Source:</b> {channel_display}\n"
                f"<b>Discord Channel:</b> {discord_channel_name} (ID: {discord_channel_id})\n"
                f"{sync_info}\n\n"
                f"💡 <b>What happens now:</b>\n"
                f"• Messages from the Telegram channel will be forwarded to the Discord channel\n"
                f"• Text messages and images are supported\n"
                f"• Forwarding happens in real-time\n"
                f"• Use 📋 View Discord Routes to see all your routes\n\n"
                f"🔧 <b>Note:</b> Make sure your Discord bot has proper permissions in the channel!"
            )
            
            log_activity(f"User {user_id} added Discord route: {source_channel} → {discord_channel_id} (webhook_only: {is_webhook_only})")
        else:
            text = f"⚠️ Discord route already exists for this channel and Discord channel."
        
        if user_id in discord_route_states:
            del discord_route_states[user_id]
        
        await safe_reply(update, text, get_discord_management_keyboard())
        
    except Exception as e:
        log_error(f"Error completing Discord route creation for user {user_id}", e)
        await safe_reply(update, "❌ An error occurred while creating the Discord route. Please try again.", get_navigation_keyboard())
        if user_id in discord_route_states:
            del discord_route_states[user_id]

async def view_discord_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all Discord routes for the user"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_routes = get_user_discord_routes(user_id)
        
        if not user_routes:
            text = (
                "🔗 <b>Your Discord Routes</b>\n\n"
                "❌ No Discord routes found.\n\n"
                "Use ➕ Add Discord Route to create your first route from Telegram to Discord."
            )
        else:
            lines = []
            total_routes = 0
            sync_enabled_count = 0
            
            for source_channel, channels in user_routes.items():
                # Get channel display name
                user_settings_data = get_user_settings_fresh(user_id)
                available_channels = user_settings_data.get("available_channels", {})
                channel_info = available_channels.get(source_channel, {})
                channel_display = f"@{channel_info.get('username', '')}" if channel_info.get('username') else f"{channel_info.get('title', 'Unknown')} (ID: {source_channel})"
                
                channel_lines = []
                for channel in channels:
                    sync_status = "❌" if channel.get("webhook_only") else "✅"
                    channel_lines.append(f"  • {channel['channel_name']} (ID: {channel['channel_id']}) {sync_status}")
                    total_routes += 1
                    if not channel.get("webhook_only"):
                        sync_enabled_count += 1
                
                lines.append(f"📢 {channel_display}:")
                lines.extend(channel_lines)
                lines.append("")  # Empty line for spacing
            
            sync_info = f"🔄 Sync Features: {sync_enabled_count}/{total_routes} routes"
            
            text = (
                f"🔗 <b>Your Discord Routes</b>\n\n"
                f"📊 Total Routes: <b>{total_routes}</b>\n"
                f"{sync_info}\n\n"
                "✅ = Deletion & editing sync enabled | ❌ = Webhook mode (no sync)\n\n" +
                "\n".join(lines) +
                f"\n💡 Messages from these Telegram channels will be forwarded to the corresponding Discord channels."
            )
        
        keyboard = [
            [InlineKeyboardButton("➕ Add More Routes", callback_data="add_discord_route")],
            [InlineKeyboardButton("🗑️ Delete Routes", callback_data="delete_discord_route")],
            [InlineKeyboardButton("🔙 Back to Discord Management", callback_data="menu_discord_routes")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error viewing Discord routes for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading Discord routes.", get_navigation_keyboard())

async def start_delete_discord_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the Discord route deletion process with improved state management"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_routes = get_user_discord_routes(user_id)
        
        if not user_routes:
            text = (
                "🗑️ <b>Delete Discord Route</b>\n\n"
                "❌ No Discord routes found to delete.\n\n"
                "💡 <b>How to create Discord routes:</b>\n"
                "1. Use ➕ Add Discord Route to create routes\n"
                "2. Make sure Discord credentials are configured\n"
                "3. Select Telegram source channels\n"
                "4. Enter Discord channel IDs\n"
            )
            await safe_edit_message(update, text, get_discord_management_keyboard())
            return
        
        # Initialize deletion state
        discord_route_states[user_id] = {
            "step": "deleting_route",
            "user_routes": user_routes,
            "selected_route": None
        }
        
        total_routes = sum(len(channels) for channels in user_routes.values())
        
        text = (
            f"🗑️ <b>Delete Discord Route</b>\n\n"
            f"📊 Found <b>{total_routes}</b> Discord routes.\n\n"
            "Select a route to delete:\n\n"
            "💡 <b>Note:</b> Deleting a route will stop forwarding messages from the Telegram channel to the Discord channel and remove deletion/editing sync."
        )
        
        keyboard = create_discord_deletion_keyboard(user_routes, user_id, 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error starting Discord route deletion for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading routes for deletion.", get_navigation_keyboard())

def create_discord_deletion_keyboard(user_routes: Dict, user_id: int, page: int = 0, items_per_page: int = 8):
    """Create keyboard for Discord route deletion with improved route key format"""
    keyboard = []
    
    # Flatten routes for display
    all_routes = []
    for source_channel, channels in user_routes.items():
        for channel in channels:
            all_routes.append({
                "source_channel": source_channel,
                "channel": channel
            })
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_routes = all_routes[start_idx:end_idx]
    
    for i, route in enumerate(page_routes):
        # Get channel display name
        user_settings_data = get_user_settings_fresh(user_id)
        available_channels = user_settings_data.get("available_channels", {})
        channel_info = available_channels.get(route["source_channel"], {})
        channel_display = f"@{channel_info.get('username', '')}" if channel_info.get('username') else f"{channel_info.get('title', 'Unknown')}"
        
        sync_status = "❌" if route["channel"].get("webhook_only") else "✅"
        display_name = f"{channel_display} → {route['channel']['channel_name']} {sync_status}"
        if len(display_name) > 35:
            display_name = display_name[:32] + "..."
        
        # Use dot separator for route key to avoid issues with underscores in channel names
        route_key = f"{route['source_channel']}.{route['channel']['channel_id']}"
        callback_data = f"discord_delete_{route_key}_{page}"
        keyboard.append([InlineKeyboardButton(f"🗑️ {display_name}", callback_data=callback_data)])
    
    navigation_buttons = []
    total_pages = (len(all_routes) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"discord_del_page_{page-1}"))
    
    if end_idx < len(all_routes):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"discord_del_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_discord_routes")])
    
    return InlineKeyboardMarkup(keyboard)

async def handle_discord_route_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE, route_key: str, page: int) -> None:
    """Handle Discord route deletion with improved error handling"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in discord_route_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    try:
        state = discord_route_states[user_id]
        user_routes = state["user_routes"]
        
        # Parse the route key (source_channel.discord_channel_id)
        if '.' in route_key:
            source_channel, channel_id = route_key.split('.', 1)
        else:
            # Fallback for old format
            parts = route_key.split('_', 1)
            if len(parts) == 2:
                source_channel, channel_id = parts
            else:
                await update.callback_query.answer("❌ Invalid route format")
                return
        
        # Find and remove the route
        route_found = False
        if source_channel in user_routes:
            original_count = len(user_routes[source_channel])
            user_routes[source_channel] = [
                route for route in user_routes[source_channel] 
                if route["channel_id"] != channel_id
            ]
            
            if len(user_routes[source_channel]) < original_count:
                route_found = True
                
                # Remove empty source channels
                if not user_routes[source_channel]:
                    del user_routes[source_channel]
                
                # Save updated routes
                save_user_discord_routes(user_id, user_routes)
                
                # Update state
                state["user_routes"] = user_routes
                
                # Get channel info for logging
                user_settings_data = get_user_settings_fresh(user_id)
                available_channels = user_settings_data.get("available_channels", {})
                channel_info = available_channels.get(source_channel, {})
                channel_display = f"@{channel_info.get('username', '')}" if channel_info.get('username') else f"{channel_info.get('title', 'Unknown')}"
                
                log_activity(f"User {user_id} deleted Discord route: {channel_display} → Discord {channel_id}")
                
                await update.callback_query.answer("✅ Route deleted successfully")
            else:
                await update.callback_query.answer("❌ Route not found")
        else:
            await update.callback_query.answer("❌ Source channel not found")
        
        # Refresh the deletion interface
        if user_routes:
            text = "🗑️ <b>Delete Discord Route</b>\n\nSelect a route to delete:"
            keyboard = create_discord_deletion_keyboard(user_routes, user_id, page)
            await safe_edit_message(update, text, keyboard)
        else:
            text = "✅ All Discord routes have been deleted."
            if user_id in discord_route_states:
                del discord_route_states[user_id]
            await safe_edit_message(update, text, get_discord_management_keyboard())
                
    except Exception as e:
        log_error(f"Error deleting Discord route for user {user_id}", e)
        await update.callback_query.answer("❌ Error deleting route")

async def handle_discord_deletion_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Handle pagination in Discord route deletion with improved error handling"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in discord_route_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    try:
        state = discord_route_states[user_id]
        user_routes = state["user_routes"]
        
        total_routes = sum(len(channels) for channels in user_routes.values())
        
        text = (
            f"🗑️ <b>Delete Discord Route</b>\n\n"
            f"📊 Found <b>{total_routes}</b> Discord routes.\n\n"
            "Select a route to delete:\n\n"
            "💡 <b>Note:</b> Deleting a route will stop forwarding messages from the Telegram channel to the Discord channel and remove deletion/editing sync."
        )
        
        keyboard = create_discord_deletion_keyboard(user_routes, user_id, page)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error handling Discord deletion pagination for user {user_id}", e)
        await update.callback_query.answer("❌ Error loading page")

async def show_discord_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Discord integration settings and status"""
    user_id = update.callback_query.from_user.id
    
    try:
        # Check Discord configuration
        discord_configured = bool(DISCORD_TOKEN or DISCORD_WEBHOOK_URL)
        
        user_routes = get_user_discord_routes(user_id)
        total_routes = sum(len(channels) for channels in user_routes.values())
        
        # Count routes with deletion sync
        sync_enabled_routes = 0
        for source_channel, channels in user_routes.items():
            for channel in channels:
                if not channel.get("webhook_only"):
                    sync_enabled_routes += 1
        
        status_emoji = "✅" if discord_configured else "❌"
        status_text = "Configured" if discord_configured else "Not Configured"
        
        text = (
            f"⚙️ <b>Discord Integration Settings</b>\n\n"
            f"🔧 Status: {status_emoji} {status_text}\n"
            f"📊 Your Discord Routes: {total_routes}\n"
            f"🔄 Routes with Sync Features: {sync_enabled_routes}/{total_routes}\n\n"
        )
        
        if discord_configured:
            if DISCORD_TOKEN:
                method = "Bot Token"
                sync_status = "✅ Enabled"
            else:
                method = "Webhook"
                sync_status = "❌ Disabled (webhooks cannot delete/edit messages)"
                
            text += (
                f"✅ <b>Discord integration is properly configured.</b>\n"
                f"📡 Method: {method}\n"
                f"🔄 Message Sync: {sync_status}\n\n"
                "💡 <b>What you can do:</b>\n"
                "• Forward messages from Telegram to Discord channels\n"
                "• Support for text and image messages\n"
                "• Real-time forwarding\n"
                "• Multiple routes management\n"
            )
            
            if DISCORD_TOKEN:
                text += (
                    "• ✅ <b>Message deletion synchronization</b>\n"
                    "• ✅ <b>Message editing synchronization</b>\n\n"
                    "🔧 <b>Required Permissions:</b>\n"
                    "• Your Discord bot must be invited to the server\n"
                    "• Bot must have 'Send Messages' permission in target channels\n"
                    "• Bot must have 'Attach Files' permission for media\n"
                    "• Bot must have 'Embed Links' permission for rich content\n"
                    "• Bot must have 'Manage Messages' permission for deletion sync\n"
                    "• Bot must have 'Read Message History' permission for editing"
                )
            else:
                text += (
                    "• ❌ <b>Message deletion synchronization NOT available</b>\n"
                    "• ❌ <b>Message editing synchronization NOT available</b>\n\n"
                    "🔧 <b>Webhook Setup:</b>\n"
                    "• Webhook URL is configured\n"
                    "• No additional permissions needed\n"
                    "• Make sure webhook is not deleted from Discord\n"
                    "• Switch to bot token for full sync functionality"
                )
        else:
            text += (
                "❌ <b>Discord integration is not configured.</b>\n\n"
                "To enable Discord integration, add this to your .env file:\n\n"
                "<code>DISCORD_TOKEN=your_discord_bot_token</code>\n"
                "OR\n"
                "<code>DISCORD_WEBHOOK_URL=your_webhook_url</code>\n\n"
                "💡 <b>How to get these credentials:</b>\n"
                "1. Create a Discord Bot at https://discord.com/developers/applications\n"
                "2. Go to the 'Bot' section and copy the token\n"
                "3. Invite the bot to your server with required permissions\n"
                "4. Add the token to your .env file and restart the bot\n\n"
                "OR create a webhook:\n"
                "1. Go to Discord channel settings → Integrations → Webhooks\n"
                "2. Create a webhook and copy the URL\n"
                "3. Add to .env as DISCORD_WEBHOOK_URL\n\n"
                "⚠️ <b>Important:</b> Only bot tokens support message deletion and editing synchronization!"
            )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Discord Management", callback_data="menu_discord_routes")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error showing Discord settings for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading Discord settings.", get_navigation_keyboard())

# ========= DISCORD MESSAGE FORWARDING =========
async def forward_to_discord(event, user_id: int) -> None:
    """Forward a Telegram message to Discord channels - UPDATED to work independently and track message IDs"""
    try:
        user_routes = get_user_discord_routes(user_id)
        if not user_routes:
            return
        
        chat = await event.get_chat()
        message_id = event.message.id
        
        # Check if this chat matches any source channel in Discord routes
        # UPDATED: This now works independently of Telegram routes
        for source_channel, channels in user_routes.items():
            if stored_value_matches_chat(source_channel, chat):
                message_text = event.message.message or ""
                media_type = get_media_type(event)
                
                # Apply keyword filtering for Discord too
                user_settings_data = get_user_settings_fresh(user_id)
                required_keywords = user_settings_data.get("required_keywords", [])
                blocked_keywords = user_settings_data.get("blocked_keywords", [])
                
                if not check_keyword_filtering(message_text, required_keywords, blocked_keywords, media_type):
                    continue
                
                # Check media type allowance
                if not check_media_type_allowed(media_type, user_id):
                    continue
                
                # Forward to all Discord channels for this source channel
                for channel_info in channels:
                    discord_message_id = await send_to_discord_channel(event, channel_info, user_id)
                    
                    # Track the Discord message ID for deletion/editing synchronization (only if using bot token)
                    if discord_message_id and not channel_info.get("webhook_only"):
                        update_discord_message_mapping(
                            str(user_id), 
                            chat.id, 
                            message_id, 
                            channel_info["channel_id"], 
                            discord_message_id
                        )
                        log_activity(f"Discord message tracking: User {user_id}, source {chat.id}:{message_id} -> Discord {channel_info['channel_id']}:{discord_message_id}")
                
                break
                
    except Exception as e:
        log_error(f"Error in Discord forwarding for user {user_id}", e)

async def send_to_discord_channel(event, channel_info: Dict, user_id: int) -> Optional[str]:
    """Send message to a specific Discord channel and return the message ID"""
    try:
        channel_id = channel_info["channel_id"]
        message_text = event.message.message or ""
        media_type = get_media_type(event)
        
        if media_type == "photo" and event.message.media:
            # Handle photo
            discord_message_id = await send_photo_to_discord(event, channel_id, message_text, user_id, channel_info.get("webhook_only", False))
            return discord_message_id
        elif message_text.strip():
            # Handle text message
            discord_message_id = await send_text_to_discord(channel_id, message_text, user_id, channel_info.get("webhook_only", False))
            return discord_message_id
        else:
            # For other media types, send as text with description
            if message_text.strip():
                discord_message_id = await send_text_to_discord(channel_id, message_text, user_id, channel_info.get("webhook_only", False))
                return discord_message_id
            else:
                # If no text and unsupported media, send a generic message
                media_description = f"📎 {media_type.capitalize()} shared"
                discord_message_id = await send_text_to_discord(channel_id, media_description, user_id, channel_info.get("webhook_only", False))
                return discord_message_id
                
    except Exception as e:
        log_error(f"Error sending to Discord channel {channel_info['channel_name']} for user {user_id}", e)
        return None

async def send_text_to_discord(channel_id: str, text: str, user_id: int, webhook_only: bool = False) -> Optional[str]:
    """Send text message to Discord channel and return message ID"""
    try:
        # Use webhook if available and in webhook-only mode, otherwise use bot token
        if webhook_only and DISCORD_WEBHOOK_URL:
            return await send_via_webhook(channel_id, text, user_id)
        elif DISCORD_TOKEN:
            return await send_via_bot(channel_id, text, user_id)
        else:
            log_error("No Discord credentials configured", None)
            return None
                    
    except Exception as e:
        log_error(f"Error sending text to Discord channel {channel_id}", e)
        return None

async def send_photo_to_discord(event, channel_id: str, caption: str, user_id: int, webhook_only: bool = False) -> Optional[str]:
    """Send photo to Discord channel and return message ID"""
    try:
        # Download photo
        photo_data = await download_media(event, "photo")
        if not photo_data:
            return None
            
        # Use webhook if available and in webhook-only mode, otherwise use bot token
        if webhook_only and DISCORD_WEBHOOK_URL:
            return await send_photo_via_webhook(channel_id, photo_data, caption, user_id)
        elif DISCORD_TOKEN:
            return await send_photo_via_bot(channel_id, photo_data, caption, user_id)
        else:
            log_error("No Discord credentials configured", None)
            return None
                    
    except Exception as e:
        log_error(f"Error sending photo to Discord channel {channel_id}", e)
        return None

async def send_via_webhook(channel_id: str, text: str, user_id: int) -> Optional[str]:
    """Send message to Discord via webhook - returns None since webhooks don't provide message IDs we can use for deletion"""
    try:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        
        # Truncate message if too long for Discord
        if len(text) > 2000:
            text = text[:1997] + "..."
            
        message = webhook.send(content=text)
        log_activity(f"User {user_id}: Text successfully sent to Discord via webhook")
        
        # Webhooks don't easily provide message IDs we can use for deletion, so return None
        return None
        
    except Exception as e:
        log_error(f"Error sending via webhook to Discord channel {channel_id}", e)
        return None

async def send_via_bot(channel_id: str, text: str, user_id: int) -> Optional[str]:
    """Send message to Discord via bot API and return message ID"""
    try:
        headers = {
            'Authorization': f'Bot {DISCORD_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        # Truncate message if too long for Discord
        if len(text) > 2000:
            text = text[:1997] + "..."
        
        payload = {
            "content": text
        }
        
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    response_data = await response.json()
                    message_id = response_data.get('id')
                    log_activity(f"User {user_id}: Text successfully sent to Discord channel {channel_id}, message ID: {message_id}")
                    return message_id
                else:
                    error_text = await response.text()
                    log_error(f"Discord API error: {response.status} - {error_text}", None)
                    return None
        
    except Exception as e:
        log_error(f"Error sending via bot to Discord channel {channel_id}", e)
        return None

async def send_photo_via_webhook(channel_id: str, photo_data: bytes, caption: str, user_id: int) -> Optional[str]:
    """Send photo to Discord via webhook - returns None since webhooks don't provide message IDs we can use for deletion"""
    try:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
        
        # Create file object from photo data
        from io import BytesIO
        file = discord.File(BytesIO(photo_data), filename="photo.jpg")
        
        # Send with caption if available
        if caption and len(caption) <= 2000:
            message = webhook.send(content=caption, file=file)
        else:
            message = webhook.send(file=file)
            
        log_activity(f"User {user_id}: Photo successfully sent to Discord via webhook")
        
        # Webhooks don't easily provide message IDs we can use for deletion, so return None
        return None
        
    except Exception as e:
        log_error(f"Error sending photo via webhook to Discord channel {channel_id}", e)
        return None

async def send_photo_via_bot(channel_id: str, photo_data: bytes, caption: str, user_id: int) -> Optional[str]:
    """Send photo to Discord via bot API and return message ID"""
    try:
        headers = {
            'Authorization': f'Bot {DISCORD_TOKEN}'
        }
        
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        
        # Prepare form data for multipart upload
        data = aiohttp.FormData()
        data.add_field('file', photo_data, filename='photo.jpg', content_type='image/jpeg')
        
        if caption and len(caption) <= 2000:
            data.add_field('content', caption)
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, headers=headers) as response:
                if response.status == 200:
                    response_data = await response.json()
                    message_id = response_data.get('id')
                    log_activity(f"User {user_id}: Photo successfully sent to Discord channel {channel_id}, message ID: {message_id}")
                    return message_id
                else:
                    error_text = await response.text()
                    log_error(f"Discord API error: {response.status} - {error_text}", None)
                    return None
        
    except Exception as e:
        log_error(f"Error sending photo via bot to Discord channel {channel_id}", e)
        return None

# ========= DISCORD DELETION PAGINATION HANDLER =========
async def handle_discord_source_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Handle pagination in Discord source channel selection"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in discord_route_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    try:
        state = discord_route_states[user_id]
        available_channels = state["available_channels"]
        
        text = (
            "🔍 <b>Select Source Channel for Discord</b>\n\n"
            "Choose the Telegram channel where messages will come from:\n\n"
            f"📊 Available Channels: <b>{len(available_channels)}</b>"
        )
        
        keyboard = create_discord_source_selection_keyboard(available_channels, page)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error handling Discord source pagination for user {user_id}", e)
        await update.callback_query.answer("❌ Error loading page")

async def handle_discord_back_to_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle going back to Discord source selection"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in discord_route_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    try:
        state = discord_route_states[user_id]
        available_channels = state["available_channels"]
        
        text = (
            "🔍 <b>Select Source Channel for Discord</b>\n\n"
            "Choose the Telegram channel where messages will come from:\n\n"
            f"📊 Available Channels: <b>{len(available_channels)}</b>"
        )
        
        keyboard = create_discord_source_selection_keyboard(available_channels, 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error handling Discord back to sources for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred.", get_navigation_keyboard())

# ========= ENHANCED LOGGING HELPERS =========
def log_error(error_msg: str, exception: Optional[Exception] = None) -> None:
    """Log errors to errors.txt with timestamp"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(ERRORS_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] ERROR: {error_msg}\n")
            if exception:
                f.write(f"[{timestamp}] EXCEPTION: {str(exception)}\n")
                f.write(f"[{timestamp}] EXCEPTION TYPE: {type(exception).__name__}\n")
            f.write("-" * 50 + "\n")
    except Exception as e:
        print(f"CRITICAL: Could not write to error log: {e}")

def log_activity(activity_msg: str) -> None:
    """Log activities to activities.txt with timestamp"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(ACTIVITIES_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {activity_msg}\n")
    except Exception as e:
        log_error(f"Could not write to activity log: {activity_msg}", e)

def log_media_forwarding(media_msg: str) -> None:
    """Log media forwarding activities"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(MEDIA_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {media_msg}\n")
    except Exception as e:
        log_error(f"Could not write to media log: {media_msg}", e)

# ========= ENHANCED PERSISTENCE HELPERS =========
def save_settings() -> None:
    """Save user settings to JSON file"""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_settings, f, indent=2, ensure_ascii=False)
        log_activity(f"Settings saved to {SETTINGS_FILE}")
    except Exception as e:
        log_error(f"Failed to save settings to {SETTINGS_FILE}", e)

def load_settings() -> Dict:
    """Load user settings from JSON file and return them"""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
            log_activity(f"Settings loaded from {SETTINGS_FILE}. {len(settings)} users found.")
            return settings
        else:
            log_activity(f"No existing settings file found.")
            return {}
    except Exception as e:
        log_error(f"Failed to load settings from {SETTINGS_FILE}", e)
        return {}

def refresh_user_settings() -> None:
    """Refresh the global user_settings from file"""
    global user_settings
    user_settings = load_settings()

def get_user_settings_fresh(user_id: int) -> Dict:
    """Get user settings directly from file (fresh read)"""
    fresh_settings = load_settings()
    return fresh_settings.get(str(user_id), {
        "routes": {}, 
        "forwarding": False, 
        "disabled_routes": {},
        "available_channels": {},
        "required_keywords": [],
        "blocked_keywords": [],
        "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
    })

# ========= PERFORMANCE MONITORING FUNCTIONS =========
async def show_deletion_performance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deletion performance statistics"""
    user_id = update.callback_query.from_user.id
    
    try:
        stats = deletion_performance.get_stats()
        
        if not stats:
            text = (
                "📊 <b>Deletion Performance</b>\n\n"
                "No deletion performance data available yet.\n\n"
                "Performance statistics will appear here after message deletions occur."
            )
        else:
            text = (
                f"📊 <b>Deletion Performance Statistics</b>\n\n"
                f"🔄 <b>Total Deletions:</b> {stats['total_deletions']}\n"
                f"✅ <b>Success Rate:</b> {stats['success_rate']:.1f}%\n"
                f"⚡ <b>Average Time:</b> {stats['average_time']:.3f}s\n"
                f"🚀 <b>Fastest:</b> {stats['min_time']:.3f}s\n"
                f"🐢 <b>Slowest:</b> {stats['max_time']:.3f}s\n"
                f"🔒 <b>Permission Errors:</b> {stats['permission_errors']}\n"
                f"❌ <b>Other Errors:</b> {stats['other_errors']}\n\n"
                f"💡 <b>Performance Tips:</b>\n"
                f"• Ensure bot has delete permissions in ALL destination channels\n"
                f"• Monitor permission errors above\n"
                f"• Target < 0.5s for optimal performance\n"
                f"• Contact support if success rate drops below 90%"
            )
        
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh Stats", callback_data="menu_deletion_stats")],
            [InlineKeyboardButton("📋 Check Permissions", callback_data="menu_check_permissions")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error showing deletion performance for user {user_id}", e)
        await safe_edit_message(update, "❌ Error loading deletion performance.", get_navigation_keyboard())

async def check_bot_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check bot permissions in all destination channels"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        routes = user_settings.get("routes", {})
        
        if not routes:
            text = "❌ No routes found. Please add routes first."
            await safe_edit_message(update, text, get_navigation_keyboard())
            return
        
        permission_issues = []
        working_channels = []
        
        # Check permissions for all destination channels
        for source_key, targets in routes.items():
            for target_key in targets:
                try:
                    if target_key.lstrip('-').isdigit():
                        target_entity = await client.get_entity(int(target_key))
                    else:
                        if not target_key.startswith('@'):
                            target_entity = await client.get_entity('@' + target_key)
                        else:
                            target_entity = await client.get_entity(target_key)
                    
                    # Try to get bot's permissions in the channel
                    try:
                        # This will fail if bot doesn't have admin permissions
                        participant = await client(GetParticipantRequest(target_entity, 'me'))
                        working_channels.append(f"✅ {get_channel_display_name(target_entity)}")
                    except (ChatAdminRequiredError, ChannelPrivateError):
                        permission_issues.append(f"❌ {get_channel_display_name(target_entity)} - Missing admin permissions")
                    except Exception:
                        permission_issues.append(f"❌ {get_channel_display_name(target_entity)} - Cannot access channel")
                        
                except Exception as e:
                    permission_issues.append(f"❌ {target_key} - Error: {str(e)}")
        
        if not permission_issues and working_channels:
            text = (
                "✅ <b>All Permissions Verified!</b>\n\n"
                "The bot has proper permissions in all destination channels:\n\n" +
                "\n".join(working_channels) +
                "\n\n💡 <b>Instant deletion is fully operational!</b>"
            )
        elif permission_issues:
            text = (
                "⚠️ <b>Permission Issues Detected</b>\n\n"
                "The following channels have permission issues:\n\n" +
                "\n".join(permission_issues) +
                "\n\n✅ Working channels:\n" +
                "\n".join(working_channels) +
                "\n\n🔧 <b>To fix:</b>\n"
                "• Ensure bot is admin in destination channels\n"
                "• Grant 'Delete Messages' permission\n"
                "• Check if channel is accessible"
            )
        else:
            text = "❌ No channels could be checked."
        
        keyboard = [
            [InlineKeyboardButton("🔄 Recheck Permissions", callback_data="menu_check_permissions")],
            [InlineKeyboardButton("📊 Deletion Stats", callback_data="menu_deletion_stats")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error checking bot permissions for user {user_id}", e)
        await safe_edit_message(update, "❌ Error checking permissions.", get_navigation_keyboard())

# ========= MEDIA TYPE FILTERING FUNCTIONS =========
async def start_media_filter_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the media type filtering management interface"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        allowed_media_types = user_settings.get("allowed_media_types", list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"])
        
        media_filter_states[user_id] = {
            "allowed_media_types": allowed_media_types.copy(),
            "editing_mode": None
        }
        
        selected_count = len(allowed_media_types)
        total_count = len(SUPPORTED_MEDIA_TYPES) + 1
        
        text = (
            "🖼️ <b>Media Type Filter Management</b>\n\n"
            "Select which types of media and messages should be forwarded:\n\n"
            f"📊 Status: <b>{selected_count}/{total_count}</b> media types allowed\n\n"
            "💡 <b>How it works:</b>\n"
            "• Only messages with allowed media types will be forwarded\n"
            "• Text-only messages are controlled separately\n"
            "• Changes take effect immediately\n"
            "• All types are allowed by default"
        )
        
        keyboard = get_media_filter_management_keyboard(allowed_media_types)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error starting media filter management for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading media filter settings.", get_navigation_keyboard())

def get_media_filter_management_keyboard(allowed_media_types: List[str]):
    """Create keyboard for media filter management with toggle buttons"""
    keyboard = []
    
    for media_type, display_name in MEDIA_TYPE_DISPLAY_NAMES.items():
        is_allowed = media_type in allowed_media_types
        checkbox = "✅" if is_allowed else "❌"
        button_text = f"{checkbox} {display_name}"
        callback_data = f"toggle_media_{media_type}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    action_buttons = [
        [InlineKeyboardButton("✅ Allow All Media Types", callback_data="allow_all_media")],
        [InlineKeyboardButton("❌ Block All Media Types", callback_data="block_all_media")],
        [InlineKeyboardButton("💾 Save Media Filters", callback_data="save_media_filters")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    
    keyboard.extend(action_buttons)
    return InlineKeyboardMarkup(keyboard)

async def handle_media_type_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str) -> None:
    """Toggle individual media type allowance"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in media_filter_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = media_filter_states[user_id]
    allowed_media_types = state["allowed_media_types"]
    
    if media_type in allowed_media_types:
        allowed_media_types.remove(media_type)
        action = "blocked"
        emoji = "❌"
    else:
        allowed_media_types.append(media_type)
        action = "allowed"
        emoji = "✅"
    
    display_name = MEDIA_TYPE_DISPLAY_NAMES.get(media_type, media_type)
    await update.callback_query.answer(f"{emoji} {display_name} {action}")
    
    selected_count = len(allowed_media_types)
    total_count = len(SUPPORTED_MEDIA_TYPES) + 1
    
    text = (
        "🖼️ <b>Media Type Filter Management</b>\n\n"
        "Select which types of media and messages should be forwarded:\n\n"
        f"📊 Status: <b>{selected_count}/{total_count}</b> media types allowed\n\n"
        "💡 <b>How it works:</b>\n"
        "• Only messages with allowed media types will be forwarded\n"
        "• Text-only messages are controlled separately\n"
        "• Changes take effect immediately\n"
        "• All types are allowed by default"
    )
    
    keyboard = get_media_filter_management_keyboard(allowed_media_types)
    await safe_edit_message(update, text, keyboard)

async def handle_bulk_media_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    """Handle bulk media filter actions (allow all/block all)"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in media_filter_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = media_filter_states[user_id]
    
    if action == "allow_all_media":
        state["allowed_media_types"] = list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
        await update.callback_query.answer("✅ All media types allowed")
    elif action == "block_all_media":
        state["allowed_media_types"] = []
        await update.callback_query.answer("❌ All media types blocked")
    
    selected_count = len(state["allowed_media_types"])
    total_count = len(SUPPORTED_MEDIA_TYPES) + 1
    
    text = (
        "🖼️ <b>Media Type Filter Management</b>\n\n"
        "Select which types of media and messages should be forwarded:\n\n"
        f"📊 Status: <b>{selected_count}/{total_count}</b> media types allowed\n\n"
        "💡 <b>How it works:</b>\n"
        "• Only messages with allowed media types will be forwarded\n"
        "• Text-only messages are controlled separately\n"
        "• Changes take effect immediately\n"
        "• All types are allowed by default"
    )
    
    keyboard = get_media_filter_management_keyboard(state["allowed_media_types"])
    await safe_edit_message(update, text, keyboard)

async def save_media_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save media filter settings to user settings"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in media_filter_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = media_filter_states[user_id]
    
    user_id_str = str(user_id)
    refresh_user_settings()
    
    if user_id_str not in user_settings:
        user_settings[user_id_str] = {
            "routes": {},
            "forwarding": False,
            "disabled_routes": {},
            "available_channels": {},
            "required_keywords": [],
            "blocked_keywords": [],
            "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
        }
    
    user_settings[user_id_str]["allowed_media_types"] = state["allowed_media_types"]
    save_settings()
    
    del media_filter_states[user_id]
    
    selected_count = len(state["allowed_media_types"])
    total_count = len(SUPPORTED_MEDIA_TYPES) + 1
    
    text = (
        f"✅ <b>Media Filter Settings Saved!</b>\n\n"
        f"📊 <b>Summary:</b>\n"
        f"• Allowed Media Types: {selected_count}/{total_count}\n\n"
        f"💡 <b>Filtering is now active!</b>\n"
        f"Only messages with allowed media types will be forwarded."
    )
    
    await safe_edit_message(update, text, get_navigation_keyboard())
    log_activity(f"User {user_id} saved media filters: {selected_count}/{total_count} types allowed")

def check_media_type_allowed(media_type: str, user_id: int) -> bool:
    """Check if a media type is allowed for forwarding"""
    user_settings = get_user_settings_fresh(user_id)
    allowed_media_types = user_settings.get("allowed_media_types", list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"])
    return media_type in allowed_media_types

# ========= KEYWORD FILTERING FUNCTIONS =========
def check_keyword_filtering(text: str, required_keywords: List[str], blocked_keywords: List[str], media_type: str = None) -> bool:
    """
    Check if message passes keyword filtering
    Returns True if message should be forwarded, False if blocked
    """
    if media_type == "sticker":
        return True
    
    if not text:
        return len(required_keywords) == 0
    
    text_lower = text.lower()
    
    if required_keywords:
        found_required = False
        for keyword in required_keywords:
            if keyword.lower() in text_lower:
                found_required = True
                break
        
        if not found_required:
            return False
    
    if blocked_keywords:
        for keyword in blocked_keywords:
            if keyword.lower() in text_lower:
                return False
    
    return True

async def start_keyword_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the keyword management interface"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        required_keywords = user_settings.get("required_keywords", [])
        blocked_keywords = user_settings.get("blocked_keywords", [])
        
        keyword_management_states[user_id] = {
            "required_keywords": required_keywords.copy(),
            "blocked_keywords": blocked_keywords.copy(),
            "editing_mode": None
        }
        
        text = (
            "🔤 <b>Keyword Filter Management</b>\n\n"
            "Configure keywords to control which messages get forwarded:\n\n"
            f"✅ <b>Required Keywords</b> ({len(required_keywords)}):\n"
            f"{', '.join(required_keywords) if required_keywords else 'None'}\n\n"
            f"❌ <b>Blocked Keywords</b> ({len(blocked_keywords)}):\n"
            f"{', '.join(blocked_keywords) if blocked_keywords else 'None'}\n\n"
            "💡 <b>How it works:</b>\n"
            "• <b>Stickers:</b> ALWAYS forwarded (ignore keywords)\n"
            "• <b>Required:</b> AT LEAST ONE keyword must be in message\n"
            "• <b>Blocked:</b> NONE of the keywords can be in message\n"
            "• Messages without text are only blocked if required keywords exist"
        )
        
        keyboard = get_keyword_management_keyboard()
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error starting keyword management for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading keyword settings.", get_navigation_keyboard())

def get_keyword_management_keyboard():
    """Create keyboard for keyword management"""
    keyboard = [
        [InlineKeyboardButton("✅ Manage Required Keywords", callback_data="edit_required_keywords")],
        [InlineKeyboardButton("❌ Manage Blocked Keywords", callback_data="edit_blocked_keywords")],
        [InlineKeyboardButton("🔄 Reset All Keywords", callback_data="reset_all_keywords")],
        [InlineKeyboardButton("💡 How Filtering Works", callback_data="keyword_filtering_help")],
        [InlineKeyboardButton("💾 Save Keywords", callback_data="save_keyword_settings")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_keyword_editing_keyboard(keyword_type: str, keywords: List[str]):
    """Create keyboard for editing specific keyword type"""
    keyboard = []
    
    for i, keyword in enumerate(keywords):
        display_text = f"🗑️ {keyword}"
        if len(display_text) > 30:
            display_text = display_text[:27] + "..."
        keyboard.append([
            InlineKeyboardButton(display_text, callback_data=f"remove_keyword_{keyword_type}_{i}")
        ])
    
    action_buttons = [
        InlineKeyboardButton("➕ Add Keyword", callback_data=f"add_keyword_{keyword_type}"),
        InlineKeyboardButton("🗑️ Clear All", callback_data=f"clear_keywords_{keyword_type}"),
        InlineKeyboardButton("💾 Save Keywords", callback_data="save_keyword_settings"),
        InlineKeyboardButton("↩️ Back to Management", callback_data="menu_keyword_management")
    ]
    
    for i in range(0, len(action_buttons), 2):
        if i + 1 < len(action_buttons):
            keyboard.append([action_buttons[i], action_buttons[i + 1]])
        else:
            keyboard.append([action_buttons[i]])
    
    return InlineKeyboardMarkup(keyboard)

async def handle_keyword_editing_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle entering keyword editing mode - FIXED VERSION"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in keyword_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    # Determine keyword_type from callback data
    data = update.callback_query.data
    if data == "edit_required_keywords":
        keyword_type = "required"
    elif data == "edit_blocked_keywords":
        keyword_type = "blocked"
    else:
        await update.callback_query.answer("Invalid keyword type")
        return
    
    state = keyword_management_states[user_id]
    state["editing_mode"] = keyword_type
    
    keywords = state[f"{keyword_type}_keywords"]
    type_display = "Required" if keyword_type == "required" else "Blocked"
    emoji = "✅" if keyword_type == "required" else "❌"
    
    text = (
        f"{emoji} <b>Editing {type_display} Keywords</b>\n\n"
        f"Current {type_display.lower()} keywords ({len(keywords)}):\n"
        f"{', '.join(keywords) if keywords else 'None'}\n\n"
        f"💡 <b>Click 🗑️ to remove a keyword</b>\n"
        f"Or use the buttons below to add new keywords."
    )
    
    keyboard = get_keyword_editing_keyboard(keyword_type, keywords)
    await safe_edit_message(update, text, keyboard)

async def handle_keyword_removal(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword_type: str, index: int) -> None:
    """Remove a keyword from the list and save immediately"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in keyword_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = keyword_management_states[user_id]
    keywords = state[f"{keyword_type}_keywords"]
    
    if 0 <= index < len(keywords):
        removed_keyword = keywords.pop(index)
        await update.callback_query.answer(f"🗑️ Removed: {removed_keyword}")
        
        await save_keyword_changes(user_id)
    else:
        await update.callback_query.answer("❌ Keyword not found")
        return
    
    type_display = "Required" if keyword_type == "required" else "Blocked"
    emoji = "✅" if keyword_type == "required" else "❌"
    
    text = (
        f"{emoji} <b>Editing {type_display} Keywords</b>\n\n"
        f"Current {type_display.lower()} keywords ({len(keywords)}):\n"
        f"{', '.join(keywords) if keywords else 'None'}\n\n"
        f"💡 <b>Click 🗑️ to remove a keyword</b>\n"
        f"Or use the buttons below to add new keywords."
    )
    
    keyboard = get_keyword_editing_keyboard(keyword_type, keywords)
    await safe_edit_message(update, text, keyboard)

async def handle_clear_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword_type: str) -> None:
    """Clear all keywords of a specific type and save immediately"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in keyword_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = keyword_management_states[user_id]
    state[f"{keyword_type}_keywords"] = []
    
    await save_keyword_changes(user_id)
    
    type_display = "Required" if keyword_type == "required" else "Blocked"
    emoji = "✅" if keyword_type == "required" else "❌"
    
    await update.callback_query.answer(f"🗑️ Cleared all {type_display.lower()} keywords")
    
    text = (
        f"{emoji} <b>Editing {type_display} Keywords</b>\n\n"
        f"Current {type_display.lower()} keywords (0):\n"
        f"None\n\n"
        f"💡 <b>Click 🗑️ to remove a keyword</b>\n"
        f"Or use the buttons below to add new keywords."
    )
    
    keyboard = get_keyword_editing_keyboard(keyword_type, [])
    await safe_edit_message(update, text, keyboard)

async def handle_add_keyword_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enter add keyword mode - FIXED VERSION"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in keyword_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    # Determine keyword_type from callback data
    data = update.callback_query.data
    if data == "add_keyword_required":
        keyword_type = "required"
    elif data == "add_keyword_blocked":
        keyword_type = "blocked"
    else:
        await update.callback_query.answer("Invalid keyword type")
        return
    
    state = keyword_management_states[user_id]
    state["editing_mode"] = f"adding_{keyword_type}"
    
    type_display = "Required" if keyword_type == "required" else "Blocked"
    emoji = "✅" if keyword_type == "required" else "❌"
    
    text = (
        f"{emoji} <b>Add {type_display} Keyword</b>\n\n"
        f"Please send me the keyword you want to add to {type_display.lower()} list.\n\n"
        f"💡 <b>Tips:</b>\n"
        f"• Keywords are case-insensitive\n"
        f"• Use specific words for better filtering\n"
        f"• You can add multiple keywords one by one\n\n"
        f"Send your keyword now or use the button to go back."
    )
    
    keyboard = [
        [InlineKeyboardButton("↩️ Back to Editing", callback_data=f"edit_{keyword_type}_keywords")]
    ]
    await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))

async def handle_keyword_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle keyword input from user and save immediately"""
    user_id = update.message.from_user.id
    user_input = update.message.text.strip()
    
    if user_id not in keyword_management_states:
        return
    
    state = keyword_management_states[user_id]
    editing_mode = state.get("editing_mode")
    
    if not editing_mode or not editing_mode.startswith("adding_"):
        return
    
    keyword_type = editing_mode.replace("adding_", "")
    
    if not user_input:
        await safe_reply(update, "❌ Please enter a valid keyword.", get_navigation_keyboard())
        return
    
    if user_input not in state[f"{keyword_type}_keywords"]:
        state[f"{keyword_type}_keywords"].append(user_input)
        
        await save_keyword_changes(user_id)
        
        await safe_reply(update, f"✅ Added '{user_input}' to {keyword_type} keywords", get_navigation_keyboard())
    else:
        await safe_reply(update, f"⚠️ Keyword '{user_input}' already exists", get_navigation_keyboard())
    
    type_display = "Required" if keyword_type == "required" else "Blocked"
    emoji = "✅" if keyword_type == "required" else "❌"
    keywords = state[f"{keyword_type}_keywords"]
    
    text = (
        f"{emoji} <b>Editing {type_display} Keywords</b>\n\n"
        f"Current {type_display.lower()} keywords ({len(keywords)}):\n"
        f"{', '.join(keywords) if keywords else 'None'}\n\n"
        f"💡 <b>Click 🗑️ to remove a keyword</b>\n"
        f"Or use the buttons below to add new keywords."
    )
    
    keyboard = get_keyword_editing_keyboard(keyword_type, keywords)
    await safe_reply(update, text, keyboard)

async def save_keyword_changes(user_id: int) -> None:
    """Save keyword changes to user settings immediately"""
    user_id_str = str(user_id)
    
    if user_id not in keyword_management_states:
        return
    
    state = keyword_management_states[user_id]
    
    refresh_user_settings()
    
    if user_id_str not in user_settings:
        user_settings[user_id_str] = {
            "routes": {},
            "forwarding": False,
            "disabled_routes": {},
            "available_channels": {},
            "required_keywords": [],
            "blocked_keywords": [],
            "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
        }
    
    user_settings[user_id_str]["required_keywords"] = state["required_keywords"]
    user_settings[user_id_str]["blocked_keywords"] = state["blocked_keywords"]
    save_settings()
    
    log_activity(f"User {user_id} saved keyword changes: {len(state['required_keywords'])} required, {len(state['blocked_keywords'])} blocked")

async def save_keyword_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save keyword settings to user settings - explicit save"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in keyword_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = keyword_management_states[user_id]
    
    await save_keyword_changes(user_id)
    
    del keyword_management_states[user_id]
    
    required_count = len(state["required_keywords"])
    blocked_count = len(state["blocked_keywords"])
    
    text = (
        f"✅ <b>Keyword Settings Saved!</b>\n\n"
        f"📊 <b>Summary:</b>\n"
        f"• ✅ Required Keywords: {required_count}\n"
        f"• ❌ Blocked Keywords: {blocked_count}\n\n"
        f"💡 <b>Filtering is now active!</b>\n"
        f"Messages will only be forwarded if they pass your keyword filters.\n"
        f"⭐ <b>Note:</b> Stickers are always forwarded (ignore keywords)"
    )
    
    await safe_edit_message(update, text, get_navigation_keyboard())
    log_activity(f"User {user_id} explicitly saved keyword settings: {required_count} required, {blocked_count} blocked")

async def handle_reset_all_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset all keyword settings and save immediately"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in keyword_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = keyword_management_states[user_id]
    state["required_keywords"] = []
    state["blocked_keywords"] = []
    
    await save_keyword_changes(user_id)
    
    await update.callback_query.answer("🔄 All keywords reset")
    
    text = (
        "🔤 <b>Keyword Filter Management</b>\n\n"
        "Configure keywords to control which messages get forwarded:\n\n"
        f"✅ <b>Required Keywords</b> (0):\n"
        f"None\n\n"
        f"❌ <b>Blocked Keywords</b> (0):\n"
        f"None\n\n"
        "💡 <b>How it works:</b>\n"
        "• <b>Stickers:</b> ALWAYS forwarded (ignore keywords)\n"
        "• <b>Required:</b> AT LEAST ONE must be present in message\n"
        "• <b>Blocked:</b> NONE of the keywords can be in message\n"
        "• Messages without text are only blocked if required keywords exist"
    )
    
    keyboard = get_keyword_management_keyboard()
    await safe_edit_message(update, text, keyboard)

async def show_keyword_filtering_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed help about keyword filtering"""
    text = (
        "💡 <b>Keyword Filtering Help</b>\n\n"
        "**🔤 How Filtering Works:**\n"
        "• <b>Stickers:</b> ALWAYS forwarded (ignore all keywords)\n"
        "• <b>Required Keywords:</b> AT LEAST ONE must be present in message text\n"
        "• <b>Blocked Keywords:</b> NONE can be present in message text\n"
        "• <b>Case Insensitive:</b> Filtering is not case-sensitive\n"
        "• <b>Text Messages Only:</b> Only messages with text content are filtered\n\n"
        "📝 **Examples:**\n"
        "• <b>Required:</b> 'news', 'update' → Message must contain EITHER word\n"
        "• <b>Blocked:</b> 'spam', 'advertisement' → Message must contain NEITHER word\n"
        "• <b>Combined:</b> Required 'important', Blocked 'test' → Message must contain 'important' but NOT 'test'\n"
        "• <b>Stickers:</b> Always forwarded regardless of keywords\n\n"
        "• <b>Media-only messages:</b> Only blocked if required keywords exist\n"
        "• <b>Empty messages:</b> Same as media-only messages\n"
        "• <b>No filters set:</b> All messages pass through\n\n"
        "⚙️ **Usage Tips:**\n"
        "• Use specific words for better accuracy\n"
        "• Start with a few keywords and adjust as needed\n"
        "• Test your filters with different message types\n"
        "• Stickers always bypass keyword filtering"
    )
    
    keyboard = [
        [InlineKeyboardButton("↩️ Back to Management", callback_data="menu_keyword_management")]
    ]
    await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))

# ========= ENHANCED CHANNEL FETCHING FUNCTIONS =========
async def get_user_channels(user_id: int) -> List[Dict[str, Any]]:
    """Get all channels and groups that the user is a member of"""
    try:
        channels = []
        
        dialogs = await client.get_dialogs(limit=400)
        
        for dialog in dialogs:
            entity = dialog.entity
            
            if isinstance(entity, Channel):
                try:
                    await client(GetParticipantRequest(entity, user_id))
                    
                    channel_info = {
                        "id": entity.id,
                        "title": getattr(entity, 'title', 'Unknown'),
                        "username": getattr(entity, 'username', None),
                        "participants_count": getattr(entity, 'participants_count', 0),
                        "is_megagroup": getattr(entity, 'megagroup', False),
                        "access_hash": entity.access_hash
                    }
                    
                    channels.append(channel_info)
                    
                except (UserNotParticipantError, ChannelPrivateError):
                    continue
                except Exception as e:
                    continue
        
        channels.sort(key=lambda x: x["title"].lower())
        return channels
        
    except Exception as e:
        log_error(f"Error fetching channels for user {user_id}", e)
        return []

def create_channels_keyboard(channels: List[Dict], step: str, page: int = 0, items_per_page: int = 10) -> InlineKeyboardMarkup:
    """Create a paginated keyboard for channel selection"""
    keyboard = []
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_channels = channels[start_idx:end_idx]
    
    for channel in page_channels:
        display_name = f"📢 {channel['title']}"
        if channel['username']:
            display_name += f" (@{channel['username']})"
        else:
            display_name += f" (ID: {channel['id']})"
        
        if len(display_name) > 40:
            display_name = display_name[:37] + "..."
        
        callback_data = f"select_channel_{step}_{channel['id']}_{page}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=callback_data)])
    
    navigation_buttons = []
    total_pages = (len(channels) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"channels_page_{step}_{page-1}"))
    
    if end_idx < len(channels):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"channels_page_{step}_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    # keyboard.append([InlineKeyboardButton("✏️ Enter Manually", callback_data=f"manual_input_{step}")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_main")])
    
    return InlineKeyboardMarkup(keyboard)

def create_channel_selection_keyboard(channels: List[Dict], selected_channels: Dict, page: int = 0, items_per_page: int = 10) -> InlineKeyboardMarkup:
    """Create a paginated keyboard for channel selection with checkboxes"""
    keyboard = []
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_channels = channels[start_idx:end_idx]
    
    for channel in page_channels:
        channel_key = channel["username"] or str(channel["id"])
        is_selected = channel_key in selected_channels
        
        display_name = f"📢 {channel['title']}"
        if channel['username']:
            display_name += f" (@{channel['username']})"
        else:
            display_name += f" (ID: {channel['id']})"
        
        if len(display_name) > 40:
            display_name = display_name[:37] + "..."
        
        checkbox = "☑️" if is_selected else "⬜"
        display_name = f"{checkbox} {display_name}"
        
        callback_data = f"toggle_channel_{channel['id']}_{page}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=callback_data)])
    
    navigation_buttons = []
    total_pages = (len(channels) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"channel_sel_page_{page-1}"))
    
    if end_idx < len(channels):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"channel_sel_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    selected_count = len(selected_channels)
    keyboard.extend([
        [InlineKeyboardButton(f"✅ Save Selection ({selected_count} channels)", callback_data="save_channel_selection")],
        [InlineKeyboardButton("🔄 Select All", callback_data="select_all_channels")],
        [InlineKeyboardButton("🗑️ Clear All", callback_data="clear_all_channels")],
        [InlineKeyboardButton("⚙️ Manage Saved Channels", callback_data="menu_manage_channels")],
        [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
        [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ])
    
    return InlineKeyboardMarkup(keyboard)

def create_channel_management_keyboard(available_channels: Dict, page: int = 0, items_per_page: int = 10) -> InlineKeyboardMarkup:
    """Create a paginated keyboard for channel management with delete buttons"""
    keyboard = []
    
    channels_list = list(available_channels.items())
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_channels = channels_list[start_idx:end_idx]
    
    for channel_key, channel_info in page_channels:
        display_name = f"📢 {channel_info['title']}"
        if channel_info['username']:
            display_name += f" (@{channel_info['username']})"
        else:
            display_name += f" (ID: {channel_info['id']})"
        
        if len(display_name) > 35:
            display_name = display_name[:32] + "..."
        
        delete_button = InlineKeyboardButton("🗑️ Remove", callback_data=f"remove_channel_{channel_key}_{page}")
        keyboard.append([
            InlineKeyboardButton(display_name, callback_data=f"view_channel_{channel_key}"),
            delete_button
        ])
    
    navigation_buttons = []
    total_pages = (len(channels_list) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"channel_mgmt_page_{page-1}"))
    
    if end_idx < len(channels_list):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"channel_mgmt_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    keyboard.extend([
        [InlineKeyboardButton("🗑️ Remove All Channels", callback_data="remove_all_channels")],
        [InlineKeyboardButton("📋 Select More Channels", callback_data="menu_select_channels")],
        [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
        [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
        [InlineKeyboardButton("➕ Add Route", callback_data="menu_add_route")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ])
    
    return InlineKeyboardMarkup(keyboard)

# ========= COMPREHENSIVE UI HELPERS WITH ROUTE MANAGEMENT =========
def get_main_menu_keyboard():
    """Create the main menu keyboard with enhanced options"""
    keyboard = [
        [
            InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels"),
            InlineKeyboardButton("⚙️ Manage Channels", callback_data="menu_manage_channels")
        ],
        [
            InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management"),
            InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")
        ],
        [
            InlineKeyboardButton("➕ Add Route", callback_data="menu_add_route"),
            InlineKeyboardButton("📋 View Routes", callback_data="menu_list_routes")
        ],
        [
            InlineKeyboardButton("⚙️ Manage Routes", callback_data="menu_manage_routes"),
            InlineKeyboardButton("✏️ Quick Add", callback_data="menu_quick_add")
        ],
        [
            InlineKeyboardButton("🔗 Discord Routes", callback_data="menu_discord_routes")
        ],
        [
            InlineKeyboardButton("🚀 Start All", callback_data="menu_start_forward"),
            InlineKeyboardButton("🛑 Stop All", callback_data="menu_stop_forward")
        ],
        [
            InlineKeyboardButton("📊 Status", callback_data="menu_status"),
            InlineKeyboardButton("🖼️ Media Stats", callback_data="menu_media_stats")
        ],
        [
            InlineKeyboardButton("⚡ Deletion Stats", callback_data="menu_deletion_stats"),
            InlineKeyboardButton("🔒 Check Permissions", callback_data="menu_check_permissions")
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="menu_help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_navigation_keyboard():
    """Create navigation keyboard for all pages with enhanced options"""
    keyboard = [
        [
            InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels"),
            InlineKeyboardButton("⚙️ Manage Channels", callback_data="menu_manage_channels")
        ],
        [
            InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management"),
            InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")
        ],
        [
            InlineKeyboardButton("➕ Add Route", callback_data="menu_add_route"),
            InlineKeyboardButton("📋 View Routes", callback_data="menu_list_routes")
        ],
        [
            InlineKeyboardButton("⚙️ Manage Routes", callback_data="menu_manage_routes"),
            InlineKeyboardButton("✏️ Quick Add", callback_data="menu_quick_add")
        ],
        [
            InlineKeyboardButton("🔗 Discord Routes", callback_data="menu_discord_routes")
        ],
        [
            InlineKeyboardButton("🚀 Start All", callback_data="menu_start_forward"),
            InlineKeyboardButton("🛑 Stop All", callback_data="menu_stop_forward")
        ],
        [
            InlineKeyboardButton("📊 Status", callback_data="menu_status"),
            InlineKeyboardButton("🖼️ Media Stats", callback_data="menu_media_stats")
        ],
        [
            InlineKeyboardButton("⚡ Deletion Stats", callback_data="menu_deletion_stats"),
            InlineKeyboardButton("🔒 Check Permissions", callback_data="menu_check_permissions")
        ],
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def parse_route_callback_data(data: str, prefix: str) -> tuple:
    """Parse callback data for routes that might contain special characters"""
    try:
        parts = data.split("_")
        prefix_parts = prefix.split("_")
        
        route_key_start = len(prefix_parts)
        route_key_end = -1
        
        route_key = "_".join(parts[route_key_start:route_key_end])
        page = int(parts[route_key_end])
        
        return route_key, page
    except Exception as e:
        log_error(f"Error parsing callback data: {data}", e)
        return None, 0

def get_route_management_keyboard(routes_data, page: int = 0, items_per_page: int = 8):
    """Create keyboard for route management with enable/disable toggles using checkboxes - MODIFIED to show usernames"""
    keyboard = []
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_routes = list(routes_data.items())[start_idx:end_idx]
    
    for route_key, route_info in page_routes:
        # Use the display names which now contain usernames instead of IDs
        display_name = f"{route_info['source_display']} → {route_info['target_display']}"
        if len(display_name) > 28:
            display_name = display_name[:25] + "..."
        
        checkbox = "✅" if not route_info['disabled'] else "⏸️"
        display_name = f"{checkbox} {display_name}"
        
        callback_data = f"toggle_route_{route_key}_{page}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=callback_data)])
    
    navigation_buttons = []
    total_pages = (len(routes_data) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"routes_page_{page-1}"))
    
    if end_idx < len(routes_data):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"routes_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    keyboard.extend([
        [InlineKeyboardButton("✅ Enable All Routes", callback_data="enable_all_routes")],
        [InlineKeyboardButton("⏸️ Disable All Routes", callback_data="disable_all_routes")],
        [InlineKeyboardButton("🗑 Delete Routes", callback_data="delete_routes_mode")],
        [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
        [InlineKeyboardButton("⚙️ Manage Channels", callback_data="menu_manage_channels")],
        [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
        [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
        [InlineKeyboardButton("✏️ Quick Add Route", callback_data="menu_quick_add")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_route_deletion_keyboard(routes_data, selected_routes=None, page: int = 0):
    """Create keyboard for route deletion with checkboxes that toggle on click - MODIFIED to show usernames"""
    keyboard = []
    
    if selected_routes is None:
        selected_routes = set()
    
    items_per_page = 8
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_routes = list(routes_data.items())[start_idx:end_idx]
    
    for route_key, route_info in page_routes:
        # Use the display names which now contain usernames instead of IDs
        display_name = f"{route_info['source_display']} → {route_info['target_display']}"
        if len(display_name) > 28:
            display_name = display_name[:25] + "..."
        
        checkbox = "☑️" if route_key in selected_routes else "⬜"
        display_name = f"{checkbox} {display_name}"
        
        callback_data = f"toggle_delete_{route_key}_{page}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=callback_data)])
    
    navigation_buttons = []
    total_pages = (len(routes_data) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"delete_page_{page-1}"))
    
    if end_idx < len(routes_data):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"delete_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    selected_count = len(selected_routes)
    keyboard.extend([
        [InlineKeyboardButton(f"🗑 Delete Selected ({selected_count})", callback_data="confirm_deletion")],
        [InlineKeyboardButton("✅ Select All", callback_data="select_all_routes")],
        [InlineKeyboardButton("❌ Clear Selection", callback_data="clear_selection")],
        [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
        [InlineKeyboardButton("⚙️ Manage Channels", callback_data="menu_manage_channels")],
        [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
        [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
        [InlineKeyboardButton("✏️ Quick Add Route", callback_data="menu_quick_add")],
        [InlineKeyboardButton("↩️ Back to Management", callback_data="menu_manage_routes")]
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_route_deletion_keyboard(routes_data, selected_routes=None, page: int = 0):
    """Create keyboard for route deletion with checkboxes that toggle on click"""
    keyboard = []
    
    if selected_routes is None:
        selected_routes = set()
    
    items_per_page = 8
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_routes = list(routes_data.items())[start_idx:end_idx]
    
    for route_key, route_info in page_routes:
        source_display = get_stored_key_display(route_info['source'])
        target_display = get_stored_key_display(route_info['target'])
        
        display_name = f"{source_display} → {target_display}"
        if len(display_name) > 28:
            display_name = display_name[:25] + "..."
        
        checkbox = "☑️" if route_key in selected_routes else "⬜"
        display_name = f"{checkbox} {display_name}"
        
        callback_data = f"toggle_delete_{route_key}_{page}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=callback_data)])
    
    navigation_buttons = []
    total_pages = (len(routes_data) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"delete_page_{page-1}"))
    
    if end_idx < len(routes_data):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"delete_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    selected_count = len(selected_routes)
    keyboard.extend([
        [InlineKeyboardButton(f"🗑 Delete Selected ({selected_count})", callback_data="confirm_deletion")],
        [InlineKeyboardButton("✅ Select All", callback_data="select_all_routes")],
        [InlineKeyboardButton("❌ Clear Selection", callback_data="clear_selection")],
        [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
        [InlineKeyboardButton("⚙️ Manage Channels", callback_data="menu_manage_channels")],
        [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
        [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
        [InlineKeyboardButton("✏️ Quick Add Route", callback_data="menu_quick_add")],
        [InlineKeyboardButton("↩️ Back to Management", callback_data="menu_manage_routes")]
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_channel_selection_keyboard(step: str, user_id: int):
    """Create keyboard for manual channel input with navigation"""
    route_creation_states[user_id] = {"step": step}
    
    keyboard = [
        [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
        [InlineKeyboardButton("⚙️ Manage Channels", callback_data="menu_manage_channels")],
        [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
        [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        [InlineKeyboardButton("➕ Add Route", callback_data="menu_add_route")],
        [InlineKeyboardButton("📋 View Routes", callback_data="menu_list_routes")],
        [InlineKeyboardButton("❌ Cancel", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_quick_add_keyboard():
    """Create keyboard for quick add mode"""
    keyboard = [
        [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
        [InlineKeyboardButton("⚙️ Manage Channels", callback_data="menu_manage_channels")],
        [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
        [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        [InlineKeyboardButton("➕ Interactive Add", callback_data="menu_add_route")],
        [InlineKeyboardButton("📋 View Routes", callback_data="menu_list_routes")],
        [InlineKeyboardButton("❌ Cancel", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def safe_reply(update: Update, text: str, reply_markup=None) -> bool:
    """Safely reply to a message with error handling"""
    try:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        return True
    except Exception as e:
        log_error(f"Could not reply to user {update.message.from_user.id}: {text}", e)
        return False

async def safe_edit_message(update: Update, text: str, reply_markup=None) -> bool:
    """Safely edit a callback query message"""
    try:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        return True
    except Exception as e:
        log_error(f"Could not edit message: {text}", e)
        return False

# ========= ENHANCED HELPER FUNCTIONS =========
def parse_channel_input(raw: str) -> Dict[str, Union[str, int]]:
    """Parse channel input and determine if it's username or ID"""
    if not raw or not isinstance(raw, str):
        raise ValueError("Invalid channel input")
    
    s = raw.strip()
    if not s:
        raise ValueError("Invalid channel input")
    
    if s.startswith("-100") and s[4:].isdigit():
        return {"type": "id", "value": int(s)}
    
    if s.startswith("-") and s[1:].isdigit():
        return {"type": "id", "value": int(s)}
    
    if s.isdigit():
        return {"type": "id", "value": int(s)}
    
    if not s.startswith("@"):
        s = "@" + s
    return {"type": "username", "value": s}

async def resolve_channel(channel_input: str):
    """Resolve channel by username or ID with improved error handling"""
    try:
        parsed = parse_channel_input(channel_input)
        
        if parsed["type"] == "username":
            entity = await client.get_entity(parsed["value"])
            if not getattr(entity, "username", None) and not hasattr(entity, "id"):
                raise Exception(f"Invalid channel: {channel_input}")
        else:
            entity = await client.get_entity(parsed["value"])
        
        return entity, parsed
    except Exception as e:
        log_error(f"Channel resolution failed for {channel_input}", e)
        raise Exception(f"Could not resolve channel {channel_input}. Make sure it's a valid username (@channel) or ID (-1001234567890).")

async def check_membership(channel_input: str, user_id: int) -> bool:
    """Check if user is member of channel with comprehensive error handling"""
    try:
        entity, parsed = await resolve_channel(channel_input)
        await client(GetParticipantRequest(entity, user_id))
        
        channel_display = f"@{entity.username}" if getattr(entity, "username", None) else f"ID: {entity.id}"
        log_activity(f"User {user_id} membership confirmed for {channel_display}")
        return True
    except (UserNotParticipantError, ChannelPrivateError):
        log_activity(f"User {user_id} not a member of {channel_input}")
        return False
    except Exception as e:
        log_error(f"Membership check failed for {channel_input}, user {user_id}", e)
        return False

async def notify_user_non_technical(user_id: int, message: str) -> None:
    """Send DM to user with error handling"""
    try:
        await tg_bot.send_message(chat_id=user_id, text=message)
        log_activity(f"Notification sent to user {user_id}: {message}")
    except Exception as e:
        log_error(f"Could not DM user {user_id}: {message}", e)

def stored_value_matches_chat(stored_key: str, chat) -> bool:
    """Check if stored channel key matches current chat (works with both usernames and IDs)"""
    try:
        if not stored_key:
            return False
        
        if stored_key.lstrip("-").isdigit():
            stored_id = int(stored_key)
            return stored_id == chat.id
        
        stored_username = stored_key.lstrip("@")
        if getattr(chat, "username", None):
            return stored_username == chat.username
        
        return False
    except Exception as e:
        log_error(f"Error matching stored value {stored_key} with chat", e)
        return False

def get_channel_display_name(entity) -> str:
    """Get a user-friendly display name for a channel"""
    if getattr(entity, "username", None):
        return f"@{entity.username}"
    else:
        title = getattr(entity, "title", "Unknown Channel")
        return f"{title} (ID: {entity.id})"

def get_stored_key_display(stored_key: str) -> str:
    """Convert stored key to user-friendly display"""
    if stored_key.lstrip("-").isdigit():
        return f"ID: {stored_key}"
    else:
        return f"@{stored_key.lstrip('@')}"

# ========= ENHANCED MEDIA FORWARDING FUNCTIONS =========
def should_forward_message(event, user_id: int) -> bool:
    """Determine if a message should be forwarded based on its type and user's media filters"""
    if isinstance(event.message, MessageService):
        return False
    
    has_media = event.message.media is not None
    has_text = event.message.message and event.message.message.strip() != ""
    
    if not has_media and not has_text:
        return False
    
    media_type = get_media_type(event)
    if not check_media_type_allowed(media_type, user_id):
        return False
    
    return True

def get_media_type(event) -> str:
    """Get the type of media in the message with enhanced detection for stickers"""
    if not event.message.media:
        return "text"
    
    media = event.message.media
    
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    elif isinstance(media, MessageMediaDocument):
        document = media.document
        if any(isinstance(attr, (DocumentAttributeSticker, DocumentAttributeCustomEmoji)) for attr in document.attributes):
            return "sticker"
        elif document.mime_type.startswith('image/'):
            return "photo"
        elif document.mime_type.startswith('video/'):
            return "video"
        elif document.mime_type.startswith('audio/'):
            return "audio"
        else:
            return "document"
    elif isinstance(media, MessageMediaWebPage):
        return "webpage"
    elif isinstance(media, MessageMediaContact):
        return "contact"
    elif isinstance(media, (MessageMediaGeo, MessageMediaVenue)):
        return "location"
    elif isinstance(media, MessageMediaGame):
        return "game"
    elif isinstance(media, MessageMediaInvoice):
        return "invoice"
    elif isinstance(media, MessageMediaPoll):
        return "poll"
    else:
        return "unknown"

def update_media_stats(user_id: int, media_type: str, success: bool = True):
    """Update media forwarding statistics"""
    user_id_str = str(user_id)
    if user_id_str not in media_forwarding_stats:
        media_forwarding_stats[user_id_str] = {}
    
    if media_type not in media_forwarding_stats[user_id_str]:
        media_forwarding_stats[user_id_str][media_type] = {"success": 0, "failed": 0}
    
    if success:
        media_forwarding_stats[user_id_str][media_type]["success"] += 1
    else:
        media_forwarding_stats[user_id_str][media_type]["failed"] += 1

async def download_media(event, media_type: str) -> Optional[bytes]:
    """Download media file with error handling"""
    try:
        if media_type in ["photo", "image"]:
            photo_data = await client.download_media(event.message.media, file=bytes)
            return photo_data
        elif media_type in ["video", "audio", "document", "sticker"]:
            document_data = await client.download_media(event.message.media, file=bytes)
            return document_data
        else:
            return None
    except Exception as e:
        log_error(f"Error downloading media of type {media_type}", e)
        return None

async def forward_media_message_enhanced(event, target_entity, user_id: int) -> Optional[int]:
    """Enhanced media forwarding WITHOUT forwarded from label - FIXED FOR STICKERS
    Returns the message ID of the forwarded message if successful, None otherwise"""
    media_type = get_media_type(event)
    user_id_str = str(user_id)
    
    try:
        # For stickers, use send_file specifically
        if media_type == "sticker":
            message = await client.send_file(target_entity, event.message.media)
            log_media_forwarding(f"User {user_id_str}: sticker forwarded without attribution")
            update_media_stats(user_id, "sticker", True)
            return message.id
        
        # For all other media types, use send_message instead of forward_messages to remove attribution
        if media_type == "text":
            # For text messages, just send the text
            if event.message.message and event.message.message.strip():
                message = await client.send_message(target_entity, event.message.message)
                log_media_forwarding(f"User {user_id_str}: text forwarded without attribution")
                update_media_stats(user_id, "text", True)
                return message.id
            else:
                update_media_stats(user_id, "text", False)
                return None
        else:
            # For media messages, use send_message with the media file
            message = await client.send_message(
                target_entity,
                event.message.message,  # Include caption if any
                file=event.message.media  # Include the media file
            )
            
            # Log successful forwarding
            log_media_forwarding(f"User {user_id_str}: {media_type} forwarded without attribution")
            update_media_stats(user_id, media_type, True)
            return message.id
            
    except FloodWaitError as e:
        # Handle flood wait errors
        wait_time = e.seconds
        log_error(f"Flood wait for {media_type} forwarding: {wait_time} seconds", e)
        await asyncio.sleep(wait_time)
        # Retry once after waiting
        try:
            if media_type == "sticker":
                message = await client.send_file(target_entity, event.message.media)
                return message.id
            elif media_type == "text":
                if event.message.message and event.message.message.strip():
                    message = await client.send_message(target_entity, event.message.message)
                    return message.id
                else:
                    return None
            else:
                message = await client.send_message(
                    target_entity,
                    event.message.message,
                    file=event.message.media
                )
                update_media_stats(user_id, media_type, True)
                return message.id
        except Exception as retry_error:
            log_error(f"Retry failed for {media_type}", retry_error)
            update_media_stats(user_id, media_type, False)
            return None
            
    except (ChannelPrivateError, ChatWriteForbiddenError, ChatAdminRequiredError) as e:
        log_error(f"Permission error forwarding {media_type} to {getattr(target_entity, 'username', getattr(target_entity, 'id', 'unknown'))}", e)
        update_media_stats(user_id, media_type, False)
        return None
            
    except Exception as e:
        log_error(f"Error forwarding {media_type} media", e)
        update_media_stats(user_id, media_type, False)
        return None

# ========= ENHANCED MESSAGE FORWARDER WITH PERSISTENT MESSAGE TRACKING, DISCORD SUPPORT, AND MESSAGE MAPPING =========
@client.on(events.NewMessage())
async def handler(event) -> None:
    """Main message forwarding handler with persistent message tracking, Discord support, and message mapping"""
    try:
        chat = await event.get_chat()
        message_id = event.message.id
        
        # Refresh settings periodically for message handler
        fresh_settings = load_settings()
        
        # Process for each user with forwarding enabled
        for user_id_str, settings in fresh_settings.items():
            try:
                user_id = int(user_id_str)
                if not settings.get("forwarding"):
                    # Update message tracking even when forwarding is off
                    update_message_tracking(user_id_str, chat.id, message_id)
                    continue

                routes = settings.get("routes", {})
                disabled_routes = settings.get("disabled_routes", {})
                required_keywords = settings.get("required_keywords", [])
                blocked_keywords = settings.get("blocked_keywords", [])
                allowed_media_types = settings.get("allowed_media_types", list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"])
                
                # Check for Telegram routes and forward if conditions are met
                telegram_processed = False
                
                for src_key, targets in routes.items():
                    if stored_value_matches_chat(src_key, chat):
                        # Get last processed message from persistent storage
                        last_processed_id = get_last_processed_message(user_id_str, chat.id)
                        
                        # Check if this message is older than our last processed message
                        if message_id <= last_processed_id:
                            continue
                        
                        # Update message tracking in persistent storage
                        update_message_tracking(user_id_str, chat.id, message_id)
                        
                        # Check if message should be forwarded based on type and media filters
                        if not should_forward_message(event, user_id):
                            chat_display = get_channel_display_name(chat)
                            media_type = get_media_type(event)
                            log_activity(f"Message blocked by media filter from {chat_display} for user {user_id_str} - media type: {media_type}")
                            continue
                        
                        # Get media type for keyword filtering
                        media_type = get_media_type(event)
                        text = event.message.message or ""
                        
                        # Apply keyword filtering using stored keywords with media type awareness
                        if not check_keyword_filtering(text, required_keywords, blocked_keywords, media_type):
                            chat_display = get_channel_display_name(chat)
                            keyword_info = []
                            if required_keywords and media_type != "sticker":
                                keyword_info.append(f"required: {', '.join(required_keywords)}")
                            if blocked_keywords and media_type != "sticker":
                                keyword_info.append(f"blocked: {', '.join(blocked_keywords)}")
                            
                            if media_type == "sticker":
                                log_activity(f"Sticker forwarded without keyword filtering from {chat_display} for user {user_id_str}")
                            else:
                                log_activity(f"Message blocked by keyword filter from {chat_display} for user {user_id_str} - {', '.join(keyword_info)}")
                                continue
                        
                        # Check blacklist for text messages (legacy) - but skip for stickers
                        if text and media_type != "sticker" and any(word in text.lower() for word in blacklist):
                            chat_display = get_channel_display_name(chat)
                            log_activity(f"Message blocked by blacklist from {chat_display} for user {user_id_str}")
                            continue

                        # Forward to Telegram targets (only if route is enabled)
                        for target_key in targets:
                            route_key = f"{src_key}->{target_key}"
                            
                            # Skip if this specific route is disabled
                            if route_key in disabled_routes:
                                continue
                            
                            try:
                                # Resolve target (could be username or ID)
                                if target_key.lstrip("-").isdigit():
                                    target_entity = await client.get_entity(int(target_key))
                                else:
                                    # For usernames, try both with and without @
                                    try:
                                        target_entity = await client.get_entity("@" + target_key)
                                    except Exception:
                                        target_entity = await client.get_entity(target_key)
                                
                                # Use enhanced forwarding that removes attribution
                                destination_message_id = await forward_media_message_enhanced(event, target_entity, user_id)
                                
                                if destination_message_id:
                                    # Store the message mapping for deletion synchronization using the target_key (not the numeric ID)
                                    update_message_mapping(user_id_str, chat.id, message_id, target_key, destination_message_id)
                                    
                                    src_display = get_stored_key_display(src_key)
                                    target_display = get_channel_display_name(target_entity)
                                    log_activity(f"Message forwarded without attribution: {src_display} → {target_display} for user {user_id_str}")
                                else:
                                    target_display = get_stored_key_display(target_key)
                                    error_msg = f"⚠️ Could not forward message to {target_display}. Please check permissions."
                                    await notify_user_non_technical(user_id, error_msg)
                                    log_error(f"Forward failed: {src_key} → {target_key} for user {user_id_str}")
                                    
                            except (ChannelPrivateError, ChatWriteForbiddenError, ChatAdminRequiredError) as forward_error:
                                target_display = get_stored_key_display(target_key)
                                error_msg = f"⚠️ Could not forward to {target_display}. Please check if the channel exists and you have permissions."
                                await notify_user_non_technical(user_id, error_msg)
                                log_error(f"Forward failed (permission): {src_key} → {target_key} for user {user_id_str}", forward_error)
                            except Exception as forward_error:
                                target_display = get_stored_key_display(target_key)
                                error_msg = f"⚠️ Could not forward to {target_display}. Please check if the channel exists and you have permissions."
                                await notify_user_non_technical(user_id, error_msg)
                                log_error(f"Forward failed: {src_key} → {target_key} for user {user_id_str}", forward_error)

                        telegram_processed = True

                # Check for Discord forwarding if the user has Discord routes - UPDATED: Now works independently and tracks message IDs
                user_discord_routes = get_user_discord_routes(user_id)
                if user_discord_routes:
                    # Get last processed message from persistent storage for Discord
                    last_processed_id = get_last_processed_message(user_id_str, chat.id)
                    
                    # Check if this message is older than our last processed message
                    if message_id <= last_processed_id:
                        continue
                    
                    # Update message tracking in persistent storage
                    update_message_tracking(user_id_str, chat.id, message_id)
                    
                    # Check if message should be forwarded based on type and media filters
                    if not should_forward_message(event, user_id):
                        chat_display = get_channel_display_name(chat)
                        media_type = get_media_type(event)
                        log_activity(f"Message blocked by media filter from {chat_display} for user {user_id_str} - media type: {media_type}")
                        continue
                    
                    # Get media type for keyword filtering
                    media_type = get_media_type(event)
                    text = event.message.message or ""
                    
                    # Apply keyword filtering using stored keywords with media type awareness
                    if not check_keyword_filtering(text, required_keywords, blocked_keywords, media_type):
                        chat_display = get_channel_display_name(chat)
                        keyword_info = []
                        if required_keywords and media_type != "sticker":
                            keyword_info.append(f"required: {', '.join(required_keywords)}")
                        if blocked_keywords and media_type != "sticker":
                            keyword_info.append(f"blocked: {', '.join(blocked_keywords)}")
                        
                        if media_type == "sticker":
                            log_activity(f"Sticker forwarded without keyword filtering from {chat_display} for user {user_id_str}")
                        else:
                            log_activity(f"Message blocked by keyword filter from {chat_display} for user {user_id_str} - {', '.join(keyword_info)}")
                            continue
                    
                    # Check blacklist for text messages (legacy) - but skip for stickers
                    if text and media_type != "sticker" and any(word in text.lower() for word in blacklist):
                        chat_display = get_channel_display_name(chat)
                        log_activity(f"Message blocked by blacklist from {chat_display} for user {user_id_str}")
                        continue

                    # Forward to Discord (this now tracks message IDs for deletion/editing sync)
                    await forward_to_discord(event, user_id)

            except Exception as user_error:
                log_error(f"Error processing message for user {user_id_str}", user_error)

    except Exception as e:
        log_error("Critical error in message handler", e)

# ========= PERSISTENT MESSAGE TRACKING =========
def load_message_tracking() -> Dict:
    """Load message tracking data from JSON file"""
    try:
        if os.path.exists(MESSAGE_TRACKING_FILE):
            with open(MESSAGE_TRACKING_FILE, "r", encoding="utf-8") as f:
                tracking_data = json.load(f)
            log_activity(f"Message tracking loaded from {MESSAGE_TRACKING_FILE}")
            return tracking_data
        else:
            log_activity(f"No existing message tracking file found.")
            return {}
    except Exception as e:
        log_error(f"Failed to load message tracking from {MESSAGE_TRACKING_FILE}", e)
        return {}

def save_message_tracking(tracking_data: Dict) -> None:
    """Save message tracking data to JSON file"""
    try:
        with open(MESSAGE_TRACKING_FILE, "w", encoding="utf-8") as f:
            json.dump(tracking_data, f, indent=2, ensure_ascii=False)
        log_activity(f"Message tracking saved to {MESSAGE_TRACKING_FILE}")
    except Exception as e:
        log_error(f"Failed to save message tracking to {MESSAGE_TRACKING_FILE}", e)

def update_message_tracking(user_id_str: str, chat_id: int, message_id: int) -> None:
    """Update message tracking for a specific user and chat"""
    try:
        tracking_data = load_message_tracking()
        user_route_key = f"{user_id_str}_{chat_id}"
        tracking_data[user_route_key] = message_id
        save_message_tracking(tracking_data)
    except Exception as e:
        log_error(f"Error updating message tracking for user {user_id_str}, chat {chat_id}", e)

def get_last_processed_message(user_id_str: str, chat_id: int) -> int:
    """Get the last processed message ID for a specific user and chat"""
    try:
        tracking_data = load_message_tracking()
        user_route_key = f"{user_id_str}_{chat_id}"
        return tracking_data.get(user_route_key, 0)
    except Exception as e:
        log_error(f"Error getting last processed message for user {user_id_str}, chat {chat_id}", e)
        return 0

# ========= BOT COMMAND HANDLERS =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message with main menu when the command /start is issued."""
    user_id = update.message.from_user.id
    
    welcome_text = (
        "🤖 <b>Telegram Auto-Forward Bot</b>\n\n"
        "I can automatically forward messages between Telegram channels and groups with advanced features:\n\n"
        "🔹 <b>Instant Message Deletion Sync</b> - When a message is deleted in source, it's instantly deleted in destinations\n"
        "🔹 <b>Message Editing Sync</b> - When a message is edited in source, it's instantly edited in destinations\n"
        "🔹 <b>No Forward Attribution</b> - Messages appear as if sent directly by the bot\n"
        "🔹 <b>Smart Media Filtering</b> - Control which media types get forwarded\n"
        "🔹 <b>Keyword Filtering</b> - Forward only messages containing specific keywords\n"
        "🔹 <b>Discord Integration</b> - Forward messages to Discord channels\n"
        "🔹 <b>Discord Deletion & Editing Sync</b> - Delete/edit Discord messages when Telegram messages are deleted/edited\n"
        "🔹 <b>Multiple Routes</b> - Forward from one source to multiple destinations\n\n"
        "💡 <b>Quick Start:</b>\n"
        "1. 📋 Select Channels - Choose source channels\n"
        "2. ⚙️ Manage Channels - Review selected channels\n"
        "3. 🔤 Keyword Filters - Set up keyword filtering\n"
        "4. 🖼️ Media Filters - Choose which media types to forward\n"
        "5. ➕ Add Route - Create forwarding routes\n"
        "6. 🚀 Start All - Begin forwarding\n\n"
        "Use the buttons below to get started!"
    )
    
    await safe_reply(update, welcome_text, get_main_menu_keyboard())
    log_activity(f"User {user_id} started the bot")

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle main menu navigation"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        routes_count = len(user_settings.get("routes", {}))
        channels_count = len(user_settings.get("available_channels", {}))
        
        text = (
            "🏠 <b>Main Menu</b>\n\n"
            f"📊 <b>Your Current Setup:</b>\n"
            f"• 📋 Selected Channels: {channels_count}\n"
            f"• 🔄 Active Routes: {routes_count}\n"
            f"• ⚡ Forwarding: {'✅ ON' if user_settings.get('forwarding') else '❌ OFF'}\n\n"
            "💡 <b>Quick Actions:</b>\n"
            "Use the buttons below to manage your setup!"
        )
        
        await safe_edit_message(update, text, get_main_menu_keyboard())
        
    except Exception as e:
        log_error(f"Error handling main menu for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading the main menu.", get_navigation_keyboard())

async def handle_channel_selection_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle channel selection menu"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        available_channels = user_settings.get("available_channels", {})
        selected_count = len(available_channels)
        
        text = (
            "📋 <b>Channel Selection</b>\n\n"
            f"📊 Currently selected: <b>{selected_count}</b> channels\n\n"
            "💡 <b>How to proceed:</b>\n"
            "• <b>Select Channels</b> - Browse and select from your channels\n"
            "• <b>Manage Channels</b> - Review and remove selected channels\n"
            # "• <b>Enter Manually</b> - Add channels by username or ID\n\n"
            "🔧 <b>Note:</b> You need to be a member of the channels you want to monitor."
        )
        
        keyboard = [
            [InlineKeyboardButton("📋 Browse & Select Channels", callback_data="browse_channels")],
            [InlineKeyboardButton("⚙️ Manage Selected Channels", callback_data="menu_manage_channels")],
            # [InlineKeyboardButton("✏️ Enter Channel Manually", callback_data="manual_input_channels")],
            [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
            [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
            [InlineKeyboardButton("➕ Add Route", callback_data="menu_add_route")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error handling channel selection menu for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading channel selection.", get_navigation_keyboard())

async def handle_browse_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Browse and select channels from user's channel list"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_channels = await get_user_channels(user_id)
        user_settings = get_user_settings_fresh(user_id)
        available_channels = user_settings.get("available_channels", {})
        
        if not user_channels:
            text = (
                "❌ <b>No Channels Found</b>\n\n"
                "I couldn't find any channels or groups that you're a member of.\n\n"
                "💡 <b>Possible reasons:</b>\n"
                "• You're not a member of any channels/groups\n"
                "• The channels are private and I can't access them\n"
                "• You haven't joined any channels with this account\n\n"
                "🔧 <b>Solutions:</b>\n"
                "• Join some public channels first\n"
                # "• Use 'Enter Channel Manually' to add channels by username/ID\n"
                "• Make sure you're a member of the channels you want to monitor"
            )
            
            keyboard = [
                # [InlineKeyboardButton("✏️ Enter Channel Manually", callback_data="manual_input_channels")],
                [InlineKeyboardButton("🔄 Try Again", callback_data="browse_channels")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
            ]
            
            await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
            return
        
        channel_selection_states[user_id] = {
            "user_channels": user_channels,
            "selected_channels": available_channels.copy(),
            "page": 0
        }
        
        selected_count = len(available_channels)
        
        text = (
            f"📋 <b>Browse Channels</b>\n\n"
            f"Found <b>{len(user_channels)}</b> channels/groups you're a member of.\n"
            f"Currently selected: <b>{selected_count}</b> channels\n\n"
            "💡 <b>Click on channels to select/deselect them</b>\n"
            "Selected channels will be marked with ☑️"
        )
        
        keyboard = create_channel_selection_keyboard(user_channels, available_channels, 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error browsing channels for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while fetching your channels.", get_navigation_keyboard())

async def handle_channel_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id: str, page: int) -> None:
    """Toggle channel selection"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in channel_selection_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = channel_selection_states[user_id]
    user_channels = state["user_channels"]
    selected_channels = state["selected_channels"]
    
    channel_info = next((ch for ch in user_channels if str(ch["id"]) == channel_id), None)
    if not channel_info:
        await update.callback_query.answer("Channel not found")
        return
    
    channel_key = channel_info["username"] or str(channel_info["id"])
    
    if channel_key in selected_channels:
        del selected_channels[channel_key]
        action = "removed"
    else:
        selected_channels[channel_key] = channel_info
        action = "added"
    
    await update.callback_query.answer(f"Channel {action}")
    
    selected_count = len(selected_channels)
    
    text = (
        f"📋 <b>Browse Channels</b>\n\n"
        f"Found <b>{len(user_channels)}</b> channels/groups you're a member of.\n"
        f"Currently selected: <b>{selected_count}</b> channels\n\n"
        "💡 <b>Click on channels to select/deselect them</b>\n"
        "Selected channels will be marked with ☑️"
    )
    
    keyboard = create_channel_selection_keyboard(user_channels, selected_channels, page)
    await safe_edit_message(update, text, keyboard)

async def handle_channel_selection_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Handle pagination in channel selection"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in channel_selection_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    state = channel_selection_states[user_id]
    user_channels = state["user_channels"]
    selected_channels = state["selected_channels"]
    state["page"] = page
    
    selected_count = len(selected_channels)
    
    text = (
        f"📋 <b>Browse Channels</b>\n\n"
        f"Found <b>{len(user_channels)}</b> channels/groups you're a member of.\n"
        f"Currently selected: <b>{selected_count}</b> channels\n\n"
        "💡 <b>Click on channels to select/deselect them</b>\n"
        "Selected channels will be marked with ☑️"
    )
    
    keyboard = create_channel_selection_keyboard(user_channels, selected_channels, page)
    await safe_edit_message(update, text, keyboard)

async def handle_save_channel_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save channel selection to user settings"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in channel_selection_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = channel_selection_states[user_id]
    selected_channels = state["selected_channels"]
    
    user_id_str = str(user_id)
    refresh_user_settings()
    
    if user_id_str not in user_settings:
        user_settings[user_id_str] = {
            "routes": {},
            "forwarding": False,
            "disabled_routes": {},
            "available_channels": {},
            "required_keywords": [],
            "blocked_keywords": [],
            "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
        }
    
    user_settings[user_id_str]["available_channels"] = selected_channels
    save_settings()
    
    del channel_selection_states[user_id]
    
    text = f"✅ <b>Channel Selection Saved!</b>\n\n📊 Selected <b>{len(selected_channels)}</b> channels for monitoring."
    
    await safe_edit_message(update, text, get_navigation_keyboard())
    log_activity(f"User {user_id} saved {len(selected_channels)} channels")

async def handle_select_all_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Select all available channels"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in channel_selection_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = channel_selection_states[user_id]
    user_channels = state["user_channels"]
    
    for channel in user_channels:
        channel_key = channel["username"] or str(channel["id"])
        state["selected_channels"][channel_key] = channel
    
    await update.callback_query.answer(f"✅ Selected all {len(user_channels)} channels")
    
    text = (
        f"📋 <b>Browse Channels</b>\n\n"
        f"Found <b>{len(user_channels)}</b> channels/groups you're a member of.\n"
        f"Currently selected: <b>{len(user_channels)}</b> channels\n\n"
        "💡 <b>Click on channels to select/deselect them</b>\n"
        "Selected channels will be marked with ☑️"
    )
    
    keyboard = create_channel_selection_keyboard(user_channels, state["selected_channels"], state["page"])
    await safe_edit_message(update, text, keyboard)

async def handle_clear_all_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all channel selections"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in channel_selection_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = channel_selection_states[user_id]
    user_channels = state["user_channels"]
    state["selected_channels"] = {}
    
    await update.callback_query.answer("🗑️ Cleared all channel selections")
    
    text = (
        f"📋 <b>Browse Channels</b>\n\n"
        f"Found <b>{len(user_channels)}</b> channels/groups you're a member of.\n"
        f"Currently selected: <b>0</b> channels\n\n"
        "💡 <b>Click on channels to select/deselect them</b>\n"
        "Selected channels will be marked with ☑️"
    )
    
    keyboard = create_channel_selection_keyboard(user_channels, {}, state["page"])
    await safe_edit_message(update, text, keyboard)

async def handle_manage_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle channel management menu"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        available_channels = user_settings.get("available_channels", {})
        
        if not available_channels:
            text = (
                "⚙️ <b>Channel Management</b>\n\n"
                "❌ No channels selected yet.\n\n"
                "💡 <b>How to add channels:</b>\n"
                "1. Use 📋 Select Channels to browse your channels\n"
                # "2. Or use ✏️ Enter Manually to add by username/ID\n"
                "3. Save your selection\n"
                "4. Come back here to manage them"
            )
            
            keyboard = [
                [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
                # [InlineKeyboardButton("✏️ Enter Channel Manually", callback_data="manual_input_channels")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
            ]
            
            await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
            return
        
        channel_management_states[user_id] = {
            "available_channels": available_channels,
            "page": 0
        }
        
        text = (
            f"⚙️ <b>Channel Management</b>\n\n"
            f"📊 Managing <b>{len(available_channels)}</b> selected channels\n\n"
            "💡 <b>Actions:</b>\n"
            "• Click 🗑️ to remove a channel\n"
            "• Use navigation to browse all channels\n"
            "• Remove all channels at once if needed"
        )
        
        keyboard = create_channel_management_keyboard(available_channels, 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error handling channel management menu for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading channel management.", get_navigation_keyboard())

async def handle_channel_management_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Handle pagination in channel management"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in channel_management_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    state = channel_management_states[user_id]
    available_channels = state["available_channels"]
    state["page"] = page
    
    text = (
        f"⚙️ <b>Channel Management</b>\n\n"
        f"📊 Managing <b>{len(available_channels)}</b> selected channels\n\n"
        "💡 <b>Actions:</b>\n"
        "• Click 🗑️ to remove a channel\n"
        "• Use navigation to browse all channels\n"
        "• Remove all channels at once if needed"
    )
    
    keyboard = create_channel_management_keyboard(available_channels, page)
    await safe_edit_message(update, text, keyboard)

async def handle_channel_removal(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_key: str, page: int) -> None:
    """Remove a channel from user's available channels"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in channel_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = channel_management_states[user_id]
    available_channels = state["available_channels"]
    
    if channel_key in available_channels:
        removed_channel = available_channels[channel_key]
        del available_channels[channel_key]
        
        user_id_str = str(user_id)
        refresh_user_settings()
        
        if user_id_str in user_settings:
            user_settings[user_id_str]["available_channels"] = available_channels
            save_settings()
        
        state["available_channels"] = available_channels
        
        channel_display = f"@{removed_channel['username']}" if removed_channel['username'] else f"{removed_channel['title']} (ID: {removed_channel['id']})"
        await update.callback_query.answer(f"🗑️ Removed: {channel_display}")
        
        if available_channels:
            text = (
                f"⚙️ <b>Channel Management</b>\n\n"
                f"📊 Managing <b>{len(available_channels)}</b> selected channels\n\n"
                "💡 <b>Actions:</b>\n"
                "• Click 🗑️ to remove a channel\n"
                "• Use navigation to browse all channels\n"
                "• Remove all channels at once if needed"
            )
            
            keyboard = create_channel_management_keyboard(available_channels, page)
            await safe_edit_message(update, text, keyboard)
        else:
            text = "✅ All channels have been removed."
            del channel_management_states[user_id]
            await safe_edit_message(update, text, get_navigation_keyboard())
    else:
        await update.callback_query.answer("❌ Channel not found")

async def handle_remove_all_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove all channels from user's available channels"""
    user_id = update.callback_query.from_user.id
    
    user_id_str = str(user_id)
    refresh_user_settings()
    
    if user_id_str in user_settings:
        removed_count = len(user_settings[user_id_str].get("available_channels", {}))
        user_settings[user_id_str]["available_channels"] = {}
        save_settings()
        
        if user_id in channel_management_states:
            del channel_management_states[user_id]
        
        await update.callback_query.answer(f"🗑️ Removed all {removed_count} channels")
        text = f"✅ <b>All Channels Removed!</b>\n\n🗑️ Removed <b>{removed_count}</b> channels from your selection."
        
        await safe_edit_message(update, text, get_navigation_keyboard())
        log_activity(f"User {user_id} removed all {removed_count} channels")
    else:
        await update.callback_query.answer("❌ No channels to remove")
        await safe_edit_message(update, "❌ No channels found to remove.", get_navigation_keyboard())

async def handle_manual_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE, step: str) -> None:
    """Handle manual channel input for various steps"""
    user_id = update.callback_query.from_user.id
    
    route_creation_states[user_id] = {"step": step}
    
    step_display = {
        "source": "source",
        "target": "destination", 
        "channels": "channel"
    }.get(step, step)
    
    text = (
        f"✏️ <b>Enter {step_display.capitalize()} Channel</b>\n\n"
        f"Please send me the {step_display} channel username or ID:\n\n"
        "💡 <b>Accepted formats:</b>\n"
        "• Username: <code>@channel_username</code> or <code>channel_username</code>\n"
        "• Channel ID: <code>-1001234567890</code> or <code>1234567890</code>\n\n"
        "🔧 <b>Note:</b>\n"
        "• For private channels, use the numeric ID\n"
        "• You must be a member of the channel\n"
        "• The bot doesn't need to be in the channel\n\n"
        "Send the channel username or ID now:"
    )
    
    keyboard = get_channel_selection_keyboard(step, user_id)
    await safe_edit_message(update, text, keyboard)

async def handle_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle manual channel input from user"""
    user_id = update.message.from_user.id
    user_input = update.message.text.strip()
    
    if user_id not in route_creation_states:
        return
    
    state = route_creation_states[user_id]
    step = state.get("step")
    
    if not step:
        return
    
    try:
        # Check membership for source channels
        if step in ["source", "channels"]:
            is_member = await check_membership(user_input, user_id)
            if not is_member:
                await safe_reply(update, 
                    f"❌ You are not a member of <code>{user_input}</code> or the channel is private.\n\n"
                    "💡 <b>Solutions:</b>\n"
                    "• Make sure you've joined the channel with this account\n"
                    "• For private channels, use the numeric ID format\n"
                    "• Check if the channel exists and is accessible\n\n"
                    "Please try again with a different channel:",
                    get_channel_selection_keyboard(step, user_id)
                )
                return
        
        # Resolve channel to get proper entity
        entity, parsed = await resolve_channel(user_input)
        
        if step == "channels":
            # Add to available channels
            user_id_str = str(user_id)
            refresh_user_settings()
            
            if user_id_str not in user_settings:
                user_settings[user_id_str] = {
                    "routes": {},
                    "forwarding": False,
                    "disabled_routes": {},
                    "available_channels": {},
                    "required_keywords": [],
                    "blocked_keywords": [],
                    "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
                }
            
            channel_key = entity.username or str(entity.id)
            channel_info = {
                "id": entity.id,
                "title": getattr(entity, 'title', 'Unknown'),
                "username": getattr(entity, 'username', None)
            }
            
            user_settings[user_id_str]["available_channels"][channel_key] = channel_info
            save_settings()
            
            del route_creation_states[user_id]
            
            channel_display = f"@{entity.username}" if entity.username else f"ID: {entity.id}"
            await safe_reply(update, 
                f"✅ <b>Channel Added Successfully!</b>\n\n"
                f"📢 <b>Channel:</b> {channel_display}\n"
                f"🏷️ <b>Title:</b> {getattr(entity, 'title', 'Unknown')}\n\n"
                f"💡 The channel has been added to your available channels list.\n"
                f"You can now use it when creating routes.",
                get_navigation_keyboard()
            )
            # log_activity(f"User {user_id} manually added channel: {channel_display}")
            
        elif step in ["source", "target"]:
            # Store for route creation
            state[step] = user_input
            state[f"{step}_entity"] = entity
            
            if step == "source":
                state["step"] = "target"
                source_display = f"@{entity.username}" if entity.username else f"ID: {entity.id}"
                
                await safe_reply(update,
                    f"✅ <b>Source Channel Set!</b>\n\n"
                    f"📢 <b>Source:</b> {source_display}\n\n"
                    "Now please send me the <b>destination channel</b>:\n\n"
                    "💡 <b>Accepted formats:</b>\n"
                    "• Username: <code>@channel_username</code> or <code>channel_username</code>\n"
                    "• Channel ID: <code>-1001234567890</code> or <code>1234567890</code>\n\n"
                    "Send the destination channel now:",
                    get_channel_selection_keyboard("target", user_id)
                )
            else:
                # Both source and target are set, complete the route
                await complete_route_creation(update, context, user_id)
                
    except Exception as e:
        error_msg = str(e)
        log_error(f"Error processing channel input '{user_input}' for user {user_id}", e)
        
        if "Could not resolve" in error_msg or "No user has" in error_msg:
            await safe_reply(update,
                f"❌ <b>Channel Not Found</b>\n\n"
                f"I couldn't find the channel <code>{user_input}</code>.\n\n"
                "💡 <b>Possible reasons:</b>\n"
                "• The channel doesn't exist\n"
                "• You entered an invalid username/ID\n"
                "• The channel is extremely private\n"
                "• There's a typo in the username\n\n"
                "Please check and try again:",
                get_channel_selection_keyboard(step, user_id)
            )
        else:
            await safe_reply(update,
                f"❌ <b>Error Processing Channel</b>\n\n"
                f"An error occurred while processing <code>{user_input}</code>.\n\n"
                f"Error: {error_msg}\n\n"
                "Please try again with a different channel:",
                get_channel_selection_keyboard(step, user_id)
            )

async def complete_route_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Complete the route creation process using only channel IDs"""
    try:
        state = route_creation_states[user_id]
        source_input = state.get("source")
        target_input = state.get("target")
        
        if not source_input or not target_input:
            await safe_reply(update, "❌ Missing source or destination channel. Please start again.", get_navigation_keyboard())
            return
        
        # Get channel entities for display only
        source_entity = state.get("source_entity")
        target_entity = state.get("target_entity")
        
        if not source_entity or not target_entity:
            await safe_reply(update, "❌ Could not resolve channels. Please start again.", get_navigation_keyboard())
            return
        
        # Store only channel IDs for internal use
        source_key = str(source_entity.id)  # Use only channel ID
        target_key = str(target_entity.id)  # Use only channel ID
        
        # Create display strings for user feedback
        source_display = f"@{source_entity.username}" if source_entity.username else f"ID: {source_entity.id}"
        target_display = f"@{target_entity.username}" if target_entity.username else f"ID: {target_entity.id}"
        
        # Get user settings
        user_id_str = str(user_id)
        refresh_user_settings()
        
        # Initialize user settings if not exists
        if user_id_str not in user_settings:
            user_settings[user_id_str] = {
                "routes": {},
                "forwarding": False,
                "disabled_routes": {},
                "available_channels": {},
                "required_keywords": [],
                "blocked_keywords": [],
                "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
            }
        
        # Get existing routes
        routes = user_settings[user_id_str].get("routes", {})
        
        # Create route using channel IDs only
        if source_key not in routes:
            routes[source_key] = []
        
        if target_key not in routes[source_key]:
            routes[source_key].append(target_key)
            user_settings[user_id_str]["routes"] = routes
            save_settings()
            
            # Success message
            text = (
                f"✅ <b>Route Added Successfully!</b>\n\n"
                f"🔄 <b>New Route:</b>\n"
                f"📢 Source: {source_display}\n"
                f"🎯 Destination: {target_display}\n\n"
                f"💡 <b>What happens now:</b>\n"
                f"• Messages from source will be forwarded to destination\n"
                f"• No 'forwarded from' attribution\n"
                f"• Instant deletion and editing sync enabled\n"
                f"• Use 🚀 Start All to begin forwarding\n\n"
                f"🔧 <b>Important:</b> The bot uses channel IDs internally. Usernames are for display only.\n\n"
                f"📝 <b>Note:</b> Make sure the bot has permission to send messages in the destination channel!"
            )
            
            log_activity(f"User {user_id} added route: {source_key} → {target_key}")
        else:
            text = f"⚠️ Route already exists."
        
        # Clean up state
        if user_id in route_creation_states:
            del route_creation_states[user_id]
        
        await safe_reply(update, text, get_navigation_keyboard())
        
    except Exception as e:
        log_error(f"Error completing route creation for user {user_id}", e)
        await safe_reply(update, "❌ An error occurred while creating the route. Please try again.", get_navigation_keyboard())
        if user_id in route_creation_states:
            del route_creation_states[user_id]

async def handle_add_route_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle add route menu"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        available_channels = user_settings.get("available_channels", {})
        
        if not available_channels:
            text = (
                "➕ <b>Add Route</b>\n\n"
                "❌ No channels available.\n\n"
                "💡 <b>How to proceed:</b>\n"
                "1. First, select some channels using 📋 Select Channels\n"
                # "2. Or add channels manually using ✏️ Enter Manually\n"
                "3. Save your channel selection\n"
                "4. Then come back here to create routes\n\n"
                "Routes connect source channels to destination channels for forwarding."
            )
            
            keyboard = [
                [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
                # [InlineKeyboardButton("✏️ Enter Channel Manually", callback_data="manual_input_channels")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
            ]
            
            await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
            return
        
        text = (
            "➕ <b>Add New Route</b>\n\n"
            "Create a new forwarding route from a source channel to a destination channel.\n\n"
            f"📊 Available Channels: <b>{len(available_channels)}</b>\n\n"
            "💡 <b>How it works:</b>\n"
            "1. Select a source channel (where messages come from)\n"
            "2. Select a destination channel (where messages go to)\n"
            "3. The bot will forward messages between them\n"
            "4. No 'forwarded from' attribution\n"
            "5. Instant deletion and editing sync enabled\n\n"
            "Choose how you want to add the route:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📋 Select from Available Channels", callback_data="select_route_channels")],
            # [InlineKeyboardButton("✏️ Enter Channels Manually", callback_data="manual_route_input")],
            [InlineKeyboardButton("🔤 Keyword Filters", callback_data="menu_keyword_management")],
            [InlineKeyboardButton("🖼️ Media Filters", callback_data="menu_media_filters")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error handling add route menu for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading route creation.", get_navigation_keyboard())

async def handle_select_route_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle route creation using available channels"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        available_channels = user_settings.get("available_channels", {})
        
        if not available_channels:
            await update.callback_query.answer("❌ No channels available")
            return
        
        manual_route_states[user_id] = {
            "step": "select_source",
            "available_channels": available_channels,
            "source": None,
            "target": None
        }
        
        text = (
            "📋 <b>Select Source Channel</b>\n\n"
            "Choose the source channel where messages will come from:\n\n"
            f"📊 Available Channels: <b>{len(available_channels)}</b>"
        )
        
        keyboard = create_route_selection_keyboard(available_channels, "source", 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error starting route channel selection for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading channels.", get_navigation_keyboard())

def create_route_selection_keyboard(available_channels: Dict, step: str, page: int = 0, items_per_page: int = 8):
    """Create keyboard for route channel selection with user-friendly display - UPDATED to handle underscores"""
    keyboard = []
    
    # Convert dict items to list
    channels_list = list(available_channels.items())
    
    # Calculate pagination
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_channels = channels_list[start_idx:end_idx]
    
    # Create buttons for each channel
    for channel_id, channel_info in page_channels:
        # Create user-friendly display name
        display_name = f"📢 {channel_info['title']}"
        
        # Add username if available for UI clarity
        if channel_info.get('username'):
            display_name += f" (@{channel_info['username']})"
        else:
            display_name += f" (ID: {channel_info['id']})"
        
        # Truncate if too long
        if len(display_name) > 40:
            display_name = display_name[:37] + "..."
        
        # Use the stored key (which could be username with underscores) as callback data
        # The handler will parse this correctly
        callback_data = f"route_select_{step}_{channel_id}_{page}"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=callback_data)])
    
    # Navigation buttons
    navigation_buttons = []
    total_pages = (len(channels_list) + items_per_page - 1) // items_per_page
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"route_{step}_page_{page-1}"))
    
    if end_idx < len(channels_list):
        navigation_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"route_{step}_page_{page+1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    # Manual input buttons
    # if step == "source":
    #     keyboard.append([InlineKeyboardButton("✏️ Enter Source Manually", callback_data="manual_input_source")])
    # else:
    #     keyboard.append([InlineKeyboardButton("✏️ Enter Destination Manually", callback_data="manual_input_target")])
    
    # Cancel button
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_add_route")])
    
    return InlineKeyboardMarkup(keyboard)

async def handle_route_channel_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, step: str, channel_key: str, page: int) -> None:
    """Handle channel selection for route creation - UPDATED to handle underscores in channel keys"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in manual_route_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = manual_route_states[user_id]
    available_channels = state["available_channels"]
    
    # First, try to find the channel by key (this handles both ID and username with underscores)
    channel_info = None
    
    # Check if it's a numeric ID
    if channel_key.lstrip('-').isdigit():
        # Look for channel by ID
        for key, info in available_channels.items():
            if info.get("id") == int(channel_key.lstrip('-')):
                channel_info = info
                channel_key = key  # Use the stored key (which might be different format)
                break
    
    # If not found by ID, try to find by username
    if not channel_info:
        # Check if it's a username (could contain underscores)
        for key, info in available_channels.items():
            if info.get("username") == channel_key:
                channel_info = info
                channel_key = key
                break
    
    if not channel_info:
        # If still not found, try direct lookup
        channel_info = available_channels.get(channel_key)
    
    if not channel_info:
        await update.callback_query.answer("Channel not found")
        return
    
    # Store only the channel ID for internal use
    state[step] = str(channel_info["id"])  # This is now the channel ID
    state[f"{step}_display"] = channel_info["title"]
    state[f"{step}_username"] = channel_info.get("username")
    
    if step == "source":
        state["step"] = "select_target"
        
        # Display user-friendly info
        channel_display = f"@{channel_info['username']}" if channel_info.get('username') else f"{channel_info['title']} (ID: {channel_info['id']})"
        
        text = (
            f"✅ <b>Source Channel Selected:</b> {channel_display}\n\n"
            "📋 <b>Select Destination Channel</b>\n\n"
            "Choose the destination channel where messages will be forwarded to:"
        )
        
        keyboard = create_route_selection_keyboard(available_channels, "target", 0)
        await safe_edit_message(update, text, keyboard)
        
    else:
        # Both source and target selected, complete the route
        await complete_manual_route_creation(update, context, user_id)
    
    await update.callback_query.answer(f"Selected: {channel_info['title']}")

async def handle_route_selection_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, step: str, page: int) -> None:
    """Handle pagination in route channel selection - UPDATED to handle underscores"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in manual_route_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    state = manual_route_states[user_id]
    available_channels = state["available_channels"]
    
    step_display = "Source" if step == "source" else "Destination"
    
    text = (
        f"📋 <b>Select {step_display} Channel</b>\n\n"
        f"Choose the {step_display.lower()} channel:\n\n"
        f"📊 Available Channels: <b>{len(available_channels)}</b>\n\n"
        f"🔧 <i>Usernames are shown for your convenience, but the bot uses channel IDs internally.</i>"
    )
    
    keyboard = create_route_selection_keyboard(available_channels, step, page)
    await safe_edit_message(update, text, keyboard)

async def complete_manual_route_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Complete the manual route creation process"""
    try:
        state = manual_route_states[user_id]
        source_id = state.get("source")  # 这是channel_id
        target_id = state.get("target")  # 这是channel_id
        source_display = state.get("source_display")  # 显示用
        target_display = state.get("target_display")  # 显示用
        
        if not source_id or not target_id:
            await safe_edit_message(update, "❌ Missing source or destination channel. Please start again.", get_navigation_keyboard())
            return
        
        # 获取显示信息
        available_channels = state.get("available_channels", {})
        source_info = available_channels.get(source_display, {})
        target_info = available_channels.get(target_display, {})
        
        # 获取显示文本
        source_display_text = f"@{source_info.get('username')}" if source_info.get('username') else f"ID: {source_info.get('id')}"
        target_display_text = f"@{target_info.get('username')}" if target_info.get('username') else f"ID: {target_info.get('id')}"
        
        # 存储到用户设置（使用channel_id）
        user_id_str = str(user_id)
        refresh_user_settings()
        
        if user_id_str not in user_settings:
            user_settings[user_id_str] = {
                "routes": {},
                "forwarding": False,
                "disabled_routes": {},
                "available_channels": {},
                "required_keywords": [],
                "blocked_keywords": [],
                "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
            }
        
        routes = user_settings[user_id_str].get("routes", {})
        
        if source_id not in routes:
            routes[source_id] = []
        
        if target_id not in routes[source_id]:
            routes[source_id].append(target_id)
            user_settings[user_id_str]["routes"] = routes
            save_settings()
            
            text = (
                f"✅ <b>Route Added Successfully!</b>\n\n"
                f"🔄 <b>New Route:</b>\n"
                f"📢 Source: {source_display_text}\n"
                f"🎯 Destination: {target_display_text}\n\n"
                f"💡 <b>What happens now:</b>\n"
                f"• Messages from source will be forwarded to destination\n"
                f"• No 'forwarded from' attribution\n"
                f"• Instant deletion and editing sync enabled\n"
                f"• Use 🚀 Start All to begin forwarding\n\n"
                f"🔧 <b>Note:</b> Make sure the bot has permission to send messages in the destination channel!"
            )
            
            log_activity(f"User {user_id} added route: {source_id} → {target_id}")
        else:
            text = f"⚠️ Route already exists."
        
        # 清理状态
        if user_id in manual_route_states:
            del manual_route_states[user_id]
        
        await safe_edit_message(update, text, get_navigation_keyboard())
        
    except Exception as e:
        log_error(f"Error completing manual route creation for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while creating the route. Please try again.", get_navigation_keyboard())
        if user_id in manual_route_states:
            del manual_route_states[user_id]

async def handle_list_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all routes for the user - MODIFIED to show usernames instead of IDs"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        routes = user_settings.get("routes", {})
        disabled_routes = user_settings.get("disabled_routes", {})
        
        if not routes:
            text = (
                "📋 <b>Your Routes</b>\n\n"
                "❌ No routes found.\n\n"
                "💡 <b>How to create routes:</b>\n"
                "1. Use 📋 Select Channels to choose source channels\n"
                "2. Use ➕ Add Route to create forwarding routes\n"
                "3. Connect source channels to destination channels\n"
                "4. Use 🚀 Start All to begin forwarding"
            )
        else:
            lines = []
            total_routes = 0
            enabled_routes = 0
            
            for source_key, targets in routes.items():
                # Get source channel display name - MODIFIED to prefer username
                available_channels = user_settings.get("available_channels", {})
                
                # Try to get channel info by ID or username
                source_info = None
                source_display = f"ID: {source_key}"
                
                # Check if source_key is in available_channels (might be ID or username)
                if source_key in available_channels:
                    source_info = available_channels[source_key]
                else:
                    # Try to find by ID in available_channels
                    for channel_key, channel_info in available_channels.items():
                        if str(channel_info.get('id')) == source_key:
                            source_info = channel_info
                            break
                
                if source_info:
                    if source_info.get('username'):
                        source_display = f"@{source_info.get('username')}"
                    else:
                        source_display = f"{source_info.get('title', 'Unknown')}"
                
                target_lines = []
                for target_key in targets:
                    route_key = f"{source_key}->{target_key}"
                    is_disabled = route_key in disabled_routes
                    status = "⏸️" if is_disabled else "✅"
                    
                    # Get target channel display name - MODIFIED to prefer username
                    target_info = None
                    target_display = f"ID: {target_key}"
                    
                    # Check if target_key is in available_channels (might be ID or username)
                    if target_key in available_channels:
                        target_info = available_channels[target_key]
                    else:
                        # Try to find by ID in available_channels
                        for channel_key, channel_info in available_channels.items():
                            if str(channel_info.get('id')) == target_key:
                                target_info = channel_info
                                break
                    
                    if target_info:
                        if target_info.get('username'):
                            target_display = f"@{target_info.get('username')}"
                        else:
                            target_display = f"{target_info.get('title', 'Unknown')}"
                    
                    target_lines.append(f"  {status} {target_display}")
                    total_routes += 1
                    if not is_disabled:
                        enabled_routes += 1
                
                lines.append(f"📢 {source_display}:")
                lines.extend(target_lines)
                lines.append("")  # Empty line for spacing
            
            text = (
                f"📋 <b>Your Routes</b>\n\n"
                f"📊 Total Routes: <b>{total_routes}</b>\n"
                f"✅ Enabled: <b>{enabled_routes}</b>\n"
                f"⏸️ Disabled: <b>{total_routes - enabled_routes}</b>\n\n" +
                "\n".join(lines) +
                f"\n💡 Use ⚙️ Manage Routes to enable/disable specific routes."
            )
        
        keyboard = [
            [InlineKeyboardButton("⚙️ Manage Routes", callback_data="menu_manage_routes")],
            [InlineKeyboardButton("➕ Add More Routes", callback_data="menu_add_route")],
            [InlineKeyboardButton("✏️ Quick Add Route", callback_data="menu_quick_add")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error listing routes for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading routes.", get_navigation_keyboard())

def _prepare_routes_data(user_settings: Dict) -> Dict:
    """Prepare routes data for management display - MODIFIED to show usernames instead of IDs"""
    routes_data = {}
    routes = user_settings.get("routes", {})
    disabled_routes = user_settings.get("disabled_routes", {})
    available_channels = user_settings.get("available_channels", {})
    
    for source_key, targets in routes.items():
        for target_key in targets:
            route_key = f"{source_key}->{target_key}"
            
            # Get source channel display name - MODIFIED to prefer username
            source_info = None
            source_display = f"ID: {source_key}"
            
            # Check if source_key is in available_channels (might be ID or username)
            if source_key in available_channels:
                source_info = available_channels[source_key]
            else:
                # Try to find by ID in available_channels
                for channel_key, channel_info in available_channels.items():
                    if str(channel_info.get('id')) == source_key:
                        source_info = channel_info
                        break
            
            if source_info:
                if source_info.get('username'):
                    source_display = f"@{source_info.get('username')}"
                else:
                    source_display = f"{source_info.get('title', 'Unknown')}"
            
            # Get target channel display name - MODIFIED to prefer username
            target_info = None
            target_display = f"ID: {target_key}"
            
            # Check if target_key is in available_channels (might be ID or username)
            if target_key in available_channels:
                target_info = available_channels[target_key]
            else:
                # Try to find by ID in available_channels
                for channel_key, channel_info in available_channels.items():
                    if str(channel_info.get('id')) == target_key:
                        target_info = channel_info
                        break
            
            if target_info:
                if target_info.get('username'):
                    target_display = f"@{target_info.get('username')}"
                else:
                    target_display = f"{target_info.get('title', 'Unknown')}"
            
            routes_data[route_key] = {
                'source': source_key,
                'target': target_key,
                'source_display': source_display,
                'target_display': target_display,
                'disabled': route_key in disabled_routes
            }
    
    return routes_data

async def handle_manage_routes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle route management menu"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        routes = user_settings.get("routes", {})
        
        if not routes:
            text = (
                "⚙️ <b>Route Management</b>\n\n"
                "❌ No routes found to manage.\n\n"
                "💡 <b>How to create routes:</b>\n"
                "1. Use 📋 Select Channels to choose source channels\n"
                "2. Use ➕ Add Route to create forwarding routes\n"
                "3. Connect source channels to destination channels\n"
                "4. Come back here to manage them"
            )
            
            keyboard = [
                [InlineKeyboardButton("➕ Add Route", callback_data="menu_add_route")],
                [InlineKeyboardButton("✏️ Quick Add Route", callback_data="menu_quick_add")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
            ]
            
            await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
            return
        
        route_management_states[user_id] = {
            "routes_data": _prepare_routes_data(user_settings),
            "page": 0
        }
        
        total_routes = sum(len(targets) for targets in routes.values())
        enabled_routes = total_routes - len(user_settings.get("disabled_routes", {}))
        
        text = (
            f"⚙️ <b>Route Management</b>\n\n"
            f"📊 Managing <b>{total_routes}</b> routes ({enabled_routes} ✅ enabled, {total_routes - enabled_routes} ⏸️ disabled)\n\n"
            "💡 <b>Actions:</b>\n"
            "• Click on routes to enable/disable them\n"
            "• Use navigation to browse all routes\n"
            "• Enable/disable all routes at once\n"
            "• Delete unwanted routes\n\n"
            "Routes marked with ✅ are active, ⏸️ are paused."
        )
        
        keyboard = get_route_management_keyboard(route_management_states[user_id]["routes_data"], 0)
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error handling route management menu for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading route management.", get_navigation_keyboard())

async def handle_list_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all routes for the user - MODIFIED to show usernames instead of IDs"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        routes = user_settings.get("routes", {})
        disabled_routes = user_settings.get("disabled_routes", {})
        
        if not routes:
            text = (
                "📋 <b>Your Routes</b>\n\n"
                "❌ No routes found.\n\n"
                "💡 <b>How to create routes:</b>\n"
                "1. Use 📋 Select Channels to choose source channels\n"
                "2. Use ➕ Add Route to create forwarding routes\n"
                "3. Connect source channels to destination channels\n"
                "4. Use 🚀 Start All to begin forwarding"
            )
        else:
            lines = []
            total_routes = 0
            enabled_routes = 0
            
            for source_key, targets in routes.items():
                # Get source channel display name - MODIFIED to prefer username
                available_channels = user_settings.get("available_channels", {})
                
                # Try to get channel info by ID or username
                source_info = None
                source_display = f"ID: {source_key}"
                
                # Check if source_key is in available_channels (might be ID or username)
                if source_key in available_channels:
                    source_info = available_channels[source_key]
                else:
                    # Try to find by ID in available_channels
                    for channel_key, channel_info in available_channels.items():
                        if str(channel_info.get('id')) == source_key:
                            source_info = channel_info
                            break
                
                if source_info:
                    if source_info.get('username'):
                        source_display = f"@{source_info.get('username')}"
                    else:
                        source_display = f"{source_info.get('title', 'Unknown')}"
                
                target_lines = []
                for target_key in targets:
                    route_key = f"{source_key}->{target_key}"
                    is_disabled = route_key in disabled_routes
                    status = "⏸️" if is_disabled else "✅"
                    
                    # Get target channel display name - MODIFIED to prefer username
                    target_info = None
                    target_display = f"ID: {target_key}"
                    
                    # Check if target_key is in available_channels (might be ID or username)
                    if target_key in available_channels:
                        target_info = available_channels[target_key]
                    else:
                        # Try to find by ID in available_channels
                        for channel_key, channel_info in available_channels.items():
                            if str(channel_info.get('id')) == target_key:
                                target_info = channel_info
                                break
                    
                    if target_info:
                        if target_info.get('username'):
                            target_display = f"@{target_info.get('username')}"
                        else:
                            target_display = f"{target_info.get('title', 'Unknown')}"
                    
                    target_lines.append(f"  {status} {target_display}")
                    total_routes += 1
                    if not is_disabled:
                        enabled_routes += 1
                
                lines.append(f"📢 {source_display}:")
                lines.extend(target_lines)
                lines.append("")  # Empty line for spacing
            
            text = (
                f"📋 <b>Your Routes</b>\n\n"
                f"📊 Total Routes: <b>{total_routes}</b>\n"
                f"✅ Enabled: <b>{enabled_routes}</b>\n"
                f"⏸️ Disabled: <b>{total_routes - enabled_routes}</b>\n\n" +
                "\n".join(lines) +
                f"\n💡 Use ⚙️ Manage Routes to enable/disable specific routes."
            )
        
        keyboard = [
            [InlineKeyboardButton("⚙️ Manage Routes", callback_data="menu_manage_routes")],
            [InlineKeyboardButton("➕ Add More Routes", callback_data="menu_add_route")],
            [InlineKeyboardButton("✏️ Quick Add Route", callback_data="menu_quick_add")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error listing routes for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading routes.", get_navigation_keyboard())

async def handle_route_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, route_key: str, page: int) -> None:
    """Toggle route enabled/disabled state"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = route_management_states[user_id]
    routes_data = state["routes_data"]
    
    if route_key not in routes_data:
        await update.callback_query.answer("❌ Route not found")
        return
    
    route_info = routes_data[route_key]
    user_id_str = str(user_id)
    refresh_user_settings()
    
    if user_id_str not in user_settings:
        await update.callback_query.answer("❌ User settings not found")
        return
    
    disabled_routes = user_settings[user_id_str].get("disabled_routes", {})
    
    if route_info['disabled']:
        # Enable the route
        if route_key in disabled_routes:
            del disabled_routes[route_key]
        action = "enabled"
        new_status = "✅"
    else:
        # Disable the route
        disabled_routes[route_key] = True
        action = "disabled"
        new_status = "⏸️"
    
    user_settings[user_id_str]["disabled_routes"] = disabled_routes
    save_settings()
    
    # Update local state
    routes_data[route_key]['disabled'] = not route_info['disabled']
    
    route_display = f"{route_info['source_display']} → {route_info['target_display']}"
    await update.callback_query.answer(f"{new_status} Route {action}")
    
    # Refresh the management interface
    total_routes = len(routes_data)
    enabled_routes = sum(1 for r in routes_data.values() if not r['disabled'])
    
    text = (
        f"⚙️ <b>Route Management</b>\n\n"
        f"📊 Managing <b>{total_routes}</b> routes ({enabled_routes} ✅ enabled, {total_routes - enabled_routes} ⏸️ disabled)\n\n"
        "💡 <b>Actions:</b>\n"
        "• Click on routes to enable/disable them\n"
        "• Use navigation to browse all routes\n"
        "• Enable/disable all routes at once\n"
        "• Delete unwanted routes\n\n"
        "Routes marked with ✅ are active, ⏸️ are paused."
    )
    
    keyboard = get_route_management_keyboard(routes_data, page)
    await safe_edit_message(update, text, keyboard)

async def handle_route_management_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Handle pagination in route management"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    state = route_management_states[user_id]
    routes_data = state["routes_data"]
    state["page"] = page
    
    total_routes = len(routes_data)
    enabled_routes = sum(1 for r in routes_data.values() if not r['disabled'])
    
    text = (
        f"⚙️ <b>Route Management</b>\n\n"
        f"📊 Managing <b>{total_routes}</b> routes ({enabled_routes} ✅ enabled, {total_routes - enabled_routes} ⏸️ disabled)\n\n"
        "💡 <b>Actions:</b>\n"
        "• Click on routes to enable/disable them\n"
        "• Use navigation to browse all routes\n"
        "• Enable/disable all routes at once\n"
        "• Delete unwanted routes\n\n"
        "Routes marked with ✅ are active, ⏸️ are paused."
    )
    
    keyboard = get_route_management_keyboard(routes_data, page)
    await safe_edit_message(update, text, keyboard)

async def handle_enable_all_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable all routes"""
    user_id = update.callback_query.from_user.id
    
    user_id_str = str(user_id)
    refresh_user_settings()
    
    if user_id_str in user_settings:
        user_settings[user_id_str]["disabled_routes"] = {}
        save_settings()
        
        # Update local state
        if user_id in route_management_states:
            for route_key in route_management_states[user_id]["routes_data"]:
                route_management_states[user_id]["routes_data"][route_key]['disabled'] = False
        
        await update.callback_query.answer("✅ All routes enabled")
        
        # Refresh the management interface
        if user_id in route_management_states:
            state = route_management_states[user_id]
            routes_data = state["routes_data"]
            total_routes = len(routes_data)
            
            text = (
                f"⚙️ <b>Route Management</b>\n\n"
                f"📊 Managing <b>{total_routes}</b> routes ({total_routes} ✅ enabled, 0 ⏸️ disabled)\n\n"
                "💡 <b>Actions:</b>\n"
                "• Click on routes to enable/disable them\n"
                "• Use navigation to browse all routes\n"
                "• Enable/disable all routes at once\n"
                "• Delete unwanted routes\n\n"
                "Routes marked with ✅ are active, ⏸️ are paused."
            )
            
            keyboard = get_route_management_keyboard(routes_data, state["page"])
            await safe_edit_message(update, text, keyboard)
    
    else:
        await update.callback_query.answer("❌ No routes found")

async def handle_disable_all_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable all routes"""
    user_id = update.callback_query.from_user.id
    
    user_id_str = str(user_id)
    refresh_user_settings()
    
    if user_id_str in user_settings:
        routes = user_settings[user_id_str].get("routes", {})
        disabled_routes = {}
        
        for source_key, targets in routes.items():
            for target_key in targets:
                route_key = f"{source_key}->{target_key}"
                disabled_routes[route_key] = True
        
        user_settings[user_id_str]["disabled_routes"] = disabled_routes
        save_settings()
        
        # Update local state
        if user_id in route_management_states:
            for route_key in route_management_states[user_id]["routes_data"]:
                route_management_states[user_id]["routes_data"][route_key]['disabled'] = True
        
        await update.callback_query.answer("⏸️ All routes disabled")
        
        # Refresh the management interface
        if user_id in route_management_states:
            state = route_management_states[user_id]
            routes_data = state["routes_data"]
            total_routes = len(routes_data)
            
            text = (
                f"⚙️ <b>Route Management</b>\n\n"
                f"📊 Managing <b>{total_routes}</b> routes (0 ✅ enabled, {total_routes} ⏸️ disabled)\n\n"
                "💡 <b>Actions:</b>\n"
                "• Click on routes to enable/disable them\n"
                "• Use navigation to browse all routes\n"
                "• Enable/disable all routes at once\n"
                "• Delete unwanted routes\n\n"
                "Routes marked with ✅ are active, ⏸️ are paused."
            )
            
            keyboard = get_route_management_keyboard(routes_data, state["page"])
            await safe_edit_message(update, text, keyboard)
    
    else:
        await update.callback_query.answer("❌ No routes found")

async def handle_delete_routes_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enter route deletion mode"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = route_management_states[user_id]
    routes_data = state["routes_data"]
    
    route_management_states[user_id]["deletion_mode"] = True
    route_management_states[user_id]["selected_routes"] = set()
    
    total_routes = len(routes_data)
    
    text = (
        f"🗑️ <b>Delete Routes</b>\n\n"
        f"📊 Found <b>{total_routes}</b> routes.\n\n"
        "💡 <b>How to delete:</b>\n"
        "• Click on routes to select/deselect them for deletion\n"
        "• Selected routes will be marked with ☑️\n"
        "• Use the button below to confirm deletion\n"
        "• This action cannot be undone!\n\n"
        "Select routes to delete:"
    )
    
    keyboard = get_route_deletion_keyboard(routes_data, set(), 0)
    await safe_edit_message(update, text, keyboard)

async def handle_route_deletion_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, route_key: str, page: int) -> None:
    """Toggle route selection for deletion"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = route_management_states[user_id]
    
    if "deletion_mode" not in state or not state["deletion_mode"]:
        await update.callback_query.answer("Not in deletion mode")
        return
    
    selected_routes = state["selected_routes"]
    
    if route_key in selected_routes:
        selected_routes.remove(route_key)
        action = "removed from selection"
    else:
        selected_routes.add(route_key)
        action = "added to selection"
    
    await update.callback_query.answer(f"Route {action}")
    
    routes_data = state["routes_data"]
    total_routes = len(routes_data)
    selected_count = len(selected_routes)
    
    text = (
        f"🗑️ <b>Delete Routes</b>\n\n"
        f"📊 Found <b>{total_routes}</b> routes.\n"
        f"✅ Selected: <b>{selected_count}</b> routes\n\n"
        "💡 <b>How to delete:</b>\n"
        "• Click on routes to select/deselect them for deletion\n"
        "• Selected routes will be marked with ☑️\n"
        "• Use the button below to confirm deletion\n"
        "• This action cannot be undone!\n\n"
        "Select routes to delete:"
    )
    
    keyboard = get_route_deletion_keyboard(routes_data, selected_routes, page)
    await safe_edit_message(update, text, keyboard)

async def handle_deletion_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Handle pagination in route deletion"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await safe_edit_message(update, "Session expired. Please start again.", get_navigation_keyboard())
        return
    
    state = route_management_states[user_id]
    
    if "deletion_mode" not in state or not state["deletion_mode"]:
        await safe_edit_message(update, "Not in deletion mode.", get_navigation_keyboard())
        return
    
    routes_data = state["routes_data"]
    selected_routes = state.get("selected_routes", set())
    total_routes = len(routes_data)
    selected_count = len(selected_routes)
    
    text = (
        f"🗑️ <b>Delete Routes</b>\n\n"
        f"📊 Found <b>{total_routes}</b> routes.\n"
        f"✅ Selected: <b>{selected_count}</b> routes\n\n"
        "💡 <b>How to delete:</b>\n"
        "• Click on routes to select/deselect them for deletion\n"
        "• Selected routes will be marked with ☑️\n"
        "• Use the button below to confirm deletion\n"
        "• This action cannot be undone!\n\n"
        "Select routes to delete:"
    )
    
    keyboard = get_route_deletion_keyboard(routes_data, selected_routes, page)
    await safe_edit_message(update, text, keyboard)

async def handle_select_all_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Select all routes for deletion"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = route_management_states[user_id]
    
    if "deletion_mode" not in state or not state["deletion_mode"]:
        await update.callback_query.answer("Not in deletion mode")
        return
    
    state["selected_routes"] = set(state["routes_data"].keys())
    
    await update.callback_query.answer(f"✅ Selected all {len(state['routes_data'])} routes")
    
    total_routes = len(state["routes_data"])
    selected_count = len(state["selected_routes"])
    
    text = (
        f"🗑️ <b>Delete Routes</b>\n\n"
        f"📊 Found <b>{total_routes}</b> routes.\n"
        f"✅ Selected: <b>{selected_count}</b> routes\n\n"
        "💡 <b>How to delete:</b>\n"
        "• Click on routes to select/deselect them for deletion\n"
        "• Selected routes will be marked with ☑️\n"
        "• Use the button below to confirm deletion\n"
        "• This action cannot be undone!\n\n"
        "Select routes to delete:"
    )
    
    keyboard = get_route_deletion_keyboard(state["routes_data"], state["selected_routes"], state.get("page", 0))
    await safe_edit_message(update, text, keyboard)

async def handle_clear_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all route selections for deletion"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = route_management_states[user_id]
    
    if "deletion_mode" not in state or not state["deletion_mode"]:
        await update.callback_query.answer("Not in deletion mode")
        return
    
    state["selected_routes"] = set()
    
    await update.callback_query.answer("🗑️ Cleared all selections")
    
    routes_data = state["routes_data"]
    total_routes = len(routes_data)
    
    text = (
        f"🗑️ <b>Delete Routes</b>\n\n"
        f"📊 Found <b>{total_routes}</b> routes.\n"
        f"✅ Selected: <b>0</b> routes\n\n"
        "💡 <b>How to delete:</b>\n"
        "• Click on routes to select/deselect them for deletion\n"
        "• Selected routes will be marked with ☑️\n"
        "• Use the button below to confirm deletion\n"
        "• This action cannot be undone!\n\n"
        "Select routes to delete:"
    )
    
    keyboard = get_route_deletion_keyboard(routes_data, set(), state.get("page", 0))
    await safe_edit_message(update, text, keyboard)

async def handle_confirm_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm and execute route deletion"""
    user_id = update.callback_query.from_user.id
    
    if user_id not in route_management_states:
        await update.callback_query.answer("Session expired. Please start again.")
        return
    
    state = route_management_states[user_id]
    
    if "deletion_mode" not in state or not state["deletion_mode"]:
        await update.callback_query.answer("Not in deletion mode")
        return
    
    selected_routes = state.get("selected_routes", set())
    
    if not selected_routes:
        await update.callback_query.answer("❌ No routes selected for deletion")
        return
    
    user_id_str = str(user_id)
    refresh_user_settings()
    
    if user_id_str not in user_settings:
        await update.callback_query.answer("❌ User settings not found")
        return
    
    # Remove selected routes
    routes = user_settings[user_id_str].get("routes", {})
    disabled_routes = user_settings[user_id_str].get("disabled_routes", {})
    
    deleted_count = 0
    
    for route_key in selected_routes:
        # Parse route key (source->target)
        if "->" in route_key:
            source_key, target_key = route_key.split("->", 1)
            
            if source_key in routes and target_key in routes[source_key]:
                routes[source_key].remove(target_key)
                deleted_count += 1
                
                # Remove empty source entries
                if not routes[source_key]:
                    del routes[source_key]
                
                # Remove from disabled routes if present
                if route_key in disabled_routes:
                    del disabled_routes[route_key]
    
    user_settings[user_id_str]["routes"] = routes
    user_settings[user_id_str]["disabled_routes"] = disabled_routes
    save_settings()
    
    # Update local state
    if user_id in route_management_states:
        del route_management_states[user_id]
    
    await update.callback_query.answer(f"🗑️ Deleted {deleted_count} routes")
    
    text = f"✅ <b>Routes Deleted Successfully!</b>\n\n🗑️ Deleted <b>{deleted_count}</b> routes."
    
    await safe_edit_message(update, text, get_navigation_keyboard())
    log_activity(f"User {user_id} deleted {deleted_count} routes")

async def handle_quick_add_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick add route menu"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        available_channels = user_settings.get("available_channels", {})
        
        if not available_channels:
            text = (
                "✏️ <b>Quick Add Route</b>\n\n"
                "❌ No channels available.\n\n"
                "💡 <b>How to proceed:</b>\n"
                "1. First, select some channels using 📋 Select Channels\n"
                # "2. Or add channels manually using ✏️ Enter Manually\n"
                "3. Save your channel selection\n"
                "4. Then come back here to create routes quickly\n\n"
                "Quick add allows you to enter source and destination channels in one go."
            )
            
            keyboard = [
                [InlineKeyboardButton("📋 Select Channels", callback_data="menu_select_channels")],
                # [InlineKeyboardButton("✏️ Enter Channel Manually", callback_data="manual_input_channels")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
            ]
            
            await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
            return
        
        text = (
            "✏️ <b>Quick Add Route</b>\n\n"
            "Quickly add a route by entering both source and destination channels at once.\n\n"
            f"📊 Available Channels: <b>{len(available_channels)}</b>\n\n"
            "💡 <b>Format:</b>\n"
            "Send message in this format:\n"
            "<code>source_channel -> destination_channel</code>\n\n"
            "📝 <b>Examples:</b>\n"
            "• <code>@source_channel -> @destination_channel</code>\n"
            "• <code>-100123456789 -> -100987654321</code>\n"
            "• <code>@source_channel -> -100987654321</code>\n\n"
            "🔧 <b>Notes:</b>\n"
            "• Use '->' as separator\n"
            "• You must be a member of the source channel\n"
            "• The bot doesn't need to be in source channel\n"
            "• Bot must have permission in destination channel\n\n"
            "Send your route in the format above:"
        )
        
        route_creation_states[user_id] = {"step": "quick_add"}
        
        keyboard = get_quick_add_keyboard()
        await safe_edit_message(update, text, keyboard)
        
    except Exception as e:
        log_error(f"Error handling quick add menu for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading quick add.", get_navigation_keyboard())

async def handle_quick_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick add route input"""
    user_id = update.message.from_user.id
    user_input = update.message.text.strip()
    
    if user_id not in route_creation_states:
        return
    
    state = route_creation_states[user_id]
    
    if state.get("step") != "quick_add":
        return
    
    # Parse the input format: source -> destination
    if "->" not in user_input:
        await safe_reply(update,
            "❌ <b>Invalid Format</b>\n\n"
            "Please use the format: <code>source_channel -> destination_channel</code>\n\n"
            "📝 <b>Examples:</b>\n"
            "• <code>@source_channel -> @destination_channel</code>\n"
            "• <code>-100123456789 -> -100987654321</code>\n"
            "• <code>@source_channel -> -100987654321</code>\n\n"
            "Please try again:",
            get_quick_add_keyboard()
        )
        return
    
    parts = user_input.split("->", 1)
    if len(parts) != 2:
        await safe_reply(update,
            "❌ <b>Invalid Format</b>\n\n"
            "Please use exactly one '->' separator.\n\n"
            "Format: <code>source_channel -> destination_channel</code>\n\n"
            "Please try again:",
            get_quick_add_keyboard()
        )
        return
    
    source_input = parts[0].strip()
    target_input = parts[1].strip()
    
    if not source_input or not target_input:
        await safe_reply(update,
            "❌ <b>Empty Channels</b>\n\n"
            "Both source and destination channels must be provided.\n\n"
            "Format: <code>source_channel -> destination_channel</code>\n\n"
            "Please try again:",
            get_quick_add_keyboard()
        )
        return
    
    try:
        # Check membership for source channel
        is_member = await check_membership(source_input, user_id)
        if not is_member:
            await safe_reply(update, 
                f"❌ You are not a member of <code>{source_input}</code> or the channel is private.\n\n"
                "💡 <b>Solutions:</b>\n"
                "• Make sure you've joined the channel with this account\n"
                "• For private channels, use the numeric ID format\n"
                "• Check if the channel exists and is accessible\n\n"
                "Please try again with a different source channel:",
                get_quick_add_keyboard()
            )
            return
        
        # Resolve both channels
        source_entity, source_parsed = await resolve_channel(source_input)
        target_entity, target_parsed = await resolve_channel(target_input)
        
        source_key = source_entity.username or str(source_entity.id)
        target_key = target_entity.username or str(target_entity.id)
        
        user_id_str = str(user_id)
        refresh_user_settings()
        
        if user_id_str not in user_settings:
            user_settings[user_id_str] = {
                "routes": {},
                "forwarding": False,
                "disabled_routes": {},
                "available_channels": {},
                "required_keywords": [],
                "blocked_keywords": [],
                "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
            }
        
        routes = user_settings[user_id_str].get("routes", {})
        
        if source_key not in routes:
            routes[source_key] = []
        
        if target_key not in routes[source_key]:
            routes[source_key].append(target_key)
            user_settings[user_id_str]["routes"] = routes
            save_settings()
            
            # Add to available channels if not already there
            if source_key not in user_settings[user_id_str]["available_channels"]:
                user_settings[user_id_str]["available_channels"][source_key] = {
                    "id": source_entity.id,
                    "title": getattr(source_entity, 'title', 'Unknown'),
                    "username": getattr(source_entity, 'username', None)
                }
            
            if target_key not in user_settings[user_id_str]["available_channels"]:
                user_settings[user_id_str]["available_channels"][target_key] = {
                    "id": target_entity.id,
                    "title": getattr(target_entity, 'title', 'Unknown'),
                    "username": getattr(target_entity, 'username', None)
                }
            
            save_settings()
            
            source_display = f"@{source_entity.username}" if source_entity.username else f"ID: {source_entity.id}"
            target_display = f"@{target_entity.username}" if target_entity.username else f"ID: {target_entity.id}"
            
            text = (
                f"✅ <b>Route Added Successfully!</b>\n\n"
                f"🔄 <b>New Route:</b>\n"
                f"📢 Source: {source_display}\n"
                f"🎯 Destination: {target_display}\n\n"
                f"💡 <b>What happens now:</b>\n"
                f"• Messages from source will be forwarded to destination\n"
                f"• No 'forwarded from' attribution\n"
                f"• Instant deletion and editing sync enabled\n"
                f"• Use 🚀 Start All to begin forwarding\n\n"
                f"🔧 <b>Note:</b> Make sure the bot has permission to send messages in the destination channel!"
            )
            
            log_activity(f"User {user_id} quick added route: {source_display} → {target_display}")
        else:
            text = f"⚠️ Route already exists."
        
        if user_id in route_creation_states:
            del route_creation_states[user_id]
        
        await safe_reply(update, text, get_navigation_keyboard())
        
    except Exception as e:
        error_msg = str(e)
        log_error(f"Error processing quick add input '{user_input}' for user {user_id}", e)
        
        if "Could not resolve" in error_msg or "No user has" in error_msg:
            await safe_reply(update,
                f"❌ <b>Channel Not Found</b>\n\n"
                f"I couldn't find one of the channels in <code>{user_input}</code>.\n\n"
                "💡 <b>Possible reasons:</b>\n"
                "• One of the channels doesn't exist\n"
                "• You entered an invalid username/ID\n"
                "• One of the channels is extremely private\n"
                "• There's a typo in one of the usernames\n\n"
                "Please check and try again:",
                get_quick_add_keyboard()
            )
        else:
            await safe_reply(update,
                f"❌ <b>Error Processing Route</b>\n\n"
                f"An error occurred while processing <code>{user_input}</code>.\n\n"
                f"Error: {error_msg}\n\n"
                "Please try again with different channels:",
                get_quick_add_keyboard()
            )

async def handle_start_forwarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start all forwarding for the user"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_id_str = str(user_id)
        refresh_user_settings()
        
        if user_id_str not in user_settings:
            user_settings[user_id_str] = {
                "routes": {},
                "forwarding": False,
                "disabled_routes": {},
                "available_channels": {},
                "required_keywords": [],
                "blocked_keywords": [],
                "allowed_media_types": list(SUPPORTED_MEDIA_TYPES.keys()) + ["text"]
            }
        
        user_settings[user_id_str]["forwarding"] = True
        save_settings()
        
        routes_count = len(user_settings[user_id_str].get("routes", {}))
        
        text = (
            f"🚀 <b>Forwarding Started!</b>\n\n"
            f"📊 Active Routes: <b>{routes_count}</b>\n\n"
            "💡 <b>What happens now:</b>\n"
            "• Messages from your source channels will be forwarded to destinations\n"
            "• No 'forwarded from' attribution\n"
            "• Instant deletion and editing sync enabled\n"
            "• Use 🛑 Stop All to pause forwarding\n\n"
            "🔧 <b>Note:</b> Make sure the bot has proper permissions in all destination channels!"
        )
        
        await safe_edit_message(update, text, get_navigation_keyboard())
        log_activity(f"User {user_id} started forwarding for {routes_count} routes")
        
    except Exception as e:
        log_error(f"Error starting forwarding for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while starting forwarding.", get_navigation_keyboard())

async def handle_stop_forwarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop all forwarding for the user"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_id_str = str(user_id)
        refresh_user_settings()
        
        if user_id_str in user_settings:
            user_settings[user_id_str]["forwarding"] = False
            save_settings()
            
            routes_count = len(user_settings[user_id_str].get("routes", {}))
            
            text = (
                f"🛑 <b>Forwarding Stopped!</b>\n\n"
                f"📊 Paused Routes: <b>{routes_count}</b>\n\n"
                "💡 <b>What happens now:</b>\n"
                "• No new messages will be forwarded\n"
                "• Existing routes remain configured\n"
                "• Use 🚀 Start All to resume forwarding\n"
                "• Message deletion and editing sync will still work\n\n"
                "🔧 Your routes are preserved and can be restarted anytime."
            )
            
            log_activity(f"User {user_id} stopped forwarding for {routes_count} routes")
        else:
            text = "❌ No forwarding was active."
        
        await safe_edit_message(update, text, get_navigation_keyboard())
        
    except Exception as e:
        log_error(f"Error stopping forwarding for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while stopping forwarding.", get_navigation_keyboard())

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current bot status"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_settings = get_user_settings_fresh(user_id)
        routes = user_settings.get("routes", {})
        available_channels = user_settings.get("available_channels", {})
        disabled_routes = user_settings.get("disabled_routes", {})
        
        total_routes = sum(len(targets) for targets in routes.values())
        enabled_routes = total_routes - len(disabled_routes)
        
        # Get Discord routes
        discord_routes = get_user_discord_routes(user_id)
        total_discord_routes = sum(len(channels) for channels in discord_routes.values())
        
        # Get media stats
        user_media_stats = media_forwarding_stats.get(str(user_id), {})
        total_forwarded = sum(stats["success"] for stats in user_media_stats.values())
        
        text = (
            "📊 <b>Bot Status</b>\n\n"
            f"⚡ <b>Forwarding Status:</b> {'✅ ACTIVE' if user_settings.get('forwarding') else '❌ PAUSED'}\n\n"
            f"📋 <b>Channels:</b> {len(available_channels)} selected\n"
            f"🔄 <b>Telegram Routes:</b> {enabled_routes}/{total_routes} active\n"
            f"🔗 <b>Discord Routes:</b> {total_discord_routes}\n"
            f"📨 <b>Messages Forwarded:</b> {total_forwarded}\n\n"
            f"🔤 <b>Keyword Filters:</b>\n"
            f"• ✅ Required: {len(user_settings.get('required_keywords', []))}\n"
            f"• ❌ Blocked: {len(user_settings.get('blocked_keywords', []))}\n\n"
            f"🖼️ <b>Media Filters:</b> {len(user_settings.get('allowed_media_types', []))}/{len(SUPPORTED_MEDIA_TYPES) + 1} types allowed\n\n"
            "💡 <b>Quick Actions:</b>\n"
            "Use the buttons below to manage your setup."
        )
        
        keyboard = [
            [InlineKeyboardButton("🚀 Start Forwarding", callback_data="menu_start_forward"),
             InlineKeyboardButton("🛑 Stop Forwarding", callback_data="menu_stop_forward")],
            [InlineKeyboardButton("📋 View Routes", callback_data="menu_list_routes"),
             InlineKeyboardButton("🔗 Discord Routes", callback_data="menu_discord_routes")],
            [InlineKeyboardButton("⚡ Deletion Stats", callback_data="menu_deletion_stats"),
             InlineKeyboardButton("🖼️ Media Stats", callback_data="menu_media_stats")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error showing status for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading status.", get_navigation_keyboard())

async def handle_media_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show media forwarding statistics"""
    user_id = update.callback_query.from_user.id
    
    try:
        user_media_stats = media_forwarding_stats.get(str(user_id), {})
        
        if not user_media_stats:
            text = (
                "🖼️ <b>Media Forwarding Statistics</b>\n\n"
                "No media forwarding statistics available yet.\n\n"
                "Statistics will appear here after messages are forwarded."
            )
        else:
            lines = []
            total_success = 0
            total_failed = 0
            
            for media_type, stats in user_media_stats.items():
                success = stats.get("success", 0)
                failed = stats.get("failed", 0)
                total = success + failed
                success_rate = (success / total * 100) if total > 0 else 0
                
                display_name = MEDIA_TYPE_DISPLAY_NAMES.get(media_type, media_type)
                lines.append(f"• {display_name}: {success} ✅ / {failed} ❌ ({success_rate:.1f}%)")
                
                total_success += success
                total_failed += failed
            
            total_forwarded = total_success + total_failed
            overall_success_rate = (total_success / total_forwarded * 100) if total_forwarded > 0 else 0
            
            text = (
                f"🖼️ <b>Media Forwarding Statistics</b>\n\n"
                f"📊 <b>Overview:</b>\n"
                f"• Total Forwarded: {total_forwarded}\n"
                f"• Successful: {total_success} ✅\n"
                f"• Failed: {total_failed} ❌\n"
                f"• Success Rate: {overall_success_rate:.1f}%\n\n"
                f"📈 <b>By Media Type:</b>\n" +
                "\n".join(lines) +
                f"\n\n💡 Statistics are reset when the bot restarts."
            )
        
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh Stats", callback_data="menu_media_stats")],
            [InlineKeyboardButton("📊 Overall Status", callback_data="menu_status")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        
        await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        log_error(f"Error showing media stats for user {user_id}", e)
        await safe_edit_message(update, "❌ An error occurred while loading media statistics.", get_navigation_keyboard())

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show comprehensive help information"""
    text = (
        "❓ <b>Help & Guide</b>\n\n"
        
        "🤖 <b>What This Bot Does:</b>\n"
        "• Automatically forwards messages between Telegram channels/groups\n"
        "• Removes 'forwarded from' attribution\n"
        "• Syncs message deletions instantly\n"
        "• Syncs message edits instantly\n"
        "• Supports Discord integration\n"
        "• Advanced filtering options\n\n"
        
        "🚀 <b>Quick Start Guide:</b>\n"
        "1. <b>Select Channels</b> - Choose source channels to monitor\n"
        "2. <b>Add Routes</b> - Connect sources to destinations\n"
        "3. <b>Configure Filters</b> - Set up keyword and media filters\n"
        "4. <b>Start Forwarding</b> - Activate the bot\n\n"
        
        "⚙️ <b>Key Features:</b>\n"
        "• <b>Instant Deletion Sync</b> - Delete in source, deleted everywhere\n"
        "• <b>Message Edit Sync</b> - Edit in source, edited everywhere\n"
        "• <b>No Attribution</b> - Messages appear as sent by bot\n"
        "• <b>Media Filtering</b> - Choose which media types to forward\n"
        "• <b>Keyword Filtering</b> - Forward only specific content\n"
        "• <b>Discord Integration</b> - Forward to Discord channels\n"
        "• <b>Multiple Routes</b> - One source to multiple destinations\n\n"
        
        "🔧 <b>Requirements:</b>\n"
        "• You must be member of source channels\n"
        "• Bot needs permission in destination channels\n"
        "• For Discord: Bot token or webhook URL\n\n"
        
        "💡 <b>Tips:</b>\n"
        "• Start with a few routes and expand gradually\n"
        "• Test your keyword filters before relying on them\n"
        "• Ensure bot has proper permissions in all channels\n"
        "• Monitor the bot status regularly\n\n"
        
        "❌ <b>Troubleshooting:</b>\n"
        "• If forwarding stops, check bot permissions\n"
        "• If deletions don't sync, ensure bot is admin\n"
        "• For Discord issues, verify token/webhook\n"
        "• Check logs for detailed error information\n\n"
        
        "Need more help? Check the main menu for all available options!"
    )
    
    keyboard = [
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        [InlineKeyboardButton("📊 Status", callback_data="menu_status")],
        [InlineKeyboardButton("🔗 Discord Help", callback_data="discord_settings")]
    ]
    
    await safe_edit_message(update, text, InlineKeyboardMarkup(keyboard))

# ========= MESSAGE HANDLER DISPATCHER =========
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch text messages to appropriate handlers based on user state"""
    user_id = update.message.from_user.id
    
    # Check for keyword input first
    if user_id in keyword_management_states:
        state = keyword_management_states[user_id]
        editing_mode = state.get("editing_mode")
        if editing_mode and editing_mode.startswith("adding_"):
            await handle_keyword_input(update, context)
            return
    
    # Check for channel input (route creation)
    if user_id in route_creation_states:
        await handle_channel_input(update, context)
        return
    
    # Check for Discord channel input
    if user_id in discord_route_states:
        state = discord_route_states[user_id]
        if state.get("step") == "entering_discord_channel":
            await handle_discord_channel_input(update, context)
            return
    
    # Check for quick add route input
    if user_id in route_creation_states:
        state = route_creation_states[user_id]
        if state.get("step") == "quick_add":
            await handle_quick_add_input(update, context)
            return
    
    # If no specific state, show main menu
    await safe_reply(update, 
        "🤖 <b>Telegram Auto-Forward Bot</b>\n\n"
        "I didn't understand that command. Please use the buttons below to interact with me.",
        get_main_menu_keyboard()
    )

# ========= CALLBACK QUERY HANDLER =========
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all callback queries from inline keyboards"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    try:
        # Main menu navigation
        if data == "menu_main":
            await handle_main_menu(update, context)
        elif data == "menu_select_channels":
            await handle_channel_selection_menu(update, context)
        elif data == "menu_manage_channels":
            await handle_manage_channels_menu(update, context)
        elif data == "menu_keyword_management":
            await start_keyword_management(update, context)
        elif data == "menu_media_filters":
            await start_media_filter_management(update, context)
        elif data == "menu_add_route":
            await handle_add_route_menu(update, context)
        elif data == "menu_list_routes":
            await handle_list_routes(update, context)
        elif data == "menu_manage_routes":
            await handle_manage_routes_menu(update, context)
        elif data == "menu_quick_add":
            await handle_quick_add_menu(update, context)
        elif data == "menu_discord_routes":
            await start_discord_route_management(update, context)
        elif data == "menu_start_forward":
            await handle_start_forwarding(update, context)
        elif data == "menu_stop_forward":
            await handle_stop_forwarding(update, context)
        elif data == "menu_status":
            await handle_status(update, context)
        elif data == "menu_media_stats":
            await handle_media_stats(update, context)
        elif data == "menu_deletion_stats":
            await show_deletion_performance(update, context)
        elif data == "menu_check_permissions":
            await check_bot_permissions(update, context)
        elif data == "menu_help":
            await show_help(update, context)
        
        # Channel selection and management
        elif data == "browse_channels":
            await handle_browse_channels(update, context)
        elif data.startswith("toggle_channel_"):
            parts = data.split("_")
            channel_id = parts[2]
            page = int(parts[3])
            await handle_channel_toggle(update, context, channel_id, page)
        elif data.startswith("channel_sel_page_"):
            page = int(data.split("_")[3])
            await handle_channel_selection_pagination(update, context, page)
        elif data == "save_channel_selection":
            await handle_save_channel_selection(update, context)
        elif data == "select_all_channels":
            await handle_select_all_channels(update, context)
        elif data == "clear_all_channels":
            await handle_clear_all_channels(update, context)
        elif data.startswith("channel_mgmt_page_"):
            page = int(data.split("_")[3])
            await handle_channel_management_pagination(update, context, page)
        elif data.startswith("remove_channel_"):
            parts = data.split("_")
            channel_key = parts[2]
            page = int(parts[3])
            await handle_channel_removal(update, context, channel_key, page)
        elif data == "remove_all_channels":
            await handle_remove_all_channels(update, context)
        
        # Manual channel input
        elif data.startswith("manual_input_"):
            step = data.split("_")[2]
            await handle_manual_channel_input(update, context, step)
        
        # Route management
        elif data == "select_route_channels":
            await handle_select_route_channels(update, context)
        elif data.startswith("route_select_"):
            # Parse route selection callback data - handle underscores in channel keys
            # Format: route_select_source_channelKey_page
            # or: route_select_target_channelKey_page
            parts = data.split("_", 3)  # Split only on first 3 underscores
            if len(parts) < 4:
                log_error(f"Invalid route_select callback data: {data}", None)
                await query.answer("❌ Invalid callback data")
                return
            
            step = parts[2]  # "source" or "target"
            remaining = parts[3]  # channelKey_page
            
            # Split remaining part to get channel_key and page
            remaining_parts = remaining.rsplit("_", 1)
            if len(remaining_parts) != 2:
                log_error(f"Invalid route_select remaining data: {remaining}", None)
                await query.answer("❌ Invalid callback data")
                return
            
            channel_key = remaining_parts[0]
            try:
                page = int(remaining_parts[1])
            except ValueError:
                log_error(f"Invalid page in route_select: {remaining_parts[1]}", None)
                await query.answer("❌ Invalid page number")
                return
            
            await handle_route_channel_selection(update, context, step, channel_key, page)
            
        elif data.startswith("route_"):
            # Parse route pagination callback data
            # Format: route_source_page_0 or route_target_page_0
            parts = data.split("_")
            if len(parts) != 4:
                log_error(f"Invalid route pagination callback data: {data}", None)
                await query.answer("❌ Invalid callback data")
                return
            
            step = parts[1]  # "source" or "target"
            try:
                page = int(parts[3])
            except ValueError:
                log_error(f"Invalid page in route pagination: {parts[3]}", None)
                await query.answer("❌ Invalid page number")
                return
            
            await handle_route_selection_pagination(update, context, step, page)
        # elif data == "manual_route_input":
        #     await handle_manual_channel_input(update, context, "source")
        
        # Route toggling and pagination
        elif data.startswith("toggle_route_"):
            parts = data.split("_")
            route_key = "_".join(parts[2:-1])
            page = int(parts[-1])
            await handle_route_toggle(update, context, route_key, page)
        elif data.startswith("routes_page_"):
            page = int(data.split("_")[2])
            await handle_route_management_pagination(update, context, page)
        elif data == "enable_all_routes":
            await handle_enable_all_routes(update, context)
        elif data == "disable_all_routes":
            await handle_disable_all_routes(update, context)
        
        # Route deletion
        elif data == "delete_routes_mode":
            await handle_delete_routes_mode(update, context)
        elif data.startswith("toggle_delete_"):
            parts = data.split("_")
            route_key = "_".join(parts[2:-1])
            page = int(parts[-1])
            await handle_route_deletion_toggle(update, context, route_key, page)
        elif data.startswith("delete_page_"):
            page = int(data.split("_")[2])
            await handle_deletion_pagination(update, context, page)
        elif data == "select_all_routes":
            await handle_select_all_routes(update, context)
        elif data == "clear_selection":
            await handle_clear_selection(update, context)
        elif data == "confirm_deletion":
            await handle_confirm_deletion(update, context)
        
        # Keyword management
        elif data == "edit_required_keywords":
            await handle_keyword_editing_mode(update, context)
        elif data == "edit_blocked_keywords":
            await handle_keyword_editing_mode(update, context)
        elif data.startswith("remove_keyword_"):
            parts = data.split("_")
            keyword_type = parts[2]
            index = int(parts[3])
            await handle_keyword_removal(update, context, keyword_type, index)
        elif data.startswith("clear_keywords_"):
            keyword_type = data.split("_")[2]
            await handle_clear_keywords(update, context, keyword_type)
        elif data.startswith("add_keyword_"):
            await handle_add_keyword_mode(update, context)
        elif data == "save_keyword_settings":
            await save_keyword_settings(update, context)
        elif data == "reset_all_keywords":
            await handle_reset_all_keywords(update, context)
        elif data == "keyword_filtering_help":
            await show_keyword_filtering_help(update, context)
        
        # Media filter management
        elif data.startswith("toggle_media_"):
            media_type = data.split("_")[2]
            await handle_media_type_toggle(update, context, media_type)
        elif data == "allow_all_media":
            await handle_bulk_media_actions(update, context, "allow_all_media")
        elif data == "block_all_media":
            await handle_bulk_media_actions(update, context, "block_all_media")
        elif data == "save_media_filters":
            await save_media_filters(update, context)
        
        # Discord route management
        elif data == "add_discord_route":
            await start_add_discord_route(update, context)
        elif data == "view_discord_routes":
            await view_discord_routes(update, context)
        elif data == "delete_discord_route":
            await start_delete_discord_route(update, context)
        elif data == "discord_settings":
            await show_discord_settings(update, context)
        elif data == "continue_webhook_discord":
            await handle_continue_webhook_discord(update, context)
        elif data.startswith("discord_select_source_"):
            parts = data.split("_")
            channel_key = parts[3]
            page = int(parts[4])
            await handle_discord_source_selection(update, context, channel_key, page)
        elif data.startswith("discord_source_page_"):
            page = int(data.split("_")[3])
            await handle_discord_source_pagination(update, context, page)
        elif data == "discord_back_to_sources":
            await handle_discord_back_to_sources(update, context)
        elif data.startswith("discord_delete_"):
            parts = data.split("_")
            route_key = parts[2]
            page = int(parts[3])
            await handle_discord_route_deletion(update, context, route_key, page)
        elif data.startswith("discord_del_page_"):
            page = int(data.split("_")[3])
            await handle_discord_deletion_pagination(update, context, page)
        
        else:
            log_activity(f"Unknown callback data: {data}")
            await query.answer("❌ Unknown command")
    
    except Exception as e:
        log_error(f"Error handling callback query: {data}", e)
        await query.answer("❌ An error occurred")

# ========= MAIN FUNCTION =========
def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Initialize message tracking files
    setup_message_mappings_file()
    setup_discord_message_mappings_file()
    
    # Load initial settings
    refresh_user_settings()
    
    # Start the Telegram client
    client.start()
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
