from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import system, xray, auth, security, files

app = FastAPI(
    title="V-UI",
    description="A powerful server management and VPN panel",
    version="0.1.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(xray.router, prefix="/api/xray", tags=["Xray"])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(security.router, prefix="/api/security", tags=["Security"])
app.include_router(files.router, prefix="/api/files", tags=["Files"])

from fastapi.staticfiles import StaticFiles
import os
import sys

def get_web_path():
    """
    Get the path to the web directory.
    If frozen (PyInstaller), it's in the temp folder (sys._MEIPASS).
    Otherwise, it's in the current directory.
    """
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    web_path = os.path.join(base_path, "web")
    if not os.path.exists(web_path):
        os.makedirs(web_path, exist_ok=True)
        # Create a default index.html if missing to avoid 404/500
        with open(os.path.join(web_path, "index.html"), "w") as f:
            f.write("<h1>V-UI Panel is running</h1><p>Please upload frontend files to the web directory.</p>")
    return web_path

# Mount Panel UI
app.mount("/ui", StaticFiles(directory=get_web_path(), html=True), name="ui")

# Mount Static Site (Camouflage)
www_root = "wwwroot"
if not os.path.exists(www_root):
    os.makedirs(www_root)
    with open(os.path.join(www_root, "index.html"), "w") as f:
        f.write("<h1>Welcome</h1>")

app.mount("/", StaticFiles(directory=www_root, html=True), name="site")


@app.get("/")
async def root():
    return {"message": "Welcome to V-UI Panel", "status": "running", "dashboard": "/ui/"}

if __name__ == "__main__":
    import uvicorn
    # Disable reload in production for stability
    # Use app object directly for PyInstaller compatibility
    uvicorn.run(app, host="0.0.0.0", port=2053)
