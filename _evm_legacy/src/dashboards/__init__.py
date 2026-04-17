"""Dashboard HTML templates — each page in its own module."""

from dashboards.main_dashboard import DASHBOARD_HTML
from dashboards.opportunity_detail import OPPORTUNITY_DETAIL_HTML
from dashboards.ops_dashboard import OPS_DASHBOARD_HTML
from dashboards.analytics_dashboard import ANALYTICS_HTML

__all__ = ["DASHBOARD_HTML", "OPPORTUNITY_DETAIL_HTML", "OPS_DASHBOARD_HTML", "ANALYTICS_HTML"]
