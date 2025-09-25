#!/usr/bin/env python3
"""
Main entry point for Telegram Download Bot
Optimized for Render deployment with health check server
"""

import os
import sys
import logging
from pathlib import Path
# Add the tgscmr directory to Python path
current_dir = Path(__file__).parent
tgscmr_dir = current_dir / "tgscmr"
sys.path.insert(0, str(tgscmr_dir))

from telegram.ext import Application
from tgscmr.config import BOT_TOKEN

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Main function to start the bot with health server"""
    try:
        logger.info("Starting Telegram Download Bot with Health Server...")
        
        # Start health check server (bind to HEALTH_PORT, not Render PORT)
        from health_server import HealthServer
        health_port = int(os.environ.get('HEALTH_PORT', 10000))
        health_server = HealthServer(port=health_port)
        health_server.start()
        health_server.update_bot_status("initializing")
        
        # Create and start the application with better timeout settings
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .read_timeout(30)
            .write_timeout(30)
            .connect_timeout(30)
            .pool_timeout(30)
            .get_updates_read_timeout(30)
            .build()
        )
        
        # Create bot instance
        from tgscmr.bot import TelegramDownloadBot
        bot = TelegramDownloadBot()
        logger.info("Bot instance created successfully")
        health_server.update_bot_status("created")
        
        # Start the bot
        logger.info("Starting bot polling...")
        health_server.update_bot_status("running")
        
        # Use the simplified run method
        bot.run()
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.error("Make sure all dependencies are installed")
        sys.exit(1)
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
