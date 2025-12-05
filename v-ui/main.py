from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import system, xray, auth, security

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

from fastapi.staticfiles import StaticFiles
import os
os.makedirs("web", exist_ok=True)
app.mount("/ui", StaticFiles(directory="web", html=True), name="ui")

@app.get("/")
async def root():
    return {"message": "Welcome to V-UI Panel", "status": "running", "dashboard": "/ui/"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=2053, reload=True)
