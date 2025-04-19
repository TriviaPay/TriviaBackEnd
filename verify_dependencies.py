#!/usr/bin/env python3
"""
Dependency version verification script
Checks that the installed dependencies match the expected secure versions
"""
import sys
import importlib.metadata
import pkg_resources

# List of dependencies with their expected versions
EXPECTED_VERSIONS = {
    "fastapi": "0.104.1",
    "uvicorn": "0.24.0",
    "sqlalchemy": "2.0.23",
    "pydantic": "2.5.2",
    "python-jose": "3.3.0",  # Note: We can't easily check for [cryptography] extra
    "passlib": "1.7.4",
    "python-multipart": "0.0.7",  # Updated for security
    "python-dotenv": "1.0.0",
    "alembic": "1.12.1",
    "pg8000": "1.30.2",
    "requests": "2.31.0",
    "pytz": "2023.3.post1",
    "apscheduler": "3.10.4",
    "typing-extensions": "4.8.0",
    "wheel": "0.41.3",
    "argon2-cffi": "23.1.0",
    "cryptography": "42.0.2",  # Updated for security
    "charset-normalizer": "3.3.2",
    "idna": "3.6",  # Updated for security
    "urllib3": "2.1.0",
    "certifi": "2024.2.2",  # Updated for security
    "pillow": "10.2.0",  # Updated for security
}

# Security critical packages to check
SECURITY_CRITICAL = [
    "python-jose",
    "cryptography",
    "python-multipart",
    "pillow",
    "idna",
    "certifi",
]

def verify_dependencies():
    """Verify that installed dependencies match expected versions."""
    print("Verifying dependency versions...")
    
    issues = []
    
    for package, expected_version in EXPECTED_VERSIONS.items():
        try:
            installed_version = importlib.metadata.version(package)
            if installed_version != expected_version:
                message = f"⚠️ {package}: installed {installed_version}, expected {expected_version}"
                if package in SECURITY_CRITICAL:
                    message += " (SECURITY CRITICAL)"
                issues.append(message)
            else:
                is_critical = "(SECURITY CRITICAL)" if package in SECURITY_CRITICAL else ""
                print(f"✅ {package}: {installed_version} {is_critical}")
        except importlib.metadata.PackageNotFoundError:
            issues.append(f"❌ {package}: not installed, expected {expected_version}")
    
    # Check for cryptography backend in python-jose
    try:
        import jose
        import jose.backends
        backend = jose.backends._get_backend()
        backend_name = backend.__module__.split('.')[-1]
        if backend_name != 'cryptography':
            issues.append("⚠️ python-jose is not using cryptography backend (SECURITY CRITICAL)")
        else:
            print("✅ python-jose is using cryptography backend")
    except (ImportError, AttributeError):
        issues.append("❌ Unable to verify python-jose backend (SECURITY CRITICAL)")
    
    if issues:
        print("\nIssues found:")
        for issue in issues:
            print(issue)
        print("\n⚠️ Some dependencies have version mismatches or are missing!")
        print("Please run: pip install -r requirements.txt")
        return False
    
    print("\n✅ All dependencies are installed with correct versions!")
    return True

if __name__ == "__main__":
    success = verify_dependencies()
    sys.exit(0 if success else 1) 