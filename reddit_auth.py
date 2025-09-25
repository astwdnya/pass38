import asyncio
import aiohttp
import base64
import urllib.parse
from typing import Optional, Dict, Any
import praw

class RedditAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "http://localhost:8080",
        username: str | None = None,
        password: str | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        # Normalize redirect URI (trim spaces, remove trailing slash)
        self.redirect_uri = (redirect_uri or "").strip()
        if self.redirect_uri.endswith('/'):
            self.redirect_uri = self.redirect_uri[:-1]
        self.access_token = None
        self.refresh_token = None
        self.username = username
        self.password = password
        self.reddit = None
        # Script mode if username/password present
        self.is_script_mode = bool(self.username and self.password)

        if self.is_script_mode:
            try:
                self.reddit = praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    user_agent="TelegramDownloadBot/1.0 (PRAW script mode)",
                    username=self.username,
                    password=self.password,
                )
                # Simple validation call
                _ = self.reddit.user.me()
                # Mark as authorized
                self.access_token = "praw_authorized"
                print("✅ PRAW script-mode authenticated successfully (username/password)")
            except Exception as e:
                print(f"❌ Failed to initialize PRAW script-mode: {e}")
        
    def get_auth_url(self, state: str = "random_state", duration: str = "permanent") -> str:
        """Generate Reddit OAuth authorization URL using PRAW.
        If running in script mode (username/password), no auth URL is required.
        """
        if self.is_script_mode:
            return ""
        try:
            # Initialize PRAW client for auth URL generation
            self.reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                user_agent="TelegramDownloadBot/1.0 (OAuth via PRAW)"
            )
            scopes = ["identity", "read"]
            return self.reddit.auth.url(scopes=scopes, state=state, duration=duration)
        except Exception:
            # Fallback to manual URL building if PRAW URL generation fails for any reason
            params = {
                'client_id': self.client_id,
                'response_type': 'code',
                'state': state,
                'redirect_uri': self.redirect_uri,
                'duration': duration,
                'scope': 'identity read'
            }
            base_url = "https://www.reddit.com/api/v1/authorize"
            return f"{base_url}?{urllib.parse.urlencode(params)}"
    
    async def exchange_code_for_token(self, code: str) -> bool:
        """Exchange authorization code for tokens using PRAW.
        In script mode, this is not necessary and returns True.
        """
        if self.is_script_mode:
            # Already authenticated via username/password
            return bool(self.reddit)
        try:
            # Accept both raw code and full redirect URL pasted by the user
            raw_input = (code or "").strip()
            if raw_input.startswith("http://") or raw_input.startswith("https://"):
                try:
                    parsed = urllib.parse.urlparse(raw_input)
                    qs = urllib.parse.parse_qs(parsed.query)
                    url_code = qs.get("code", [""])[0]
                    if url_code:
                        code = url_code
                except Exception:
                    pass
            elif "code=" in raw_input:
                try:
                    pseudo_qs = urllib.parse.parse_qs(raw_input.replace("?", "&"))
                    qs_code = pseudo_qs.get("code", [""])[0]
                    if qs_code:
                        code = qs_code
                except Exception:
                    pass
            # Clean simple artifacts
            if '?' in code:
                code = code.split('?')[0]
            if '&' in code:
                code = code.split('&')[0]

            # Ensure PRAW client exists
            if not hasattr(self, 'reddit') or self.reddit is None:
                self.reddit = praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    redirect_uri=self.redirect_uri,
                    user_agent="TelegramDownloadBot/1.0 (OAuth via PRAW)"
                )

            refresh_token = self.reddit.auth.authorize(code.strip())
            # If duration is temporary, refresh_token can be None
            self.refresh_token = refresh_token
            # Mark as authorized for our bot logic
            self.access_token = "praw_authorized"
            return True

        except Exception as e:
            print(f"❌ Error exchanging code via PRAW: {e}")
            return False
    
    async def get_post_data(self, post_url: str) -> Optional[Dict[Any, Any]]:
        """Get Reddit post data using PRAW"""
        if not self.is_script_mode and not self.access_token and not self.refresh_token:
            return None
        try:
            # Ensure we have an authenticated PRAW instance (use refresh_token if available)
            if self.is_script_mode and self.reddit:
                pass  # already initialized
            elif self.refresh_token:
                self.reddit = praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    refresh_token=self.refresh_token,
                    user_agent="TelegramDownloadBot/1.0 (OAuth via PRAW)"
                )
            elif not hasattr(self, 'reddit') or self.reddit is None:
                self.reddit = praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    redirect_uri=self.redirect_uri,
                    user_agent="TelegramDownloadBot/1.0 (OAuth via PRAW)"
                )

            submission = self.reddit.submission(url=post_url)
            # Build a response compatible with previous code expectations
            data: Dict[str, Any] = {
                'is_video': bool(getattr(submission, 'is_video', False)),
                'media': getattr(submission, 'media', None) or {},
                'secure_media': getattr(submission, 'secure_media', None) or {},
                'id': submission.id,
                'permalink': submission.permalink,
                'title': submission.title,
            }
            return data
        except Exception as e:
            print(f"❌ Error getting Reddit post via PRAW: {e}")
            return None
    
    def _extract_post_id(self, url: str) -> Optional[str]:
        """Extract post ID from Reddit URL"""
        try:
            # Handle different Reddit URL formats
            if '/comments/' in url:
                parts = url.split('/comments/')
                if len(parts) > 1:
                    post_id = parts[1].split('/')[0]
                    return post_id
            return None
        except:
            return None
    
    async def refresh_access_token(self) -> bool:
        """Recreate PRAW client using stored refresh token"""
        if not self.refresh_token:
            return False
        try:
            self.reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                refresh_token=self.refresh_token,
                user_agent="TelegramDownloadBot/1.0 (OAuth via PRAW)"
            )
            self.access_token = "praw_authorized"
            return True
        except Exception as e:
            print(f"❌ Error refreshing PRAW client: {e}")
            return False
