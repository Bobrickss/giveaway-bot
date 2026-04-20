from aiohttp import web
import asyncio

async def serve_file(request):
    with open("captcha.html", "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")

app = web.Application()
app.router.add_get("/captcha.html", serve_file)
app.router.add_get("/", lambda r: web.Response(text="OK"))

web.run_app(app, host="0.0.0.0", port=8080)
