import os
import asyncio
import aiohttp
import aiofiles
import tempfile
import time
import re
import subprocess
import json
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.error import Conflict, BadRequest, Forbidden
import yt_dlp
from config import (
    BOT_TOKEN,
    BOT_API_BASE_URL,
    BOT_API_BASE_FILE_URL,
    TG_SESSION_STRING,
    BRIDGE_CHANNEL_ID,
    AUTHORIZED_USERS as CFG_AUTH_USERS,
    ALLOW_ALL,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_REDIRECT_URI,
    REDDIT_USERNAME,
    REDDIT_PASSWORD,
)
try:
    from uploader import upload_to_bridge
except Exception:
    upload_to_bridge = None

try:
    from reddit_auth import RedditAuth
except Exception:
    RedditAuth = None

class TelegramDownloadBot:
    def __init__(self):
        # Create and configure the application with better timeout settings
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .read_timeout(60)
            .write_timeout(60)
            .connect_timeout(30)
            .pool_timeout(60)
            .get_updates_read_timeout(60)
            .build()
        )
        if BOT_API_BASE_URL:
            # Point to local Bot API server to lift 50MB cloud limit (up to 2GB)
            builder = (
                Application.builder()
                .token(BOT_TOKEN)
                .base_url(BOT_API_BASE_URL)
            )
            if BOT_API_BASE_FILE_URL:
                builder = builder.base_file_url(BOT_API_BASE_FILE_URL)
            # Increase timeouts for large media uploads
            req = HTTPXRequest(
                read_timeout=None,
                write_timeout=None,
                connect_timeout=30.0,
                pool_timeout=30.0,
                media_write_timeout=None,
            )
            builder = builder.request(req).get_updates_request(req)
            application = builder.build()
            print(f"🔗 Using Local Bot API server: {BOT_API_BASE_URL}")

        # Define a post_init hook to run after application initialization
        async def _post_init(app):
            try:
                await app.bot.delete_webhook(drop_pending_updates=True)
                print("🔧 Webhook removed; polling enabled.")
            except Exception as e:
                print(f"⚠️ Webhook removal failed: {e}")
            
            # Add retry mechanism for get_me() to handle flood control
            import asyncio
            from telegram.error import RetryAfter
            
            for attempt in range(3):
                try:
                    me = await app.bot.get_me()
                    print(f"✅ Bot connected: @{me.username}")
                    break
                except RetryAfter as e:
                    if attempt < 2:
                        wait_time = min(e.retry_after, 60)  # Max 60 seconds
                        print(f"⏳ Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        print("⚠️ Rate limit exceeded, continuing without verification")
                        break
                except Exception as e:
                    if attempt < 2:
                        print(f"⚠️ Connection attempt {attempt + 1} failed, retrying...")
                        await asyncio.sleep(5)
                    else:
                        print(f"⚠️ Bot verification failed: {e}")
                        break
        
        # Set the post_init hook
        application.post_init = _post_init
        self.app = application
        # Authorized user IDs
        default_users = {818185073, 6936101187, 7972834913}
        self.authorized_users = CFG_AUTH_USERS or {818185073, 6936101187, 7972834913}
        self.allow_all = ALLOW_ALL
        
        # Initialize Reddit authentication
        self.reddit_auth = None
        if RedditAuth and REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            self.reddit_auth = RedditAuth(
                REDDIT_CLIENT_ID,
                REDDIT_CLIENT_SECRET,
                REDDIT_REDIRECT_URI,
                username=REDDIT_USERNAME,
                password=REDDIT_PASSWORD,
            )
        
        # Store pending Reddit authentications
        self.pending_reddit_auth = {}
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup command and message handlers"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("id", self.id_command))
        self.app.add_handler(CommandHandler("reddit_auth", self.reddit_auth_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_link))
        # Centralized error handler (e.g., for 409 Conflict)
        self.app.add_error_handler(self.error_handler)
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Log errors globally to avoid noisy tracebacks and explain common cases."""
        err = context.error
        if isinstance(err, Conflict) or (err and "Conflict" in str(err)):
            print("⚠️ Conflict: Another getUpdates request is running. Ensure only one bot instance is polling.")
            return
        print(f"⚠️ Unhandled error: {err}")
    
    def is_authorized_user(self, user_id: int) -> bool:
        """Check if user is authorized to use the bot"""
        if self.allow_all:
            return True
        return user_id in self.authorized_users
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        print(f"📱 /start command received from user: {user.first_name} (@{user.username}) - ID: {user.id}")
        
        # Check if user is authorized - silently ignore if not
        if not self.is_authorized_user(user.id):
            print(f"🚫 Unauthorized access attempt by {user.first_name} (ID: {user.id})")
            await update.message.reply_text(
                f"🚫 دسترسی شما مجاز نیست.\nشناسه شما: {user.id}\nاز ادمین بخواهید شما را به لیست مجاز اضافه کند یا موقتاً ALLOW_ALL را فعال کند."
            )
            return
        welcome_message = """
        سلام! من ربات دانلود فایل و ویدیو هستم

        لینک مستقیم دانلود فایل یا لینک ویدیو خودتون رو برام بفرستید تا براتون دانلود کنم و ارسال کنم.

        پشتیبانی از سایت‌های ویدیو: YouTube, Pornhub, Xvideos, LuxureTV و...
        پشتیبانی از لینک‌های مستقیم دانلود

        برای راهنمایی /help رو بزنید.
        """
        await update.message.reply_text(welcome_message)
        print(f"✅ Welcome message sent to {user.first_name}")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        user = update.effective_user
        print(f"❓ /help command received from user: {user.first_name} (@{user.username}) - ID: {user.id}")
        
        # Check if user is authorized - silently ignore if not
        if not self.is_authorized_user(user.id):
            print(f"🚫 Unauthorized help request by {user.first_name} (ID: {user.id}) - ignored")
            return
        
        help_message = """
📖 راهنمای استفاده:

1️⃣ لینک مستقیم دانلود فایل یا لینک ویدیو رو برام بفرست
2️⃣ من فایل/ویدیو رو دانلود می‌کنم
3️⃣ فایل رو مستقیماً براتون ارسال می‌کنم

🎬 سایت‌های ویدیو پشتیبانی شده:
• P*rnhub
• YouTube
• Xvideos
• Xnxx
• P*rn300
• Xvv1deos
• Rule34.xxx
• LuxureTV

📁 لینک‌های مستقیم دانلود:
• تمام فرمت‌های فایل
• بدون محدودیت حجم فایل

مثال لینک‌های معتبر:
https://www.pornhub.com/view_video.php?viewkey=...
https://www.porn300.com/video/title/embed/
https://www.xvv1deos.com/video.id/title
https://rule34.xxx/index.php?page=post&s=view&id=...
https://en.luxuretv.com/videos/video-title-12345.html
https://example.com/file.pdf
https://example.com/image.jpg
        """
        await update.message.reply_text(help_message)
        print(f"✅ Help message sent to {user.first_name}")
    
    async def id_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send user their Telegram ID"""
        user_id = update.effective_user.id
        await update.message.reply_text(f"🆔 شناسه شما: `{user_id}`", parse_mode=ParseMode.MARKDOWN)
    
    async def reddit_auth_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle Reddit authentication"""
        user_id = update.effective_user.id
        
        if not self.reddit_auth:
            await update.message.reply_text("❌ Reddit API تنظیم نشده است.")
            return
        
        # If running in PRAW script/read-only mode, no user auth is needed
        if getattr(self.reddit_auth, "is_script_mode", False) or getattr(self.reddit_auth, "is_read_only", False):
            await update.message.reply_text(
                "✅ احراز هویت Reddit قبلاً با PRAW (script mode) انجام شده است.\n"
                "لینک‌های Reddit را مستقیم ارسال کنید تا پردازش شوند."
            )
            return
        
        # Generate auth URL
        state = f"user_{user_id}_{int(time.time())}"
        auth_url = self.reddit_auth.get_auth_url(state)
        compact_url = auth_url.replace('/authorize', '/authorize.compact')
        # Fallback: temporary duration (no refresh token) for debugging "Invalid request" cases
        temp_url = self.reddit_auth.get_auth_url(state, duration="temporary")
        temp_compact_url = temp_url.replace('/authorize', '/authorize.compact')
        
        # Store pending auth
        self.pending_reddit_auth[user_id] = {
            'state': state,
            'timestamp': time.time()
        }
        
        keyboard = [
            [InlineKeyboardButton("🔑 ورود به Reddit (دائم)", url=auth_url)],
            [InlineKeyboardButton("📱 نسخه موبایل (دائم)", url=compact_url)],
            [InlineKeyboardButton("🧪 تست موقت (Temporary)", url=temp_url)],
            [InlineKeyboardButton("📱 تست موقت موبایل", url=temp_compact_url)],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Log helpful debug info
        try:
            print(f"🔑 Reddit OAuth client_id: {REDDIT_CLIENT_ID}")
            print(f"🔑 Reddit OAuth redirect_uri: {self.reddit_auth.redirect_uri}")
            print(f"🔗 Reddit OAuth URL (permanent): {auth_url}")
            print(f"🔗 Reddit OAuth URL (temporary):  {temp_url}")
        except Exception:
            pass

        await update.message.reply_text(
            (
                "🔴 برای دسترسی به Reddit، لطفاً مراحل زیر را دنبال کنید:\n\n"
                "1️⃣ روی یکی از دکمه‌های زیر کلیک کنید\n"
                "2️⃣ وارد حساب Reddit خود شوید\n"
                "3️⃣ روی \"Allow\" کلیک کنید\n"
                "4️⃣ بعد از redirect، کد موجود در URL را کپی کنید\n"
                "5️⃣ کد را برای من ارسال کنید (یا می‌توانید کل آدرس صفحه را هم ارسال کنید)\n\n"
                "💡 اگر به صفحه خطا رسیدید، می‌توانید این لینک را در مرورگر کپی کنید:\n"
                f"{auth_url}"
            ),
            reply_markup=reply_markup
        )
    
    async def handle_reddit_auth_code(self, update: Update, code: str):
        """Handle Reddit authorization code from user"""
        user_id = update.effective_user.id
        
        if user_id not in self.pending_reddit_auth:
            await update.message.reply_text("❌ درخواست احراز هویت یافت نشد. لطفاً دوباره /reddit_auth را اجرا کنید.")
            return
        
        try:
            # Exchange code for token
            success = await self.reddit_auth.exchange_code_for_token(code)
            
            if success:
                # Remove pending auth
                del self.pending_reddit_auth[user_id]
                await update.message.reply_text("✅ احراز هویت Reddit موفق بود! حالا می‌توانید لینک‌های Reddit را ارسال کنید.")
            else:
                await update.message.reply_text("❌ احراز هویت ناموفق. لطفاً دوباره تلاش کنید.")
                
        except Exception as e:
            print(f"❌ Reddit auth error: {e}")
            await update.message.reply_text("❌ خطا در احراز هویت. لطفاً دوباره تلاش کنید.")
    
    async def handle_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle download links sent by users"""
        user = update.effective_user
        url = update.message.text.strip()
        
        print(f"🔗 Download request received from {user.first_name} (@{user.username}) - ID: {user.id}")
        print(f"📎 Requested URL: {url}")
        
        # Check if user is authorized - silently ignore if not
        if not self.is_authorized_user(user.id):
            print(f"🚫 Unauthorized download request by {user.first_name} (ID: {user.id})")
            await update.message.reply_text(
                f"🚫 دسترسی شما مجاز نیست.\nشناسه شما: {user.id}\nاز ادمین بخواهید شما را به لیست مجاز اضافه کند یا موقتاً ALLOW_ALL را فعال کند."
            )
            return
        
        # Check if this might be a Reddit authorization code (raw code or full redirect URL)
        if user.id in self.pending_reddit_auth and (
            (len(url) > 10 and not url.startswith('http')) or ('code=' in url)
        ):
            await self.handle_reddit_auth_code(update, url)
            return
        
        # Check if the message contains a valid URL
        if not self.is_valid_url(url):
            print(f"❌ Invalid URL provided by {user.first_name}")
            await update.message.reply_text("❌ لینک نامعتبر است! لطفاً یک لینک مستقیم دانلود یا لینک ویدیو ارسال کنید.")
            return
        
        # Send processing message
        print(f"⏳ Starting download process for {user.first_name}")
        processing_msg = await update.message.reply_text("⏳ در حال دانلود فایل...")
        
        try:
            # Check if it's qombol.com - handle specially
            if 'qombol.com' in url.lower():
                print(f"🎬 Detected qombol.com URL, using custom handler: {url}")
                result = await self.download_qombol_content(url, processing_msg, user.first_name)
                if result == (None, None, None):
                    # Handler provided user message, no further action needed
                    return
                file_path, filename, file_size = result
            # Check if it's Instagram - handle specially
            elif 'instagram.com' in url.lower():
                print(f"📸 Detected Instagram URL, using custom handler: {url}")
                result = await self.download_instagram_content(url, processing_msg, user.first_name)
                if result == (None, None, None):
                    return
                file_path, filename, file_size = result
            # Check if it's Reddit - handle specially  
            elif 'reddit.com' in url.lower() or 'v.redd.it' in url.lower():
                print(f"🔴 Detected Reddit URL, using custom handler: {url}")
                result = await self.download_reddit_content(url, processing_msg, user.first_name)
                if result == (None, None, None):
                    return
                file_path, filename, file_size = result
            # Check if it's Rule34.xxx - handle specially to bypass captcha
            elif 'rule34.xxx' in url.lower():
                print(f"🔞 Detected Rule34.xxx URL, using captcha bypass handler: {url}")
                result = await self.download_rule34_bypass_captcha(url, processing_msg, user.first_name)
                if result == (None, None, None):
                    return
                file_path, filename, file_size = result
            # Check if it's a video site URL that needs yt-dlp
            elif self.is_video_site_url(url):
                print(f"📹 Detected video site URL, using yt-dlp: {url}")
                file_path, filename, file_size = await self.download_video_with_ytdlp(url, processing_msg, user.first_name)
            else:
                # Download the file with progress
                print(f"📥 Downloading file from: {url}")
                file_path, filename, file_size = await self.download_file(url, processing_msg, user.first_name)
            print(f"✅ File downloaded successfully: {filename} ({self.format_file_size(file_size)})")
            
            # Check if file is suspiciously small (likely an error file)
            if file_size < 1024:  # Less than 1KB
                raise Exception(f"فایل دانلود شده خیلی کوچک است ({self.format_file_size(file_size)}). احتمالاً خطا رخ داده است.")
            
            # No file size limit - removed all restrictions
            
            # Upload with progress tracking - detect file type
            print(f"📤 Uploading file to Telegram for {user.first_name}")
            await self.upload_with_progress(update, context, processing_msg, file_path, filename, file_size, user.first_name)
            
            print(f"✅ File successfully sent to {user.first_name}: {filename}")
            
            # Delete processing message
            await processing_msg.delete()
            
            # Schedule file deletion after 20 seconds
            print(f"🗑️ Scheduled file cleanup in 20 seconds: {filename}")
            asyncio.create_task(self.delayed_file_cleanup(file_path, 20))
            
        except Exception as e:
            print(f"❌ Error processing request from {user.first_name}: {str(e)}")
            await processing_msg.edit_text(f"❌ خطا در دانلود فایل: {str(e)}")
    
    def is_valid_url(self, url: str) -> bool:
        """Check if the provided string is a valid URL"""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False
    
    def is_video_site_url(self, url: str) -> bool:
        """Check if URL is from supported video sites"""
        video_sites = [
            'pornhub.com', 'www.pornhub.com',
            'youtube.com', 'www.youtube.com', 'youtu.be',
            'xvideos.com', 'www.xvideos.com',
            'xnxx.com', 'www.xnxx.com',
            'porn300.com', 'www.porn300.com',
            'xvv1deos.com', 'www.xvv1deos.com',
            'motherless.com', 'www.motherless.com',
            'rule34.xxx', 'www.rule34.xxx',
            # Working porn sites with yt-dlp support
            'redtube.com', 'www.redtube.com',
            'tube8.com', 'www.tube8.com',
            'youporn.com', 'www.youporn.com',
            'spankbang.com', 'www.spankbang.com',
            'eporner.com', 'www.eporner.com',
            'txxx.com', 'www.txxx.com',
            'beeg.com', 'www.beeg.com',
            'tnaflix.com', 'www.tnaflix.com',
            'empflix.com', 'www.empflix.com',
            'drtuber.com', 'www.drtuber.com'
        ]
        try:
            parsed = urlparse(url.lower())
            return any(site in parsed.netloc for site in video_sites)
        except:
            return False
    
    async def extract_mediadelivery_video(self, embed_url: str) -> str:
        """Extract direct video URL from mediadelivery.net embed"""
        try:
            print(f"🔍 Extracting from mediadelivery embed: {embed_url}")
            
            # Fetch the embed page
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://www.qombol.com/',
            }
            
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(embed_url) as response:
                    if response.status != 200:
                        raise Exception(f"HTTP {response.status}")
                    
                    embed_content = await response.text()
            
            print(f"📄 Embed page content length: {len(embed_content)}")
            
            # Look for video URLs in the embed page
            import re
            video_patterns = [
                r'"src":\s*"([^"]*\.mp4[^"]*)"',
                r'"file":\s*"([^"]*\.mp4[^"]*)"',
                r'"url":\s*"([^"]*\.mp4[^"]*)"',
                r'src:\s*"([^"]*\.mp4[^"]*)"',
                r'file:\s*"([^"]*\.mp4[^"]*)"',
                r'https://[^"\s]*\.b-cdn\.net/[^"\s]*\.mp4',
                r'https://[^"\s]*bunnycdn[^"\s]*\.mp4',
                r'https://[^"\s]*mediadelivery[^"\s]*\.mp4',
            ]
            
            for i, pattern in enumerate(video_patterns):
                matches = re.findall(pattern, embed_content, re.IGNORECASE)
                if matches:
                    video_url = matches[0]
                    print(f"✅ Found video URL with pattern {i+1}: {video_url}")
                    
                    # Clean up the URL (remove escape characters)
                    video_url = video_url.replace('\\/', '/')
                    return video_url
            
            # If no direct video found, try to construct the URL from embed parameters
            # Extract video ID from embed URL
            import re
            video_id_match = re.search(r'/embed/(\d+)/([a-f0-9-]+)', embed_url)
            if video_id_match:
                library_id = video_id_match.group(1)
                video_id = video_id_match.group(2)
                print(f"📋 Extracted IDs - Library: {library_id}, Video: {video_id}")
                
                # Try common BunnyCDN/MediaDelivery URL patterns
                possible_urls = [
                    f"https://vz-{library_id}.b-cdn.net/{video_id}/playlist.m3u8",
                    f"https://vz-{library_id}.b-cdn.net/{video_id}/play_720p.mp4",
                    f"https://vz-{library_id}.b-cdn.net/{video_id}/play_480p.mp4",
                    f"https://vz-{library_id}.b-cdn.net/{video_id}/play_360p.mp4",
                    f"https://vz-{library_id}.b-cdn.net/{video_id}/play_240p.mp4",
                    f"https://iframe.mediadelivery.net/play/{library_id}/{video_id}",
                    f"https://customer-{library_id}.cloudflarestream.com/{video_id}/manifest/video.m3u8",
                    f"https://videodelivery.net/{video_id}/mp4/download",
                ]
                
                # Create a new session for testing URLs with proper authentication headers
                auth_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Referer': embed_url,
                    'Origin': 'https://iframe.mediadelivery.net',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Sec-Fetch-Dest': 'video',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'cross-site',
                }
                
                test_timeout = aiohttp.ClientTimeout(total=10, connect=5)
                async with aiohttp.ClientSession(timeout=test_timeout, headers=auth_headers) as test_session:
                    for i, test_url in enumerate(possible_urls):
                        try:
                            print(f"🔍 Testing URL {i+1}: {test_url}")
                            
                            # Try both HEAD and GET requests
                            for method in ['HEAD', 'GET']:
                                try:
                                    if method == 'HEAD':
                                        async with test_session.head(test_url, allow_redirects=True) as test_response:
                                            status = test_response.status
                                    else:
                                        # For GET, only read first few bytes to check if it's valid
                                        async with test_session.get(test_url, allow_redirects=True) as test_response:
                                            status = test_response.status
                                            if status == 200:
                                                # Read first few bytes to verify it's a video
                                                chunk = await test_response.content.read(1024)
                                                if chunk and (b'ftyp' in chunk or b'moov' in chunk or b'#EXTM3U' in chunk):
                                                    print(f"✅ Verified video content in URL: {test_url}")
                                                    return test_url
                                    
                                    print(f"   {method} Response: {status}")
                                    if status == 200:
                                        print(f"✅ Found working video URL: {test_url}")
                                        return test_url
                                    elif status in [302, 301]:
                                        # Follow redirect
                                        redirect_url = str(test_response.headers.get('Location', ''))
                                        if redirect_url and any(ext in redirect_url for ext in ['.mp4', '.m3u8']):
                                            print(f"✅ Found redirect video URL: {redirect_url}")
                                            return redirect_url
                                    elif status == 403:
                                        # 403 might mean the URL exists but needs different auth
                                        continue
                                    else:
                                        break  # Try next URL
                                        
                                except Exception as e:
                                    print(f"   {method} Error: {e}")
                                    continue
                                    
                        except Exception as e:
                            print(f"   Error: {e}")
                            continue
            
            print("⚠️ Could not extract direct video URL from mediadelivery embed")
            return None
            
        except Exception as e:
            print(f"❌ Error extracting mediadelivery video: {e}")
            return None
    
    async def download_instagram_content(self, url: str, progress_msg=None, user_name: str = "") -> tuple:
        """Handle Instagram downloads with fallback message"""
        try:
            if progress_msg:
                await progress_msg.edit_text("📸 در حال پردازش لینک Instagram...")
            
            # Instagram requires authentication, provide alternative
            if progress_msg:
                await progress_msg.edit_text(
                    f"📸 Instagram محدودیت‌های دسترسی دارد.\n\n"
                    f"🔗 لینک اصلی:\n{url}\n\n"
                    f"💡 راه‌های جایگزین:\n"
                    f"• از اپلیکیشن Instagram ذخیره کنید\n"
                    f"• از سایت‌های دانلود آنلاین استفاده کنید\n"
                    f"• لینک را در مرورگر باز کنید"
                )
                return None, None, None
        except Exception as e:
            print(f"❌ Error handling Instagram: {e}")
            raise Exception(f"خطا در پردازش Instagram: {str(e)}")
    
    async def resolve_reddit_url(self, url: str) -> str:
        """Resolve Reddit short/share URLs (e.g., /s/ or redd.it) to the canonical post URL"""
        try:
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    final_url = str(resp.url)
                    return final_url or url
        except Exception as e:
            print(f"⚠️ Could not resolve Reddit URL redirect: {e}")
            return url

    async def download_reddit_content(self, url: str, progress_msg=None, user_name: str = "") -> tuple:
        """Handle Reddit downloads using API when authenticated"""
        try:
            if progress_msg:
                await progress_msg.edit_text("🔴 در حال پردازش لینک Reddit...")
            original_url = url
            # Resolve Reddit share/short URLs
            if '/s/' in url or 'redd.it' in url.lower():
                resolved = await self.resolve_reddit_url(url)
                if resolved and resolved != url:
                    url = resolved
                    if progress_msg:
                        try:
                            await progress_msg.edit_text("🔴 در حال پردازش لینک Reddit (اصلاح ریدایرکت)...")
                        except:
                            pass
            
            # Check if we have Reddit API access
            if self.reddit_auth and getattr(self.reddit_auth, 'is_available', None) and self.reddit_auth.is_available():
                try:
                    # Try to get post data using Reddit API
                    post_data = await self.reddit_auth.get_post_data(url)
                    
                    if post_data:
                        # Extract video URL from various possible fields
                        video_url = None
                        if post_data.get('is_video'):
                            video_url = (
                                post_data.get('media', {}).get('reddit_video', {}).get('fallback_url')
                                or post_data.get('secure_media', {}).get('reddit_video', {}).get('fallback_url')
                            )
                        if not video_url:
                            # Some posts expose preview.reddit_video_preview
                            preview = post_data.get('preview') or {}
                            if isinstance(preview, dict):
                                video_url = (
                                    preview.get('reddit_video_preview', {}) or {}
                                ).get('fallback_url')
                        
                        if video_url:
                            if progress_msg:
                                await progress_msg.edit_text("⏬ در حال دانلود ویدیو از Reddit...")
                            
                            # Download the video file directly
                            return await self.download_file(video_url, progress_msg, user_name)
                    
                    # Try yt-dlp as a fallback even if API did not return video
                    try:
                        if progress_msg:
                            await progress_msg.edit_text("📹 تلاش برای دانلود با yt-dlp...")
                        return await self.download_video_with_ytdlp(url, progress_msg, user_name)
                    except Exception as e:
                        print(f"⚠️ yt-dlp fallback failed: {e}")

                    # If not a video or no video URL found, provide link
                    if progress_msg:
                        await progress_msg.edit_text(
                            f"🔴 این پست Reddit شامل ویدیو قابل دانلود نیست.\n\n"
                            f"🔗 لینک اصلی:\n{url}\n\n"
                            f"💡 می‌توانید لینک را در مرورگر باز کنید."
                        )
                        return None, None, None
                        
                except Exception as api_error:
                    print(f"⚠️ Reddit API failed: {api_error}")
                    # Fall through to auth message
            
            # No authentication or API failed - avoid asking user to authenticate
            if progress_msg:
                await progress_msg.edit_text(
                    f"🔴 در حال حاضر امکان دانلود مستقیم از Reddit فراهم نشد.\n\n"
                    f"🔗 لینک:\n{url}\n\n"
                    f"💡 لطفاً لینک اصلی پست (نه share /s/) را ارسال کنید یا بعداً دوباره تلاش کنید."
                )
                return None, None, None
                
        except Exception as e:
            error_msg = f"خطا در پردازش Reddit: {str(e)}"
            print(f"❌ {error_msg}")
            if progress_msg:
                try:
                    await progress_msg.edit_text(
                        f"🔴 خطا در پردازش Reddit.\n\n"
                        f"🔗 لینک اصلی:\n{url}\n\n"
                        f"💡 لطفاً لینک را در مرورگر باز کنید."
                    )
                    return None, None, None
                except:
                    pass
            raise Exception(error_msg)
    
    async def download_rule34_bypass_captcha(self, url: str, progress_msg=None, user_name: str = "") -> tuple:
        """Handle Rule34.xxx downloads with captcha bypass techniques"""
        try:
            if progress_msg:
                await progress_msg.edit_text("🔞 در حال دور زدن محافظت‌های Rule34...")
            
            # Method 1: Try with session and cookies to simulate browser behavior
            import aiohttp
            import asyncio
            import time
            import random
            
            # Create a persistent session with browser-like behavior
            jar = aiohttp.CookieJar()
            timeout = aiohttp.ClientTimeout(total=60, connect=30)
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            }
            
            async with aiohttp.ClientSession(
                timeout=timeout, 
                headers=headers, 
                cookie_jar=jar,
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session:
                
                # Step 1: Visit homepage first to get cookies
                try:
                    if progress_msg:
                        await progress_msg.edit_text("🔞 مرحله 1: دریافت کوکی‌های اولیه...")
                    
                    async with session.get('https://rule34.xxx/') as resp:
                        homepage_content = await resp.text()
                        print(f"📄 Homepage status: {resp.status}")
                        
                    # Wait a bit to simulate human behavior
                    await asyncio.sleep(random.uniform(2, 4))
                    
                except Exception as e:
                    print(f"⚠️ Homepage visit failed: {e}")
                
                # Step 2: Try to access the target page
                if progress_msg:
                    await progress_msg.edit_text("🔞 مرحله 2: دسترسی به صفحه هدف...")
                
                # Add referer for the actual request
                headers['Referer'] = 'https://rule34.xxx/'
                
                async with session.get(url, headers=headers) as response:
                    if response.status == 403:
                        # Try alternative methods
                        if progress_msg:
                            await progress_msg.edit_text("🔞 مرحله 3: تلاش با روش‌های جایگزین...")
                        
                        # Method 2: Try with different user agents
                        alternative_agents = [
                            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
                        ]
                        
                        for agent in alternative_agents:
                            headers['User-Agent'] = agent
                            await asyncio.sleep(random.uniform(1, 3))
                            
                            async with session.get(url, headers=headers) as retry_resp:
                                if retry_resp.status == 200:
                                    response = retry_resp
                                    break
                        else:
                            # Method 3: Try API endpoint if exists
                            post_id = None
                            import re
                            id_match = re.search(r'id=(\d+)', url)
                            if id_match:
                                post_id = id_match.group(1)
                                api_url = f'https://rule34.xxx/index.php?page=dapi&s=post&q=index&id={post_id}'
                                
                                if progress_msg:
                                    await progress_msg.edit_text("🔞 مرحله 4: تلاش از طریق API...")
                                
                                async with session.get(api_url, headers=headers) as api_resp:
                                    if api_resp.status == 200:
                                        api_content = await api_resp.text()
                                        # Parse XML response to get file URL
                                        file_url_match = re.search(r'file_url="([^"]+)"', api_content)
                                        if file_url_match:
                                            media_url = file_url_match.group(1)
                                            if progress_msg:
                                                await progress_msg.edit_text("✅ فایل پیدا شد! در حال دانلود...")
                                            return await self.download_file(media_url, progress_msg, user_name)
                    
                    if response.status != 200:
                        raise Exception(f"HTTP {response.status}")
                    
                    page_content = await response.text()
                    print(f"📄 Rule34 page content length: {len(page_content)}")
                    
                    # Parse the page to find media URLs
                    import re
                    media_url = None
                    
                    # Look for various media URL patterns
                    media_patterns = [
                        r'<img[^>]*src=["\']([^"\']*(?:\.jpg|\.jpeg|\.png|\.gif|\.webm|\.mp4)[^"\']*)["\'][^>]*(?:id=["\']image["\']|class=["\'].*image.*["\'])',
                        r'<video[^>]*src=["\']([^"\']+)["\']',
                        r'<source[^>]*src=["\']([^"\']+)["\']',
                        r'"file_url":\s*"([^"]+)"',
                        r'"sample_url":\s*"([^"]+)"',
                        r'https://[^"\s]*rule34[^"\s]*\.(?:jpg|jpeg|png|gif|webm|mp4)',
                        r'https://[^"\s]*\.(?:jpg|jpeg|png|gif|webm|mp4)',
                    ]
                    
                    for i, pattern in enumerate(media_patterns):
                        matches = re.findall(pattern, page_content, re.IGNORECASE)
                        if matches:
                            # Filter out thumbnails and small images
                            for match in matches:
                                if any(skip in match.lower() for skip in ['thumb', 'preview', 'small', 'icon', 'avatar']):
                                    continue
                                media_url = match
                                print(f"✅ Found media URL with pattern {i+1}: {media_url}")
                                break
                            if media_url:
                                break
                    
                    if not media_url:
                        # Last resort: try yt-dlp with session cookies
                        if progress_msg:
                            await progress_msg.edit_text("🔞 تلاش نهایی با yt-dlp...")
                        
                        # Export cookies for yt-dlp
                        cookies_str = ""
                        for cookie in jar:
                            cookies_str += f"{cookie.key}={cookie.value}; "
                        
                        # Try yt-dlp with cookies
                        try:
                            return await self.download_video_with_ytdlp_cookies(url, cookies_str, progress_msg, user_name)
                        except Exception as e:
                            print(f"⚠️ yt-dlp with cookies failed: {e}")
                        
                        if progress_msg:
                            await progress_msg.edit_text(
                                f"🔞 متأسفانه نتوانستم محافظت‌های Rule34 را دور بزنم.\n\n"
                                f"🔗 لینک اصلی:\n{url}\n\n"
                                f"💡 راه‌های جایگزین:\n"
                                f"• لینک را در مرورگر باز کنید\n"
                                f"• از VPN استفاده کنید\n"
                                f"• کپچا را حل کنید و دوباره تلاش کنید"
                            )
                            return None, None, None
                    
                    # Clean up the URL
                    if media_url.startswith('//'):
                        media_url = 'https:' + media_url
                    elif media_url.startswith('/'):
                        media_url = 'https://rule34.xxx' + media_url
                    
                    if progress_msg:
                        await progress_msg.edit_text("⏬ در حال دانلود فایل از Rule34...")
                    
                    # Download the media file
                    return await self.download_file(media_url, progress_msg, user_name)
                    
        except Exception as e:
            error_msg = f"خطا در دور زدن محافظت‌های Rule34: {str(e)}"
            print(f"❌ {error_msg}")
            if progress_msg:
                try:
                    await progress_msg.edit_text(
                        f"🔞 خطا در پردازش Rule34.\n\n"
                        f"🔗 لینک اصلی:\n{url}\n\n"
                        f"💡 لطفاً لینک را در مرورگر باز کنید و کپچا را حل کنید."
                    )
                    return None, None, None
                except:
                    pass
            raise Exception(error_msg)
    
    async def download_video_with_ytdlp_cookies(self, url: str, cookies: str, progress_msg=None, user_name: str = "") -> tuple:
        """Download video using yt-dlp with cookies"""
        temp_dir = tempfile.gettempdir()
        
        # Write cookies to temporary file
        import tempfile
        cookie_file = os.path.join(temp_dir, f"cookies_{int(time.time())}.txt")
        
        try:
            with open(cookie_file, 'w') as f:
                # Convert cookies to Netscape format
                f.write("# Netscape HTTP Cookie File\n")
                for cookie_pair in cookies.split(';'):
                    if '=' in cookie_pair:
                        key, value = cookie_pair.strip().split('=', 1)
                        f.write(f"rule34.xxx\tTRUE\t/\tFALSE\t0\t{key}\t{value}\n")
            
            # yt-dlp options with cookies
            ydl_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'format': 'best',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 30,
                'retries': 3,
                'cookiefile': cookie_file,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Referer': 'https://rule34.xxx/',
                },
            }
            
            # Run yt-dlp
            loop = asyncio.get_event_loop()
            
            def download_sync():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title = info.get('title', 'rule34_video')
                    
                    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
                    if len(safe_title) > 100:
                        safe_title = safe_title[:100]
                    
                    ydl_opts['outtmpl'] = os.path.join(temp_dir, f'{safe_title}.%(ext)s')
                    
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_download:
                        ydl_download.download([url])
                    
                    return safe_title, info.get('filesize', 0)
            
            safe_title, estimated_size = await asyncio.wait_for(
                loop.run_in_executor(None, download_sync), 
                timeout=300
            )
            
            # Find downloaded file
            downloaded_files = []
            for file in os.listdir(temp_dir):
                if safe_title in file and not file.endswith('.part'):
                    downloaded_files.append(file)
            
            if not downloaded_files:
                raise Exception("فایل دانلود شده پیدا نشد")
            
            downloaded_file = max(downloaded_files, key=lambda f: os.path.getctime(os.path.join(temp_dir, f)))
            file_path = os.path.join(temp_dir, downloaded_file)
            file_size = os.path.getsize(file_path)
            
            return file_path, downloaded_file, file_size
            
        finally:
            # Clean up cookie file
            try:
                if os.path.exists(cookie_file):
                    os.unlink(cookie_file)
            except:
                pass
    
    async def download_qombol_content(self, url: str, progress_msg=None, user_name: str = "") -> tuple:
        """Download content from qombol.com by extracting video URLs from the page"""
        import re
        import tempfile
        
        try:
            # Update progress message
            if progress_msg:
                try:
                    await progress_msg.edit_text("🔍 در حال استخراج لینک ویدیو از qombol.com...")
                except:
                    pass
            
            # Fetch the webpage content with proper headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise Exception(f"HTTP {response.status}")
                    
                    html_content = await response.text()
            
            print(f"🔍 Analyzing HTML content (length: {len(html_content)})")
            
            # Enhanced patterns for qombol.com specifically
            video_patterns = [
                # Direct video tags
                r'<video[^>]*src=["\']([^"\']+)["\']',
                r'<source[^>]*src=["\']([^"\']+)["\']',
                # JavaScript video URLs
                r'file:\s*["\']([^"\']+\.(?:mp4|avi|mkv|mov|wmv|flv|webm|m3u8))["\']',
                r'src:\s*["\']([^"\']+\.(?:mp4|avi|mkv|mov|wmv|flv|webm|m3u8))["\']',
                r'video_url["\']?\s*:\s*["\']([^"\']+)["\']',
                r'videoUrl["\']?\s*:\s*["\']([^"\']+)["\']',
                r'mp4["\']?\s*:\s*["\']([^"\']+)["\']',
                # CDN patterns common in adult sites
                r'https?://[^"\'\s]*\.b-cdn\.net/[^"\'\s]*\.(?:mp4|avi|mkv|mov|wmv|flv|webm)',
                r'https?://[^"\'\s]*cdn[^"\'\s]*\.(?:mp4|avi|mkv|mov|wmv|flv|webm)',
                # Generic video file URLs
                r'https?://[^"\'\s]+\.(?:mp4|avi|mkv|mov|wmv|flv|webm|m3u8)',
                # WordPress media URLs
                r'wp-content/uploads/[^"\'\s]*\.(?:mp4|avi|mkv|mov|wmv|flv|webm)',
            ]
            
            video_url = None
            for i, pattern in enumerate(video_patterns):
                matches = re.findall(pattern, html_content, re.IGNORECASE)
                if matches:
                    print(f"✅ Found video with pattern {i+1}: {matches[0]}")
                    video_url = matches[0]
                    break
            
            if not video_url:
                # Try to find embedded players
                embed_patterns = [
                    r'<iframe[^>]*src=["\']([^"\']+)["\']',
                    r'<embed[^>]*src=["\']([^"\']+)["\']',
                    r'embed_url["\']?\s*:\s*["\']([^"\']+)["\']',
                    # Look for player URLs
                    r'player["\']?\s*:\s*["\']([^"\']+)["\']',
                ]
                
                for i, pattern in enumerate(embed_patterns):
                    matches = re.findall(pattern, html_content, re.IGNORECASE)
                    if matches:
                        embed_url = matches[0]
                        print(f"🔗 Found embed with pattern {i+1}: {embed_url}")
                        
                        # Check if it's a known video platform or streaming service
                        if any(domain in embed_url.lower() for domain in ['youtube.com', 'vimeo.com', 'dailymotion.com', 'pornhub.com', 'xvideos.com', 'mediadelivery.net', 'bunnycdn.com', 'jwplayer.com']):
                            print(f"🎯 Recognized video service: {embed_url}")
                            # For mediadelivery.net, try to extract direct video URL
                            if 'mediadelivery.net' in embed_url.lower():
                                try:
                                    video_url = await self.extract_mediadelivery_video(embed_url)
                                    if video_url:
                                        break
                                except Exception as e:
                                    print(f"⚠️ Failed to extract from mediadelivery: {e}")
                            else:
                                video_url = embed_url
                                break
                        # Or if it contains video file extension
                        elif any(ext in embed_url.lower() for ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']):
                            video_url = embed_url
                            break
            
            if not video_url:
                # Last resort: look for any media URLs in the page
                media_patterns = [
                    r'(https?://[^"\'\s]*(?:video|media|stream)[^"\'\s]*\.(?:mp4|avi|mkv|mov|wmv|flv|webm))',
                    r'(https?://[^"\'\s]*\.(?:mp4|avi|mkv|mov|wmv|flv|webm)[^"\'\s]*)',
                ]
                
                for pattern in media_patterns:
                    matches = re.findall(pattern, html_content, re.IGNORECASE)
                    if matches:
                        video_url = matches[0]
                        print(f"📹 Found media URL: {video_url}")
                        break
            
            if not video_url:
                # Last resort: try yt-dlp on the embed URL if we found one
                embed_patterns = [r'<iframe[^>]*src=["\']([^"\']+)["\']']
                for pattern in embed_patterns:
                    matches = re.findall(pattern, html_content, re.IGNORECASE)
                    if matches:
                        embed_url = matches[0]
                        if 'mediadelivery.net' in embed_url or 'iframe' in embed_url:
                            print(f"🎯 Last resort: trying yt-dlp on embed URL: {embed_url}")
                            try:
                                return await self.download_video_with_ytdlp(embed_url, progress_msg, user_name)
                            except Exception as e:
                                print(f"⚠️ yt-dlp also failed: {e}")
                                
                                # Final fallback: provide the embed URL to user
                                if progress_msg:
                                    try:
                                        await progress_msg.edit_text(
                                            f"⚠️ نتوانستم ویدیو را مستقیماً دانلود کنم.\n\n"
                                            f"🔗 لینک پخش ویدیو:\n{embed_url}\n\n"
                                            f"💡 می‌توانید این لینک را در مرورگر باز کنید و ویدیو را مشاهده کنید."
                                        )
                                        return None, None, None  # Signal that we handled it with a message
                                    except:
                                        pass
                                break
                
                # Debug: Show some HTML content to understand the structure
                print("🔍 No video found. HTML sample:")
                print(html_content[:1000] + "..." if len(html_content) > 1000 else html_content)
                raise Exception("لینک ویدیو در صفحه پیدا نشد - ممکن است نیاز به روش دیگری باشد")
            
            # Make sure URL is absolute
            if video_url.startswith('//'):
                video_url = 'https:' + video_url
            elif video_url.startswith('/'):
                from urllib.parse import urljoin
                video_url = urljoin(url, video_url)
            
            print(f"📹 Final video URL: {video_url}")
            
            # Update progress message
            if progress_msg:
                try:
                    await progress_msg.edit_text("⏬ در حال دانلود ویدیو...")
                except:
                    pass
            
            # Now download the actual video file
            return await self.download_file(video_url, progress_msg, user_name)
            
        except Exception as e:
            error_msg = f"خطا در دانلود از qombol.com: {str(e)}"
            print(f"❌ {error_msg}")
            raise Exception(error_msg)
    
    async def download_file(self, url: str, progress_msg=None, user_name: str = "") -> tuple:
        """Download file from URL with progress tracking"""
        # Configure session with no size limits
        timeout = aiohttp.ClientTimeout(total=None, connect=30)
        connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
        
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}: نمی‌توان فایل را دانلود کرد")
                
                # Get filename and total size
                filename = self.get_filename_from_response(response, url)
                total_size = int(response.headers.get('content-length', 0))
                
                # Create temporary file
                temp_dir = tempfile.gettempdir()
                file_path = os.path.join(temp_dir, filename)
                
                # Download with progress tracking - no size limits
                downloaded = 0
                start_time = time.time()
                last_update = 0
                
                with open(file_path, 'wb') as file:
                    async for chunk in response.content.iter_chunked(1024 * 1024):  # 1MB chunks for large files
                        file.write(chunk)
                        downloaded += len(chunk)
                        
                        # Update progress every 2 seconds or if no total size
                        current_time = time.time()
                        if current_time - last_update >= 2 and progress_msg:
                            elapsed_time = current_time - start_time
                            speed = downloaded / elapsed_time if elapsed_time > 0 else 0
                            
                            if total_size > 0:
                                percentage = (downloaded / total_size) * 100
                                progress_text = self.create_progress_text(
                                    "📥 دانلود", percentage, speed, downloaded, total_size
                                )
                            else:
                                # Show progress without percentage for unknown size
                                progress_text = f"""📥 دانلود در حال انجام...

📊 دانلود شده: {self.format_file_size(downloaded)}
🚀 سرعت: {self.format_speed(speed)}

لطفاً صبر کنید..."""
                            
                            try:
                                await progress_msg.edit_text(progress_text)
                                last_update = current_time
                                print(f"📊 Download progress for {user_name}: {self.format_file_size(downloaded)} - {self.format_speed(speed)}")
                            except:
                                pass  # Ignore edit errors
                
                return file_path, filename, downloaded
    
    async def download_video_with_ytdlp(self, url: str, progress_msg=None, user_name: str = "") -> tuple:
        """Download video from video sites using yt-dlp"""
        temp_dir = tempfile.gettempdir()
        
        # Progress hook for yt-dlp
        last_update = 0
        def progress_hook(d):
            nonlocal last_update
            current_time = time.time()
            
            if d['status'] == 'downloading' and progress_msg and current_time - last_update >= 2:
                try:
                    # Extract progress info
                    downloaded = d.get('downloaded_bytes', 0)
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    speed = d.get('speed', 0) or 0
                    
                    if total > 0:
                        percentage = (downloaded / total) * 100
                        progress_text = self.create_progress_text(
                            "📹 دانلود ویدیو", percentage, speed, downloaded, total
                        )
                    else:
                        # Show progress without percentage for unknown size
                        progress_text = f"""📹 دانلود ویدیو در حال انجام...

📊 دانلود شده: {self.format_file_size(downloaded)}
🚀 سرعت: {self.format_speed(speed)}

لطفاً صبر کنید..."""
                    
                    # Run in event loop
                    loop = asyncio.get_event_loop()
                    loop.create_task(progress_msg.edit_text(progress_text))
                    last_update = current_time
                    print(f"📊 Video download progress for {user_name}: {self.format_file_size(downloaded)} - {self.format_speed(speed)}")
                except Exception as e:
                    pass  # Ignore progress update errors
        
        # Initialize cookies file path for cleanup
        cookies_file_path = None
        
        # yt-dlp options
        ydl_opts = {
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'format': 'best[height<=720]/best',  # Limit to 720p for faster download
            'noplaylist': True,
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            },
        }
        
        # Special handling for Rule34.xxx
        if 'rule34.xxx' in url.lower():
            ydl_opts.update({
                'extractor_args': {
                    'generic': {
                        'force_generic_extractor': True,
                    }
                },
                'format': 'best',  # Don't limit quality for Rule34
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Referer': 'https://rule34.xxx/',
                },
            })
        
        # Special handling for Redtube (broken extractor, needs cookies)
        elif 'redtube.com' in url.lower():
            # Create temporary cookies file for Redtube
            import tempfile
            cookies_content = """# Netscape HTTP Cookie File
.redtube.com	TRUE	/	FALSE	1999999999	age_verified	1
.redtube.com	TRUE	/	FALSE	1999999999	language	en
.redtube.com	TRUE	/	FALSE	1999999999	content_filter	off"""
            
            cookies_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            cookies_file.write(cookies_content)
            cookies_file.close()
            
            # Store cookies file path for cleanup
            cookies_file_path = cookies_file.name
            
            ydl_opts.update({
                'format': 'best[height<=1080]/best',
                'cookies': cookies_file_path,
                'extractor_args': {
                    'redtube': {
                        'age_limit': 18,
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Cache-Control': 'max-age=0',
                    'Referer': 'https://www.redtube.com/',
                },
            })
        
        # Special handling for other tube sites
        elif any(site in url.lower() for site in ['tube8.com', 'youporn.com', 'spankbang.com']):
            ydl_opts.update({
                'format': 'best[height<=1080]/best',  # Allow higher quality for these sites
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Cache-Control': 'max-age=0',
                },
            })
        
        
        try:
            # Run yt-dlp in executor to avoid blocking
            loop = asyncio.get_event_loop()
            
            def download_sync():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Extract info first
                    info = ydl.extract_info(url, download=False)
                    title = info.get('title', 'video')
                    
                    # Clean filename
                    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
                    if len(safe_title) > 100:
                        safe_title = safe_title[:100]
                    
                    # Update template with safe title
                    ydl_opts['outtmpl'] = os.path.join(temp_dir, f'{safe_title}.%(ext)s')
                    
                    # Download
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_download:
                        ydl_download.download([url])
                    
                    return safe_title, info.get('filesize', 0)
            
            # Execute download with timeout
            try:
                safe_title, estimated_size = await asyncio.wait_for(
                    loop.run_in_executor(None, download_sync), 
                    timeout=300  # 5 minutes timeout
                )
            except asyncio.TimeoutError:
                raise Exception("دانلود ویدیو بیش از حد طول کشید (5 دقیقه)")
            
            # Find the downloaded file
            downloaded_files = []
            for file in os.listdir(temp_dir):
                if safe_title in file and not file.endswith('.part'):
                    downloaded_files.append(file)
            
            if not downloaded_files:
                raise Exception("فایل دانلود شده پیدا نشد")
            
            # Get the most recent file
            downloaded_file = max(downloaded_files, key=lambda f: os.path.getctime(os.path.join(temp_dir, f)))
            file_path = os.path.join(temp_dir, downloaded_file)
            file_size = os.path.getsize(file_path)
            
            return file_path, downloaded_file, file_size
            
        except Exception as e:
            raise Exception(f"خطا در دانلود ویدیو: {str(e)}")
        finally:
            # Clean up cookies file if it was created for Redtube
            if cookies_file_path:
                try:
                    import os
                    os.unlink(cookies_file_path)
                except:
                    pass
    
    def get_filename_from_response(self, response, url: str) -> str:
        """Extract filename from response headers or URL"""
        # Try to get filename from Content-Disposition header
        content_disposition = response.headers.get('Content-Disposition')
        if content_disposition:
            import re
            filename_match = re.findall('filename="(.+)"', content_disposition)
            if filename_match:
                return filename_match[0]
        
        # Extract filename from URL
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        
        # If no filename found, use a default name
        if not filename or '.' not in filename:
            filename = "downloaded_file"
        
        return filename
    
    def format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format"""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB"]
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"
    
    def is_video_file(self, filename: str) -> bool:
        """Check if file is a video based on extension"""
        video_extensions = {
            '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', 
            '.m4v', '.3gp', '.ogv', '.ts', '.mts', '.m2ts'
        }
        return any(filename.lower().endswith(ext) for ext in video_extensions)
    
    def is_audio_file(self, filename: str) -> bool:
        """Check if file is audio based on extension"""
        audio_extensions = {
            '.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', 
            '.opus', '.aiff', '.au', '.ra'
        }
        return any(filename.lower().endswith(ext) for ext in audio_extensions)
    
    def is_photo_file(self, filename: str) -> bool:
        """Check if file is a photo based on extension"""
        photo_extensions = {
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', 
            '.tiff', '.tif', '.svg', '.ico'
        }
        return any(filename.lower().endswith(ext) for ext in photo_extensions)
    
    def get_video_info(self, file_path: str) -> dict:
        """Extract video information using ffprobe"""
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json', 
                '-show_format', '-show_streams', file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                
                # Find video stream
                video_stream = None
                for stream in data.get('streams', []):
                    if stream.get('codec_type') == 'video':
                        video_stream = stream
                        break
                
                if video_stream:
                    width = int(video_stream.get('width', 0))
                    height = int(video_stream.get('height', 0))
                    duration = float(video_stream.get('duration', 0))
                    
                    return {
                        'width': width,
                        'height': height,
                        'duration': int(duration) if duration > 0 else None
                    }
            
        except Exception as e:
            print(f"⚠️ Could not extract video info: {e}")
        
        # Return default values if extraction fails
        return {'width': None, 'height': None, 'duration': None}
    
    def create_progress_text(self, action: str, percentage: float, speed: float, current: int, total: int) -> str:
        """Create progress text with bar and stats"""
        # Create progress bar
        bar_length = 20
        filled_length = int(bar_length * percentage / 100)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        
        # Format text
        speed_text = self.format_speed(speed)
        current_size = self.format_file_size(current)
        total_size = self.format_file_size(total)
        
        return f"""{action} در حال انجام...

{bar} {percentage:.1f}%

📊 حجم: {current_size} / {total_size}
🚀 سرعت: {speed_text}

لطفاً صبر کنید..."""
    
    def format_speed(self, bytes_per_second: float) -> str:
        """Format speed in human readable format"""
        if bytes_per_second == 0:
            return "0 B/s"
        
        speed_names = ["B/s", "KB/s", "MB/s", "GB/s"]
        import math
        i = int(math.floor(math.log(bytes_per_second, 1024)))
        if i >= len(speed_names):
            i = len(speed_names) - 1
        p = math.pow(1024, i)
        s = round(bytes_per_second / p, 1)
        return f"{s} {speed_names[i]}"
    
    async def upload_with_progress(self, update, context, progress_msg, file_path: str, filename: str, file_size: int, user_name: str):
        """Upload file with progress tracking"""
        start_time = time.time()
        
        # Show initial upload message
        progress_text = self.create_progress_text("📤 آپلود", 0, 0, 0, file_size)
        await progress_msg.edit_text(progress_text)
        
        # If Local Bot API not configured and file > 50MB and bridge is configured, use user-account bridge
        bridge_configured = bool(TG_SESSION_STRING) and BRIDGE_CHANNEL_ID != 0 and upload_to_bridge is not None
        if not BOT_API_BASE_URL and file_size > 50 * 1024 * 1024 and bridge_configured:
            try:
                await progress_msg.edit_text("🚀 در حال ارسال از طریق حساب کاربری (بدون محدودیت 50MB)...")
            except:
                pass
            try:
                caption = f"✅ فایل آپلود شد (Bridge)\n📁 {filename}\n📊 {self.format_file_size(file_size)}"
                bridge_chat_id, message_id = await upload_to_bridge(file_path, filename, caption)
                await context.bot.copy_message(
                    chat_id=update.effective_chat.id,
                    from_chat_id=bridge_chat_id,
                    message_id=message_id
                )
                try:
                    await progress_msg.delete()
                except:
                    pass
                return
            except (BadRequest, Forbidden) as e:
                await update.message.reply_text(
                    "⚠️ دسترسی ربات به کانال Bridge مشکل دارد. ربات را ادمین کانال خصوصی قرار دهید و دوباره تلاش کنید."
                )
                raise e
            except Exception as e:
                await update.message.reply_text(
                    f"⚠️ ارسال از طریق Bridge با خطا مواجه شد: {e}\nتلاش برای ارسال مستقیم از طریق Bot API..."
                )
                # continue to direct upload fallback

        # Note: To avoid truncated uploads, we stream the real file handle via InputFile
        # and let HTTPX handle chunking. This prevents calling read(-1) on a wrapper.
        
        # Upload the file based on its type with fallback for large files
        caption = f"✅ فایل با موفقیت دانلود شد!\n📁 نام فایل: {filename}\n📊 حجم: {self.format_file_size(file_size)}"
        try:
            with open(file_path, 'rb') as file:
                media_file = InputFile(file, filename=filename, read_file_handle=False)
                if self.is_video_file(filename):
                    # Get video dimensions to maintain aspect ratio
                    video_info = self.get_video_info(file_path)
                    await update.message.reply_video(
                        video=media_file,
                        caption=caption,
                        supports_streaming=True,
                        width=video_info['width'],
                        height=video_info['height'],
                        duration=video_info['duration']
                    )
                elif self.is_audio_file(filename):
                    await update.message.reply_audio(
                        audio=media_file,
                        caption=caption
                    )
                elif self.is_photo_file(filename):
                    await update.message.reply_photo(
                        photo=media_file,
                        caption=caption
                    )
                else:
                    await update.message.reply_document(
                        document=media_file,
                        caption=caption
                    )
        except Exception as e:
            # If sending as media fails (413 error), fallback to document
            if "413" in str(e) or "Request Entity Too Large" in str(e):
                print(f"⚠️ Media upload failed due to size limit, falling back to document: {filename}")
                try:
                    with open(file_path, 'rb') as file:
                        await update.message.reply_document(
                            document=InputFile(file, filename=filename, read_file_handle=False),
                            caption=f"📄 فایل به صورت سند ارسال شد (حجم بزرگ)\n📁 نام فایل: {filename}\n📊 حجم: {self.format_file_size(file_size)}"
                        )
                except Exception as e2:
                    if "413" in str(e2) or "Request Entity Too Large" in str(e2):
                        if not BOT_API_BASE_URL:
                            await update.message.reply_text(
                                "⚠️ محدودیت 50MB در Bot API ابری. برای ارسال فایل‌های بزرگ (تا 2GB) باید Local Bot API Server را راه‌اندازی کنید و متغیرهای BOT_API_BASE_URL و BOT_API_BASE_FILE_URL را تنظیم کنید."
                            )
                        else:
                            await update.message.reply_text(
                                "⚠️ ارسال فایل در حالت Local Bot API هم ناموفق بود. لطفاً پیکربندی سرور Local Bot API را بررسی کنید."
                            )
                    else:
                        raise e2
            else:
                raise e
    


    async def delayed_file_cleanup(self, file_path: str, delay_seconds: int):
        """Delete file after specified delay"""
        try:
            await asyncio.sleep(delay_seconds)
            os.unlink(file_path)
            print(f"File deleted after {delay_seconds} seconds: {file_path}")
        except FileNotFoundError:
            # File already deleted, this is expected and not an error
            print(f"File already removed: {file_path}")
        except Exception as e:
            print(f"Error deleting file {file_path}: {str(e)}")
    
    def run(self):
        """Start the bot"""
        print("🤖 Bot started successfully!")
        print("📊 Bot is now online and waiting for requests...")
        print("=" * 50)
        self.app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    bot = TelegramDownloadBot()
    bot.run()
