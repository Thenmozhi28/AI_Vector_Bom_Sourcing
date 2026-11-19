"""
Authentication Router - MySQL Based
Login, Registration, Admin Approval
All endpoints require authentication
"""
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from typing import Optional, Dict
from datetime import datetime, timedelta
import secrets
import os
from dotenv import load_dotenv
from auth_db import (
    create_user, authenticate_user, get_user, get_user_by_id,
    approve_user, reject_user, get_pending_users, get_all_users,
    make_admin, create_session, get_session, invalidate_session,
    log_activity, get_system_stats,
    admin_create_user, admin_update_user, admin_delete_user
)

# Load environment
load_dotenv()

router = APIRouter()

# Constants from environment
SESSION_TIMEOUT_DAYS = int(os.getenv('SESSION_TIMEOUT_DAYS', '7'))

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = ""

class LoginRequest(BaseModel):
    username: str
    password: str

class ApproveRequest(BaseModel):
    username: str
    action: str  # "approve" or "reject"

# ═════════════════════════════════════════════════════════════════════════════
#  AUTHENTICATION HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def verify_auth(token: str = Query(...)) -> Dict:
    """Dependency: Verify authentication token
    
    Raises HTTPException if not authenticated
    """
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    
    session = get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    return session

def verify_admin_auth(token: str = Query(...)) -> Dict:
    """Dependency: Verify admin authentication token"""
    session = verify_auth(token)
    
    if not session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return session

def verify_system_admin_auth(token: str = Query(...)) -> Dict:
    """Dependency: Verify system admin (role == 'admin') authentication token"""
    session = verify_auth(token)
    
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="System Admin access required for user management")
    
    return session

# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC AUTH ENDPOINTS (No login required)
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/register", tags=["Authentication"])
async def register(request: RegisterRequest):
    """
    Register a new user
    
    Requires admin approval before login is allowed
    """
    result = create_user(
        username=request.username,
        email=request.email,
        password=request.password,
        full_name=request.full_name
    )
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    
    return {
        "status": "success",
        "message": result.get("message"),
        "user": result.get("user")
    }

@router.post("/login", tags=["Authentication"])
async def login(request: LoginRequest, req: Request):
    """
    Login with username and password
    
    Returns a session token if credentials are valid and user is approved
    """
    user = authenticate_user(request.username, request.password)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    if isinstance(user, dict) and "error" in user:
        raise HTTPException(status_code=403, detail=user["error"])
    
    # Generate token
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=SESSION_TIMEOUT_DAYS)
    
    # Store session in database
    create_session(
        user_id=user["id"],
        token=token,
        expires_at=expires_at,
        ip_address=req.client.host if req.client else "",
        user_agent=req.headers.get("user-agent", "")
    )
    
    # Log activity
    log_activity(user["id"], "LOGIN", "auth", "success")
    
    return {
        "status": "success",
        "message": f"Welcome {user['username']}!",
        "token": token,
        "expires_in_days": SESSION_TIMEOUT_DAYS,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user.get("full_name"),
            "role": user.get("role", "viewer"),
            "is_admin": user.get("is_admin", False)
        }
    }

@router.post("/logout", tags=["Authentication"])
async def logout(token: str = Query(...)):
    """
    Logout user (invalidate token)
    """
    session = get_session(token)
    if not session:
        raise HTTPException(status_code=400, detail="Invalid token")
    
    invalidate_session(token)
    log_activity(session["user_id"], "LOGOUT", "auth", "success")
    
    return {"status": "success", "message": "Logged out successfully"}

@router.get("/verify-token", tags=["Authentication"])
async def verify_token(token: str = Query(...)):
    """
    Verify if token is valid
    """
    session = get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    return {
        "status": "valid",
        "user_id": session["user_id"],
        "username": session["username"],
        "email": session["email"],
        "role": session.get("role", "viewer"),
        "is_admin": session.get("is_admin", False),
        "expires_at": session["expires_at"]
    }

@router.get("/auth-status", tags=["Authentication"])
async def auth_status():
    """
    Get authentication system status (no login required)
    """
    stats = get_system_stats()
    
    return {
        "status": "ok",
        "system": stats
    }

# ═════════════════════════════════════════════════════════════════════════════
#  ADMIN ENDPOINTS (Requires admin authentication)
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/admin/pending-users", tags=["Admin"])
async def get_pending(auth: Dict = Depends(verify_admin_auth)):
    """
    Get all users waiting for approval
    
    Requires: admin token via ?token=your_token
    """
    pending = get_pending_users()
    
    log_activity(auth["user_id"], "VIEW_PENDING_USERS", "admin", "success")
    
    return {
        "status": "success",
        "pending_count": len(pending),
        "users": [
            {
                "id": u["id"],
                "username": u["username"],
                "email": u["email"],
                "full_name": u["full_name"],
                "created_at": str(u["created_at"])
            }
            for u in pending
        ]
    }

@router.post("/admin/approve-user", tags=["Admin"])
async def admin_approve(
    request: ApproveRequest,
    auth: Dict = Depends(verify_admin_auth)
):
    """
    Approve or reject a user registration
    
    Body params:
    - username: User to approve/reject
    - action: "approve" or "reject"
    """
    if request.action == "approve":
        result = approve_user(request.username)
        if result.get("success"):
            log_activity(auth["user_id"], "APPROVE_USER", request.username, "success")
            return {
                "status": "success",
                "action": "approved",
                "username": request.username,
                "message": f"User {request.username} can now login"
            }
    elif request.action == "reject":
        result = reject_user(request.username)
        if result.get("success"):
            log_activity(auth["user_id"], "REJECT_USER", request.username, "success")
            return {
                "status": "success",
                "action": "rejected",
                "username": request.username,
                "message": f"User {request.username} registration rejected"
            }
    
    raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))

@router.get("/admin/all-users", tags=["Admin"])
async def admin_get_all(auth: Dict = Depends(verify_system_admin_auth)):
    """
    Get all users list
    
    Requires: system admin token
    """
    users = get_all_users()
    
    log_activity(auth["user_id"], "VIEW_ALL_USERS", "admin", "success")
    
    return {
        "status": "success",
        "total_users": len(users),
        "users": users
    }

@router.post("/admin/make-admin", tags=["Admin"])
async def admin_make_admin(
    username: str = Query(...),
    auth: Dict = Depends(verify_admin_auth)
):
    """
    Make a user admin
    
    Query params:
    - username: User to make admin
    """
    result = make_admin(username)
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    
    log_activity(auth["user_id"], "MAKE_ADMIN", username, "success")
    
    return {"status": "success", "message": result.get("message")}

@router.get("/admin/system-stats", tags=["Admin"])
async def admin_system_stats(auth: Dict = Depends(verify_admin_auth)):
    """
    Get system statistics
    
    Requires: admin token
    """
    stats = get_system_stats()
    
    log_activity(auth["user_id"], "VIEW_STATS", "admin", "success")
    
    return {
        "status": "success",
        "statistics": stats
    }

# ═════════════════════════════════════════════════════════════════════════════
#  SYSTEM ADMIN ONLY: USER CRUD MODULE (Restricted from Client Admin)
# ═════════════════════════════════════════════════════════════════════════════

class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = ""
    role: str = "viewer"
    valid_until: str = "2036-01-01 00:00:00"
    is_approved: bool = True
    is_admin: bool = False

class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    valid_until: Optional[str] = None
    is_approved: Optional[bool] = None
    is_admin: Optional[bool] = None

@router.post("/admin/create-user", tags=["User Management"])
async def create_new_user(
    request: CreateUserRequest,
    auth: Dict = Depends(verify_system_admin_auth)
):
    """
    System Admin Only: Create a new user with full roles and licensing controls
    """
    result = admin_create_user(
        username=request.username,
        email=request.email,
        password=request.password,
        full_name=request.full_name,
        role=request.role,
        valid_until=request.valid_until,
        is_approved=request.is_approved,
        is_admin=request.is_admin
    )
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
        
    log_activity(auth["user_id"], "CREATE_USER", request.username, "success")
    return {"status": "success", "message": result.get("message"), "user_id": result.get("user_id")}

@router.put("/admin/update-user/{user_id}", tags=["User Management"])
async def update_existing_user(
    user_id: int,
    request: UpdateUserRequest,
    auth: Dict = Depends(verify_system_admin_auth)
):
    """
    System Admin Only: Update any user detail (password, role, valid_until, etc.)
    """
    updates = request.dict(exclude_unset=True)
    result = admin_update_user(user_id, updates)
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
        
    log_activity(auth["user_id"], "UPDATE_USER", f"user_id:{user_id}", "success")
    return {"status": "success", "message": result.get("message")}

@router.delete("/admin/delete-user/{user_id}", tags=["User Management"])
async def delete_existing_user(
    user_id: int,
    auth: Dict = Depends(verify_system_admin_auth)
):
    """
    System Admin Only: Delete a user and terminate active sessions
    """
    # Prevent self-deletion
    if user_id == auth["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot self-delete logged in admin session")
        
    result = admin_delete_user(user_id)
    if not result.get("success"):
         raise HTTPException(status_code=400, detail=result.get("error"))
         
    log_activity(auth["user_id"], "DELETE_USER", f"user_id:{user_id}", "success")
    return {"status": "success", "message": result.get("message")}

