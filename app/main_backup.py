
@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    try:
        start_date = date.fromisoformat(from_date)
        end_date = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD",
        )

    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (end_date - start_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    if not db.employees.find_one({"department": department}):
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    # MongoDB creates one row for every date in the range.
    pipeline = [
        {
            "$match": {
                "department": department,
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "employee_code": "$emp_code",
                    "join_date": "$joined_on",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {"$gte": ["$date", from_date]},
                                    {"$lte": ["$date", to_date]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                "date": "$logs.date",
                "status": "$logs.status",
                "half_day": "$logs.half_day",
                "late_minutes": "$logs.late_minutes",
                "joined_on": 1,
                "emp_code": 1,
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                
                                "$and": [
    {
        "$in": [
            "$status",
            ["PRESENT", "WFH", "ON_DUTY"],
        ]
    },
    {"$ne": ["$date", None]},
    {
        "$in": [
            {
                "$dayOfWeek": {
                    "$dateFromString": {
                        "dateString": "$date"
                    }
                }
            },
            [2, 3, 4, 5, 6],
        ]
    },
]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {"$sort": {"_id": 1}},
    ]

    # This endpoint needs a complete daily series, including dates
    # that have no attendance records.
    aggregated = {
        row["_id"]: row
        for row in db.employees.aggregate(pipeline)
        if row["_id"] is not None
    }

    items = []
    current = start_date

    while current <= end_date:
        day_string = current.isoformat()
        working_day = current.weekday() < 5

        headcount = db.employees.count_documents({
            "department": department,
            "joined_on": {"$lte": day_string},
        })

        stats = aggregated.get(day_string, {})
        present_count = stats.get("present_count", 0)
        late_count = stats.get("late_count", 0)

        attendance_rate = None
        if working_day and headcount > 0:
            attendance_rate = float(
                (
                    Decimal(str(present_count))
                    / Decimal(headcount)
                    * Decimal("100")
                ).quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP,
                )
            )

        items.append({
            "date": day_string,
            "is_working_day": working_day,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": late_count,
            "attendance_rate": attendance_rate,
        })

        current += timedelta(days=1)

    # Calculate the moving average from non-null rates in the
    # current day and up to six preceding rows.
    for index, item in enumerate(items):
        window = items[max(0, index - 6):index + 1]
        rates = [
            row["attendance_rate"]
            for row in window
            if row["attendance_rate"] is not None
        ]

        item["moving_avg_7d"] = (
            float(
                (
                    sum(Decimal(str(rate)) for rate in rates)
                    / Decimal(len(rates))
                ).quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP,
                )
            )
            if rates
            else None
        )

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": items,
    }
