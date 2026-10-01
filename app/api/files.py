"""Disabled legacy site API. Importing this module never touches the filesystem."""
from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter()


@router.post("/upload_site")
async def upload_site(file: UploadFile = File(...)):
    # Keep the route contract while site hosting remains deliberately isolated.
    # Do not create wwwroot, unpack user archives, or alter existing site files.
    raise HTTPException(503, "Site hosting is disabled until it has an isolated origin")


@router.get("/list_files")
def list_files():
    raise HTTPException(503, "Site hosting is disabled until it has an isolated origin")
