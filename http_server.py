# http_server.py
from aiohttp import web
import asyncio
import os

async def health_check(request):
    return web.Response(text="OK")

async def start_http_server():
    port = int(os.getenv("PORT", 10000))
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"HTTP server running on port {port}")
    while True:
        await asyncio.sleep(3600)
