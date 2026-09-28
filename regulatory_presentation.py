"""Display-only helpers; never change stored timestamps, actor IDs or snapshots."""
from datetime import datetime, timedelta, timezone

VIETNAM_TIME = timezone(timedelta(hours=7), name='Asia/Ho_Chi_Minh')


def vietnam_time(value):
    """Render DB timestamptz or its JSON ISO representation in Vietnam time."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return '—'
    if not isinstance(value, datetime):
        return '—'
    # Database timestamps are aware; legacy naive display inputs are interpreted as UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(VIETNAM_TIME).strftime('%d/%m/%Y %H:%M')
