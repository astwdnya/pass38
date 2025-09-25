#!/usr/bin/env python3
"""
Simple HTTP health check server for UptimeBot monitoring
Runs alongside the Telegram bot
"""

import threading
import time
from flask import Flask, jsonify
from datetime import datetime

class HealthServer:
    def __init__(self, port=8080):
        self.app = Flask(__name__)
        self.port = port
        self.start_time = datetime.now()
        self.bot_status = "starting"
        self.setup_routes()
    
    def setup_routes(self):
        @self.app.route('/')
        def health_check():
            uptime = datetime.now() - self.start_time
            return jsonify({
                "status": "healthy",
                "bot_status": self.bot_status,
                "uptime_seconds": int(uptime.total_seconds()),
                "uptime": str(uptime).split('.')[0],
                "timestamp": datetime.now().isoformat(),
                "message": "Telegram Download Bot is running"
            })
        
        @self.app.route('/health')
        def health():
            return jsonify({"status": "ok", "bot_status": self.bot_status})
        
        @self.app.route('/ping')
        def ping():
            return "pong"
    
    def update_bot_status(self, status):
        """Update bot status for health checks"""
        self.bot_status = status
    
    def start(self):
        """Start the health server in a separate thread"""
        def run_server():
            # Bind to all interfaces for external accessibility (UptimeRobot monitoring)
            self.app.run(host='0.0.0.0', port=self.port, debug=False, use_reloader=False)
        
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        print(f"🌐 Health server started on port {self.port} (accessible externally)")
        return server_thread
