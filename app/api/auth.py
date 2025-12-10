from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

# Simple in-memory user store (Replace with DB in production)
# Default: admin / admin
current_user = {
    "username": "admin",
    "password": "admin"
}

class LoginRequest(BaseModel):
    username: str
    password: str

class UpdateProfileRequest(BaseModel):
    username: str
    password: Optional[str] = None

@router.post("/login")
async def login(creds: LoginRequest):
    if creds.username == current_user["username"] and creds.password == current_user["password"]:
        return {"token": "mock-jwt-token-xyz", "expires_in": 3600}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@router.get("/me")
async def get_current_user():
    return {"username": current_user["username"], "role": "superuser"}

@router.post("/update")
async def update_profile(profile: UpdateProfileRequest):
    global current_user
    current_user["username"] = profile.username
    if profile.password:
        current_user["password"] = profile.password
    return {"message": "Profile updated successfully"}
