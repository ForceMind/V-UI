from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.middleware.auth import configured_origin, cookie_name
from app.services import auth_service

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    current_password: SecretStr = Field(min_length=1, max_length=128)
    password: SecretStr | None = Field(default=None, min_length=15, max_length=128)


def clear_cookie(response: Response) -> None:
    response.delete_cookie(cookie_name(), path="/", httponly=True,
                           secure=configured_origin().startswith("https://"), samesite="strict")


@router.post("/login")
def login(creds: LoginRequest, request: Request, response: Response):
    user, token = auth_service.login(
        creds.username, creds.password.get_secret_value(),
        request.client.host if request.client else "unknown",
        request.cookies.get(cookie_name(), ""))
    response.set_cookie(cookie_name(), token, max_age=auth_service.SESSION_SECONDS,
                        path="/", httponly=True,
                        secure=configured_origin().startswith("https://"), samesite="strict")
    return {"user": user, "expires_in": auth_service.SESSION_SECONDS}


@router.get("/me")
def me(request: Request):
    return request.state.admin


@router.post("/logout")
def logout(request: Request, response: Response):
    auth_service.logout(request.cookies.get(cookie_name(), ""))
    clear_cookie(response)
    return {"message": "Signed out"}


@router.post("/update")
@router.post("/update_profile")
def update_profile(profile: UpdateProfileRequest, request: Request, response: Response):
    auth_service.change_profile(request.state.admin["id"], profile.username,
                                profile.current_password.get_secret_value(),
                                profile.password.get_secret_value() if profile.password else None)
    clear_cookie(response)
    return {"message": "Profile changed; all sessions revoked. Sign in again."}
