"""Local dashboard shell with optional anonymous loopback reads; writes require a key."""
from importlib.resources import files
from fastapi.responses import Response, JSONResponse

PUBLIC_UI_PATHS=frozenset({'/','/ui/app.js','/ui/app.css','/ui/explorer.js','/ui/research_views.js','/ui/config'})
SECURITY={
    'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Cache-Control':'no-store',
}


def public_dashboard_read(request, enabled):
    """Opt-in reads for loopback hosts; cross-site browser requests stay authenticated."""
    if not enabled or request.url.hostname not in {'localhost', '127.0.0.1', '::1'}:
        return False
    if request.headers.get('sec-fetch-site') == 'cross-site':
        return False
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
        return False
    return True


def install_dashboard(app, settings):
    from fastapi import Request
    @app.get('/ui/config', include_in_schema=False)
    async def config(request: Request):
        return JSONResponse({'public_read': public_dashboard_read(request, settings.dashboard_public_read)}, headers=SECURITY)
    def asset(name,media):
        return Response(files('midas_data').joinpath('ui',name).read_bytes(),media_type=media,headers=SECURITY)
    @app.get('/',include_in_schema=False)
    async def index():return asset('index.html','text/html')
    @app.get('/ui/app.js',include_in_schema=False)
    async def javascript():return asset('app.js','text/javascript')
    @app.get('/ui/app.css',include_in_schema=False)
    async def stylesheet():return asset('app.css','text/css')

    @app.get('/ui/explorer.js',include_in_schema=False)
    async def explorer():return asset('explorer.js','text/javascript')
    @app.get('/ui/research_views.js',include_in_schema=False)
    async def research_views():return asset('research_views.js','text/javascript')
