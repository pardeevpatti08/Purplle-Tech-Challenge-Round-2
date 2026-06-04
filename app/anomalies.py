from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import (
    daily_conversion_rates,
    fetch_store_events,
    fetch_store_pos,
    latest_queue_depth,
    non_staff,
    unique_visitors,
)
from app.db import get_session
from app.layout_config import zones_for_store
from app.store_ids import canonical_store_id

router = APIRouter(prefix="/stores", tags=["anomalies"])


@router.get("/{id}/anomalies")
async def store_anomalies(id: str, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        events = await fetch_store_events(session, id)
        transactions = await fetch_store_pos(session, id)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_unavailable",
                "message": "Service temporarily unavailable",
                "trace_id": getattr(request.state, "trace_id", None),
            },
        )

    anomalies = []
    detected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    queue_depth = latest_queue_depth(events)
    if queue_depth > 10:
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL",
                "detected_at": detected_at,
                "suggested_action": "Open additional billing counters and redirect floor staff to checkout.",
                "metadata": {"queue_depth": queue_depth},
            }
        )
    elif queue_depth > 5:
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "WARN",
                "detected_at": detected_at,
                "suggested_action": "Monitor billing queue and prepare an additional counter.",
                "metadata": {"queue_depth": queue_depth},
            }
        )

    visitors = unique_visitors(events)
    rates = daily_conversion_rates(events, transactions)
    if transactions and visitors and len(rates) >= 2:
        latest_day = max(rates)
        previous_days = [day for day in sorted(rates) if day < latest_day][-7:]
        if previous_days:
            current_rate = rates[latest_day]
            seven_day_average = sum(rates[day] for day in previous_days) / len(previous_days)
            if seven_day_average > 0 and current_rate < seven_day_average * 0.8:
                anomalies.append(
                    {
                        "type": "CONVERSION_DROP",
                        "severity": "WARN",
                        "detected_at": detected_at,
                        "suggested_action": "Review floor staff deployment and promotion visibility.",
                        "metadata": {
                            "conversion_rate": round(current_rate, 4),
                            "seven_day_average": round(seven_day_average, 4),
                        },
                    }
                )

    customer_events = non_staff(events)
    last_event_ts = max((event.timestamp for event in customer_events), default=None)
    if last_event_ts:
        cutoff = last_event_ts - timedelta(minutes=30)
        active_zones = {
            event.zone_id
            for event in customer_events
            if event.event_type == "ZONE_ENTER" and event.zone_id and event.timestamp >= cutoff
        }
        for zone_id in zones_for_store(id):
            if zone_id not in active_zones:
                anomalies.append(
                    {
                        "type": "DEAD_ZONE",
                        "severity": "INFO",
                        "detected_at": detected_at,
                        "suggested_action": "Check camera coverage or refresh product display in this zone.",
                        "metadata": {"zone_id": zone_id},
                    }
                )

    return {"store_id": canonical_store_id(id), "anomalies": anomalies}
