from app.core.license.client import LicenseClient, LicenseError
from app.core.license.device_id import get_device_id
from app.core.license.store import LicenseState, load_license, save_license

__all__ = [
    "LicenseClient",
    "LicenseError",
    "LicenseService",
    "LicenseState",
    "get_device_id",
    "load_license",
    "save_license",
]


def __getattr__(name: str):
    if name == "LicenseService":
        from app.core.license.service import LicenseService

        return LicenseService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
