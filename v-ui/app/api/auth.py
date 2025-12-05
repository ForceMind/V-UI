from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
async def login(creds: LoginRequest):
    # Mock authentication
    if creds.username == "admin" and creds.password == "admin":
        return {"token": "mock-jwt-token-xyz", "expires_in": 3600}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@router.get("/me")
async def get_current_user():
    return {"username": "admin", "role": "superuser"}
