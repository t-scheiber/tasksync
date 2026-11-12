"""TickTick OAuth helper for TaskSync.

Run this script to get your TickTick access and refresh tokens:
    python -m tasksync.auth_ticktick

Prerequisites:
    1. Register an app at https://developer.ticktick.com
    2. Set redirect URI to http://localhost:8080/callback
    3. Copy your Client ID and Client Secret
"""

import base64
import html
import webbrowser
from urllib.parse import urlencode
import requests

try:
    from flask import Flask, request
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

from .logger import setup_logger

logger = setup_logger(__name__, "INFO")

# Configuration
REDIRECT_URI = "http://localhost:8080/callback"
AUTH_URL = "https://ticktick.com/oauth/authorize"
TOKEN_URL = "https://ticktick.com/oauth/token"
SCOPES = "tasks:read tasks:write"


def get_auth_url(client_id: str) -> str:
    """Generate the authorization URL."""
    params = {
        "client_id": client_id,
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(
    code: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Exchange authorization code for access and refresh tokens."""
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()
    
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    
    if response.status_code != 200:
        raise Exception(f"Token exchange failed: HTTP {response.status_code}")
    
    return response.json()


def _oauth_error_page(error: str) -> str:
    """Render an OAuth error as text inside the fixed callback page."""
    return f"<h1>Error</h1><p>{html.escape(error)}</p><p>You can close this window.</p>"


def run_oauth_flow_with_server(client_id: str, client_secret: str) -> dict:
    """Run OAuth flow with local Flask server to catch callback."""
    if not HAS_FLASK:
        raise ImportError("Flask is required for OAuth flow. Run: pip install flask")
    
    app = Flask(__name__)
    result = {"code": None, "error": None}
    
    @app.route("/callback")
    def callback():
        code = request.args.get("code")
        error = request.args.get("error")
        
        if error:
            result["error"] = error
            return _oauth_error_page(error)
        
        if code:
            result["code"] = code
            return "<h1>Success!</h1><p>Authorization code received. You can close this window.</p>"
        
        return "<h1>Error</h1><p>No code received.</p>"
    
    @app.route("/shutdown")
    def shutdown():
        func = request.environ.get("werkzeug.server.shutdown")
        if func:
            func()
        return "Server shutting down..."
    
    # Open browser for authorization
    auth_url = get_auth_url(client_id)
    print(f"\n{'='*60}")
    print("Opening browser for TickTick authorization...")
    print(f"{'='*60}\n")
    print(f"If browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)
    
    # Run server to catch callback
    from werkzeug.serving import make_server
    server = make_server("localhost", 8080, app)
    server.timeout = 120  # 2 minute timeout
    
    print("Waiting for authorization (timeout: 2 minutes)...")
    
    # Handle one request
    server.handle_request()
    
    if result["error"]:
        raise Exception(f"Authorization failed: {result['error']}")
    
    if not result["code"]:
        raise Exception("No authorization code received")
    
    # Exchange code for tokens
    print("\nExchanging authorization code for tokens...")
    tokens = exchange_code_for_tokens(result["code"], client_id, client_secret)
    
    return tokens


def run_oauth_flow_manual(client_id: str, client_secret: str) -> dict:
    """Run OAuth flow with manual code entry (fallback)."""
    auth_url = get_auth_url(client_id)
    
    print(f"\n{'='*60}")
    print("TickTick OAuth Setup")
    print(f"{'='*60}\n")
    print("1. Open this URL in your browser:\n")
    print(f"   {auth_url}\n")
    print("2. Log in and authorize the app")
    print("3. Copy the 'code' parameter from the redirect URL")
    print("   (The URL will look like: http://localhost:8080/callback?code=XXXXXX)\n")
    
    code = input("Enter the authorization code: ").strip()
    
    if not code:
        raise ValueError("No code provided")
    
    print("\nExchanging authorization code for tokens...")
    tokens = exchange_code_for_tokens(code, client_id, client_secret)
    
    return tokens


def list_projects(access_token: str) -> list:
    """List all TickTick projects to help user find project ID."""
    response = requests.get(
        "https://api.ticktick.com/open/v1/project",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    
    if response.status_code != 200:
        raise Exception(f"Failed to list projects: HTTP {response.status_code}")
    
    return response.json()


def main():
    """Main entry point for OAuth setup."""
    print(f"\n{'='*60}")
    print("TickTick OAuth Setup for TaskSync")
    print(f"{'='*60}\n")
    
    print("Prerequisites:")
    print("1. Go to https://developer.ticktick.com/docs#/openapi")
    print("2. Click 'Manage Apps' and create a new app")
    print("3. Set Redirect URI to: http://localhost:8080/callback")
    print("4. Copy your Client ID and Client Secret\n")
    
    client_id = input("Enter your Client ID: ").strip()
    client_secret = input("Enter your Client Secret: ").strip()
    
    if not client_id or not client_secret:
        print("Error: Client ID and Secret are required")
        return
    
    # Try with Flask server first, fall back to manual
    try:
        if HAS_FLASK:
            tokens = run_oauth_flow_with_server(client_id, client_secret)
        else:
            print("\nFlask not installed, using manual flow...")
            tokens = run_oauth_flow_manual(client_id, client_secret)
    except Exception as e:
        print(f"\nAutomatic flow failed: {e}")
        print("Falling back to manual flow...\n")
        tokens = run_oauth_flow_manual(client_id, client_secret)
    
    print(f"\n{'='*60}")
    print("SUCCESS! Here are your tokens:")
    print(f"{'='*60}\n")
    print(f"Access Token:  {tokens.get('access_token', 'N/A')}")
    print(f"Refresh Token: {tokens.get('refresh_token', 'N/A')}")
    print(f"Expires In:    {tokens.get('expires_in', 'N/A')} seconds")
    
    # List projects to help user find project ID
    print(f"\n{'='*60}")
    print("Your TickTick Projects:")
    print(f"{'='*60}\n")
    
    try:
        projects = list_projects(tokens["access_token"])
        for project in projects:
            print(f"  - {project['name']}: {project['id']}")
    except Exception as e:
        print(f"  (Could not list projects: {e})")
    
    print(f"\n{'='*60}")
    print("Add these to your .env file:")
    print(f"{'='*60}\n")
    print(f"TICKTICK_CLIENT_ID={client_id}")
    print(f"TICKTICK_CLIENT_SECRET={client_secret}")
    print(f"TICKTICK_ACCESS_TOKEN={tokens.get('access_token', '')}")
    print(f"TICKTICK_REFRESH_TOKEN={tokens.get('refresh_token', '')}")
    print("TICKTICK_PROJECT_ID=<copy the project ID from above>")


if __name__ == "__main__":
    main()
