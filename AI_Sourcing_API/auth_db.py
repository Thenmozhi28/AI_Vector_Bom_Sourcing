"""
MySQL User Database Management
Plain text passwords stored in database
"""
from datetime import datetime
from typing import Optional, List, Dict
from database import execute_query

def create_user(username: str, email: str, password: str, full_name: str = "") -> Dict:
    """Create a new user (requires admin approval)"""
    
    # Check if user already exists
    existing = execute_query(
        "SELECT id FROM users WHERE username = %s OR email = %s",
        (username, email),
        fetch=True
    )
    
    if existing:
        if existing[0]:
            return {"success": False, "error": "Username or email already exists"}
    
    # Insert new user
    result = execute_query(
        """INSERT INTO users (username, email, password, full_name, is_approved, is_admin)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (username, email, password, full_name, False, False)
    )
    
    if result is None:
        return {"success": False, "error": "Failed to create user"}
    
    return {
        "success": True,
        "message": "User registered successfully. Awaiting admin approval.",
        "user": {
            "username": username,
            "email": email,
            "is_approved": False
        }
    }

def get_user(username: str) -> Optional[Dict]:
    """Get user by username"""
    result = execute_query(
        "SELECT * FROM users WHERE username = %s",
        (username,),
        fetch=True
    )
    
    if result:
        return result[0]
    return None

def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Get user by ID"""
    result = execute_query(
        "SELECT * FROM users WHERE id = %s",
        (user_id,),
        fetch=True
    )
    
    if result:
        return result[0]
    return None

def authenticate_user(username: str, password: str) -> Optional[Dict]:
    """Authenticate user with username, password, and active valid period check."""
    user = get_user(username)
    
    if not user:
        return None
    
    # Check if user is approved
    if not user.get("is_approved"):
        return {"error": "User not approved by admin"}
        
    # Check if user valid period has expired
    valid_until_val = user.get("valid_until")
    if valid_until_val:
        try:
            if isinstance(valid_until_val, str):
                valid_until = datetime.strptime(valid_until_val.split(".")[0], "%Y-%m-%d %H:%M:%S")
            else:
                valid_until = valid_until_val
                
            if datetime.now() > valid_until:
                return {"error": f"Access Expired: Your login validity period ended on {valid_until.strftime('%Y-%m-%d')}. Please contact the system administrator."}
        except Exception:
            pass
    
    # Plain text password comparison
    if user["password"] == password:
        # Update last login
        execute_query(
            "UPDATE users SET last_login = NOW() WHERE id = %s",
            (user["id"],)
        )
        return user
    
    return None

def create_session(user_id: int, token: str, expires_at: datetime, ip_address: str = "", user_agent: str = "") -> bool:
    """Create a new session"""
    result = execute_query(
        """INSERT INTO sessions (user_id, token, expires_at, ip_address, user_agent)
           VALUES (%s, %s, %s, %s, %s)""",
        (user_id, token, expires_at, ip_address, user_agent)
    )
    return result is not None

def get_session(token: str) -> Optional[Dict]:
    """Get session by token"""
    result = execute_query(
        """SELECT s.*, u.username, u.is_admin, u.email, u.role 
           FROM sessions s
           JOIN users u ON s.user_id = u.id
           WHERE s.token = %s AND s.is_active = TRUE AND s.expires_at > NOW()""",
        (token,),
        fetch=True
    )
    
    if result:
        return result[0]
    return None

def invalidate_session(token: str) -> bool:
    """Invalidate a session"""
    result = execute_query(
        "UPDATE sessions SET is_active = FALSE WHERE token = %s",
        (token,)
    )
    return result is not None

def approve_user(username: str) -> Dict:
    """Admin: Approve a user for login"""
    result = execute_query(
        """UPDATE users SET is_approved = TRUE, approved_at = NOW() 
           WHERE username = %s""",
        (username,)
    )
    
    if result is None:
        return {"success": False, "error": "User not found"}
    
    return {"success": True, "message": f"User {username} approved"}

def reject_user(username: str) -> Dict:
    """Admin: Reject/delete a user request"""
    result = execute_query(
        "DELETE FROM users WHERE username = %s AND is_approved = FALSE",
        (username,)
    )
    
    if result is None or result == 0:
        return {"success": False, "error": "User not found or already approved"}
    
    return {"success": True, "message": f"User {username} rejected"}

def get_pending_users() -> List[Dict]:
    """Admin: Get all users waiting for approval"""
    result = execute_query(
        """SELECT id, username, email, full_name, created_at 
           FROM users WHERE is_approved = FALSE
           ORDER BY created_at ASC""",
        fetch=True
    )
    
    return result if result else []

def get_all_users() -> List[Dict]:
    """Admin: Get all users (without passwords)"""
    result = execute_query(
        """SELECT id, username, email, full_name, is_approved, is_admin, created_at, approved_at, role, valid_until
           FROM users ORDER BY created_at DESC""",
        fetch=True
    )
    
    return result if result else []

def make_admin(username: str) -> Dict:
    """Make a user admin"""
    result = execute_query(
        """UPDATE users SET is_admin = TRUE, is_approved = TRUE, approved_at = NOW() 
           WHERE username = %s""",
        (username,)
    )
    
    if result is None or result == 0:
        return {"success": False, "error": "User not found"}
    
    return {"success": True, "message": f"User {username} is now admin"}

def update_password(username: str, new_password: str) -> Dict:
    """Update user password (plain text)"""
    result = execute_query(
        "UPDATE users SET password = %s WHERE username = %s",
        (new_password, username)
    )
    
    if result is None:
        return {"success": False, "error": "Failed to update password"}
    
    return {"success": True, "message": f"Password updated for {username}"}

def log_activity(user_id: int, action: str, resource: str = "", status: str = "success", 
                 details: str = "", ip_address: str = "") -> bool:
    """Log user activity"""
    result = execute_query(
        """INSERT INTO activity_log (user_id, action, resource, status, details, ip_address)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, action, resource, status, details, ip_address)
    )
    return result is not None

def get_user_activity(user_id: int, limit: int = 50) -> List[Dict]:
    """Get user activity log"""
    result = execute_query(
        """SELECT id, action, resource, status, details, created_at 
           FROM activity_log WHERE user_id = %s
           ORDER BY created_at DESC LIMIT %s""",
        (user_id, limit),
        fetch=True
    )
    
    return result if result else []

def cleanup_expired_sessions() -> int:
    """Clean up expired sessions"""
    result = execute_query(
        "DELETE FROM sessions WHERE expires_at < NOW() OR is_active = FALSE"
    )
    return result if result else 0

def get_system_stats() -> Dict:
    """Get system statistics"""
    total_users = execute_query(
        "SELECT COUNT(*) as count FROM users",
        fetch=True
    )
    
    pending = execute_query(
        "SELECT COUNT(*) as count FROM users WHERE is_approved = FALSE",
        fetch=True
    )
    
    approved = execute_query(
        "SELECT COUNT(*) as count FROM users WHERE is_approved = TRUE",
        fetch=True
    )
    
    admins = execute_query(
        "SELECT COUNT(*) as count FROM users WHERE is_admin = TRUE",
        fetch=True
    )
    
    return {
        "total_users": total_users[0]["count"] if total_users else 0,
        "pending_approvals": pending[0]["count"] if pending else 0,
        "approved_users": approved[0]["count"] if approved else 0,
        "admins": admins[0]["count"] if admins else 0
    }

def admin_create_user(username: str, email: str, password: str, full_name: str, role: str, valid_until: str, is_approved: bool, is_admin: bool) -> Dict:
    """System Admin: Create a new user with full controls"""
    dup_user = execute_query("SELECT id FROM users WHERE username = %s", (username,), fetch=True)
    if dup_user:
        return {"success": False, "error": "Username already exists"}
        
    dup_email = execute_query("SELECT id FROM users WHERE email = %s", (email,), fetch=True)
    if dup_email:
        return {"success": False, "error": "Email already exists"}
        
    res = execute_query(
        """INSERT INTO users (username, email, password, full_name, is_approved, is_admin, role, valid_until, approved_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
        (username, email, password, full_name, is_approved, is_admin, role, valid_until)
    )
    if res is None:
        return {"success": False, "error": "Database error while inserting user"}
    return {"success": True, "message": f"User {username} created successfully", "user_id": res}

def admin_update_user(user_id: int, updates: dict) -> Dict:
    """System Admin: Update any user attributes dynamically"""
    user = execute_query("SELECT id, username FROM users WHERE id = %s", (user_id,), fetch=True)
    if not user:
        return {"success": False, "error": "User not found"}
        
    set_clauses = []
    params = []
    for key, value in updates.items():
        if value is not None:
            set_clauses.append(f"{key} = %s")
            params.append(value)
            
    if not set_clauses:
        return {"success": True, "message": "No changes to update"}
        
    params.append(user_id)
    query = f"UPDATE users SET {', '.join(set_clauses)} WHERE id = %s"
    res = execute_query(query, tuple(params))
    if res is None:
        return {"success": False, "error": "Database error during user update"}
    return {"success": True, "message": f"User {user[0]['username']} updated successfully"}

def admin_delete_user(user_id: int) -> Dict:
    """System Admin: Delete a user completely"""
    user = execute_query("SELECT id, username FROM users WHERE id = %s", (user_id,), fetch=True)
    if not user:
        return {"success": False, "error": "User not found"}
        
    execute_query("DELETE FROM sessions WHERE user_id = %s", (user_id,))
    execute_query("DELETE FROM activity_log WHERE user_id = %s", (user_id,))
    
    res = execute_query("DELETE FROM users WHERE id = %s", (user_id,))
    if res is None or res == 0:
         return {"success": False, "error": "Failed to delete user"}
    return {"success": True, "message": f"User {user[0]['username']} deleted successfully"}

