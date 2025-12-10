from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil
import os
import zipfile

router = APIRouter()

WWW_ROOT = "wwwroot"

if not os.path.exists(WWW_ROOT):
    os.makedirs(WWW_ROOT)
    # Create a default index.html
    with open(os.path.join(WWW_ROOT, "index.html"), "w") as f:
        f.write("<h1>Welcome to V-UI Server</h1>")

@router.post("/upload_site")
async def upload_site(file: UploadFile = File(...)):
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only zip files are allowed")
    
    file_location = f"temp_{file.filename}"
    with open(file_location, "wb+") as file_object:
        shutil.copyfileobj(file.file, file_object)
    
    # Clear existing wwwroot
    for filename in os.listdir(WWW_ROOT):
        file_path = os.path.join(WWW_ROOT, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Failed to delete {file_path}. Reason: {e}')

    # Extract zip
    try:
        with zipfile.ZipFile(file_location, 'r') as zip_ref:
            zip_ref.extractall(WWW_ROOT)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    finally:
        os.remove(file_location)
    
    return {"message": "Site deployed successfully"}

@router.get("/list_files")
def list_files():
    files = []
    for root, dirs, filenames in os.walk(WWW_ROOT):
        for filename in filenames:
            files.append(os.path.relpath(os.path.join(root, filename), WWW_ROOT))
    return files
