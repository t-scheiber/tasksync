"""Icon assignment for Notion tasks based on title keywords.

This module provides keyword-based icon matching for task titles.
Icons are emoji strings that Notion accepts for page icons.
"""

import re
from typing import Optional

# Default icon when no keywords match
DEFAULT_ICON = "🔧"

# Icon categories with keywords (case-insensitive matching)
# Order matters - first match wins, so put more specific categories first
ICON_CATEGORIES = [
    {
        "icon": "🔒",
        "name": "Security",
        "keywords": [
            "password", "phishing", "mfa", "2fa", "sso", "auth", "security",
            "encrypt", "certificate", "cert", "ssl", "tls", "vpn", "firewall",
            "vulnerability", "breach", "malware", "antivirus",
            "1password", "bitwarden", "lastpass", "credential", "secret",
        ],
    },
    {
        "icon": "📧",
        "name": "Email",
        "keywords": [
            "email", "e-mail", "outlook", "mailbox", "smtp", "imap", "inbox",
            "mail", "exchange", "calendar", "invite",
        ],
    },
    {
        "icon": "🌐",
        "name": "Network",
        "keywords": [
            "network", "wifi", "wi-fi", "dns", "dhcp", "ip address", "subnet",
            "router", "switch", "vlan", "bandwidth", "latency", "proxy",
        ],
    },
    {
        "icon": "💾",
        "name": "Database/Storage",
        "keywords": [
            "database", "sql", "postgres", "mysql", "mongodb", "redis",
            "backup", "restore", "storage", "nas", "san", "raid",
        ],
    },
    {
        "icon": "🐛",
        "name": "Bug/Issue",
        "keywords": [
            "bug", "error", "fix", "crash", "broken", "issue", "problem",
            "debug", "troubleshoot", "investigate", "failing",
        ],
    },
    {
        "icon": "👤",
        "name": "User/Account",
        "keywords": [
            "user", "account", "onboarding", "offboarding", "access",
            "active directory", " ad ", "ldap", "permission", "role",
            "new employee", "termination", "new joiner", "joiner",
            "new hire", "employee",
        ],
    },
    {
        "icon": "🖥️",
        "name": "Hardware",
        "keywords": [
            "printer", "laptop", "computer", "keyboard", "mouse",
            "hardware", "device", "workstation", "dock", "headset", "webcam",
            "scanner", "usb", "screen", "display", "macbook", "imac",
            "equipment", "reimaging", "reimage", "macos", " mac ",
        ],
    },
    {
        "icon": "☁️",
        "name": "Cloud",
        "keywords": [
            "azure", "aws", "gcp", "google cloud", "cloud", "sharepoint",
            "teams", "onedrive", "microsoft 365", "m365", "office 365", "o365",
            "saas", "tenant", "firebase", "bigquery",
        ],
    },
    {
        "icon": "📊",
        "name": "Reporting/Analytics",
        "keywords": [
            "report", "analytics", "dashboard", "metrics", "monitoring",
            "logging", "audit", "compliance", "grafana", "kibana",
        ],
    },
    {
        "icon": "📦",
        "name": "Update/Deploy",
        "keywords": [
            "update", "deploy", "install", "upgrade", "patch", "release",
            "rollout", "migration", "version", "package",
        ],
    },
    {
        "icon": "⚙️",
        "name": "Config/Setup",
        "keywords": [
            "config", "setup", "settings", "configure", "implementation",
            "integration", "api", "webhook",
        ],
    },
    {
        "icon": "📱",
        "name": "Mobile",
        "keywords": [
            "mobile", "phone", "iphone", "android", "tablet", "ipad",
            "mdm", "intune", "app", "ios", "ipados", "apple mobile",
        ],
    },
    {
        "icon": "🔄",
        "name": "Sync/Automation",
        "keywords": [
            "sync", "automat", "script", "cron", "scheduled", "workflow",
            "power automate", "zapier",
        ],
    },
    {
        "icon": "📝",
        "name": "Documentation",
        "keywords": [
            "document", "wiki", "confluence", "notion", "readme",
            "procedure", "policy", "guide",
        ],
    },
    {
        "icon": "🎫",
        "name": "Ticket/Request",
        "keywords": [
            "ticket", "request", "support", "helpdesk", "jira", "zendesk",
            "service desk",
        ],
    },
    {
        "icon": "💰",
        "name": "Finance",
        "keywords": [
            "invoice", "budget", "expense", "payment",
            "accounting", "finance", "billing",
        ],
    },
]


def get_icon_for_title(title: str) -> str:
    """Get the appropriate icon for a task based on its title.
    
    Args:
        title: The task title to analyze.
        
    Returns:
        An emoji string for the icon.
    """
    if not title:
        return DEFAULT_ICON
    
    title_lower = title.lower()
    
    for category in ICON_CATEGORIES:
        for keyword in category["keywords"]:
            # Use word boundary matching for short keywords to avoid false positives
            if len(keyword) <= 3:
                # Short keywords need word boundaries
                pattern = rf"\b{re.escape(keyword)}\b"
                if re.search(pattern, title_lower):
                    return category["icon"]
            else:
                # Longer keywords can use simple contains
                if keyword in title_lower:
                    return category["icon"]
    
    return DEFAULT_ICON


def get_category_for_title(title: str) -> Optional[str]:
    """Get the category name for a task (useful for debugging).
    
    Args:
        title: The task title to analyze.
        
    Returns:
        Category name or None if no match.
    """
    if not title:
        return None
    
    title_lower = title.lower()
    
    for category in ICON_CATEGORIES:
        for keyword in category["keywords"]:
            if len(keyword) <= 3:
                pattern = rf"\b{re.escape(keyword)}\b"
                if re.search(pattern, title_lower):
                    return category["name"]
            else:
                if keyword in title_lower:
                    return category["name"]
    
    return None


# Quick test when run directly
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    test_titles = [
        "Fix SSO login issue",
        "Setup new laptop for John",
        "Phishing report button",
        "Update SharePoint permissions",
        "Database backup failing",
        "Configure new printer",
        "Email signature template",
        "VPN connection problems",
        "Deploy new monitoring dashboard",
        "Create user account for new hire",
        "General task",
    ]
    
    print("Icon matching test:")
    print("-" * 60)
    for title in test_titles:
        icon = get_icon_for_title(title)
        category = get_category_for_title(title) or "Default"
        print(f"{icon} [{category:20}] {title}")
