import asyncio
import aiohttp
import base64
import urllib.parse
from typing import Optional, Dict, Any

class RedditAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str = "http://localhost:8080/"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = None
        self.refresh_token = None
        
    def get_auth_url(self, state: str = "random_state") -> str:
        """Generate Reddit OAuth authorization URL"""
        params = {
            'client_id': self.client_id,
            'response_type': 'code',
            'state': state,
            'redirect_uri': self.redirect_uri,
            'duration': 'permanent',
            'scope': 'read'
        }
        
        base_url = "https://www.reddit.com/api/v1/authorize"
        return f"{base_url}?{urllib.parse.urlencode(params)}"
    
    async def exchange_code_for_token(self, code: str) -> bool:
        """Exchange authorization code for access token"""
        try:
            # Clean the code - remove any URL parameters if present
            if '?' in code:
                code = code.split('?')[0]
            if '&' in code:
                code = code.split('&')[0]
            
            # Prepare credentials
            credentials = f"{self.client_id}:{self.client_secret}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded_credentials}',
                'User-Agent': 'TelegramDownloadBot/1.0',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            data = {
                'grant_type': 'authorization_code',
                'code': code.strip(),
                'redirect_uri': self.redirect_uri
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://www.reddit.com/api/v1/access_token',
                    headers=headers,
                    data=data
                ) as response:
                    if response.status == 200:
                        token_data = await response.json()
                        self.access_token = token_data.get('access_token')
                        self.refresh_token = token_data.get('refresh_token')
                        return True
                    else:
                        print(f"❌ Token exchange failed: {response.status}")
                        response_text = await response.text()
                        print(f"❌ Response: {response_text}")
                        return False
                        
        except Exception as e:
            print(f"❌ Error exchanging code for token: {e}")
            return False
    
    async def get_post_data(self, post_url: str) -> Optional[Dict[Any, Any]]:
        """Get Reddit post data using API"""
        if not self.access_token:
            return None
            
        try:
            # Extract post ID from URL
            post_id = self._extract_post_id(post_url)
            if not post_id:
                return None
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'User-Agent': 'TelegramDownloadBot/1.0'
            }
            
            # Get post data from Reddit API
            api_url = f"https://oauth.reddit.com/comments/{post_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data[0]['data']['children'][0]['data'] if data else None
                    else:
                        print(f"❌ Reddit API request failed: {response.status}")
                        return None
                        
        except Exception as e:
            print(f"❌ Error getting Reddit post data: {e}")
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
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            return False
            
        try:
            credentials = f"{self.client_id}:{self.client_secret}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded_credentials}',
                'User-Agent': 'TelegramDownloadBot/1.0',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            data = {
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://www.reddit.com/api/v1/access_token',
                    headers=headers,
                    data=data
                ) as response:
                    if response.status == 200:
                        token_data = await response.json()
                        self.access_token = token_data.get('access_token')
                        return True
                    else:
                        return False
                        
        except Exception as e:
            print(f"❌ Error refreshing token: {e}")
            return False
