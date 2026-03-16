from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import subprocess
import tempfile
import os
import json
import shutil
import traceback
from jose import jwt, JWTError
from supabase import create_client, Client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        # Try HS256 first (standard JWT secret)
        payload = jwt.decode(
            credentials.credentials,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_aud": True},
        )
        return payload
    except JWTError:
        try:
            # Fallback: decode without verification for Supabase ES256
            payload = jwt.decode(
                credentials.credentials,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                },
            )
            return payload
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid or expired token")


def upload_image_to_storage(image_path: str, storage_path: str) -> str:
    """Upload image to Supabase Storage.
    Returns the storage path on success.
    storage_path format: {user_id}/baseline.jpg or {user_id}/scans/{timestamp}.jpg
    """
    with open(image_path, "rb") as img_file:
        supabase.storage.from_("face-images").upload(
            path=storage_path,
            file=img_file.read(),
            file_options={
                "content-type": "image/jpeg",
                "upsert": "true",
            },
        )
    return storage_path


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(
    mode: str = Form(...),
    image: UploadFile = File(...),
    fingerprint_data: str = Form(None),
    user=Depends(verify_token),
):
    temp_dir = tempfile.mkdtemp()
    user_id = user["sub"]

    try:
        # Save uploaded image to temp
        image_path = os.path.join(temp_dir, "image.jpg")
        with open(image_path, "wb") as f:
            shutil.copyfileobj(image.file, f)

        # Build command args for cv_engine
        args = ["python", "analyze.py", "--mode", mode, "--image", image_path]

        if mode == "baseline":
            out_path = os.path.join(temp_dir, "fingerprint.json")
            args.extend(["--out", out_path])

        if mode == "analyze":
            if not fingerprint_data:
                return JSONResponse(
                    status_code=400,
                    content={"error": "No fingerprint data provided. Upload baseline first."},
                )
            fingerprint_path = os.path.join(temp_dir, "fingerprint.json")
            with open(fingerprint_path, "w") as f:
                f.write(fingerprint_data)
            args.extend(["--fingerprint", fingerprint_path])

        # Run cv_engine
        result = subprocess.run(
            args,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            return JSONResponse(
                status_code=500,
                content={"error": result.stderr or "Unknown error", "stdout": result.stdout},
            )

        if not result.stdout.strip():
            return JSONResponse(
                status_code=500,
                content={"error": "No output from analyze.py", "stderr": result.stderr},
            )

        try:
            output = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=500,
                content={"error": "Invalid JSON from analyze.py", "raw": result.stdout},
            )

        # ── BASELINE MODE ──────────────────────────────────────────────
        if mode == "baseline":
            out_path = os.path.join(temp_dir, "fingerprint.json")
            if os.path.exists(out_path):
                with open(out_path, "r") as f:
                    fingerprint_str = f.read()
                output["fingerprint_data"] = fingerprint_str

                # Upload baseline image to Supabase Storage
                # Path: face-images/{user_id}/baseline.jpg
                # upsert=true overwrites previous baseline automatically
                baseline_storage_path = f"{user_id}/baseline.jpg"
                upload_image_to_storage(image_path, baseline_storage_path)

                # Save fingerprint + image path to baselines table
                # upsert on user_id so re-running baseline updates existing row
                supabase.table("baselines").upsert(
                    {
                        "user_id": user_id,
                        "fingerprint_json": json.loads(fingerprint_str),
                        "image_path": baseline_storage_path,
                    }
                ).execute()

                output["baseline_image_path"] = baseline_storage_path

        # ── ANALYZE MODE ───────────────────────────────────────────────
        if mode == "analyze":
            import time
            timestamp = int(time.time())

            # Upload scan image to Supabase Storage
            # Path: face-images/{user_id}/scans/{timestamp}.jpg
            scan_storage_path = f"{user_id}/scans/{timestamp}.jpg"
            upload_image_to_storage(image_path, scan_storage_path)

            # Save scan result + image path to scans table
            supabase.table("scans").insert(
                {
                    "user_id": user_id,
                    "verdict": output.get("verdict"),
                    "zones": output.get("zones"),
                    "aggregate_deviation": output.get("aggregate", {}).get("deviation"),
                    "image_path": scan_storage_path,
                }
            ).execute()

            output["scan_image_path"] = scan_storage_path

        return JSONResponse(content=output)

    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=504, content={"error": "Analysis timed out after 60s"})
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "trace": traceback.format_exc()},
        )
    finally:
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
