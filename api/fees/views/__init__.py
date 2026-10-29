"""
Viewsets for the fees app.

Kept deliberately thin: a handler validates, then hands off to a service in
`fees/services/` (writes) or a selector in `fees/selectors.py` (reads).
Re-exported flat so `from api.fees.views import FeeViewSet` keeps working.
"""

from .batch_views import GeneratedFeeBatchViewSet
from .fee_views import FeeViewSet

__all__ = ["FeeViewSet", "GeneratedFeeBatchViewSet"]
