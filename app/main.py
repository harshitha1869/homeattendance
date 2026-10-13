"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
import calendar


from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, StrictInt
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))


from decimal import Decimal, ROUND_HALF_UP

def epoch_ms_to_ist(value: int) -> datetime:
    """Convert epoch milliseconds to an IST datetime, truncated to seconds."""
    utc_dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return utc_dt.astimezone(IST).replace(microsecond=0)

def round_half_up(value: float, places: int = 2) -> float:
    """Round numeric values using the assignment's half-up rule."""
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employees_emp_code",
    )

    db.employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)],
        name="ix_employees_department_emp_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_emp_code_date",
    )

    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_date_emp_code",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def epoch_ms_to_ist(value: int) -> datetime:
    """Convert epoch milliseconds to IST, truncated to whole seconds."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(422, "Timestamp must be an integer in milliseconds")

    if not 100_000_000_000 <= value <= 4_102_444_800_000:
        raise HTTPException(422, "Timestamp is outside the allowed range")

    utc_time = datetime.fromtimestamp(value // 1000, tz=timezone.utc)
    return utc_time.astimezone(IST)


def to_mongo_utc(value: datetime) -> datetime:
    """Store an instant as a naive UTC datetime for MongoDB."""
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    """Convert a MongoDB UTC datetime to epoch milliseconds."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return int(value.timestamp()) * 1000


def attendance_date_for(punch_in: datetime, shift_start: str,
                        shift_end: str) -> str:
    """Determine the attendance date, including overnight shifts."""
    local_time = punch_in.strftime("%H:%M")

    if shift_end <= shift_start and local_time < shift_end:
        return (punch_in.date() - timedelta(days=1)).isoformat()

    return punch_in.date().isoformat()








def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    """Calculate lateness in whole minutes, using IST."""
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)

    local_punch = punch_in.astimezone(IST)
    shift_hour, shift_minute = map(int, shift_start.split(":"))

    shift_start_time = local_punch.replace(
        hour=shift_hour,
        minute=shift_minute,
        second=0,
        microsecond=0,
    )

    elapsed_seconds = (
        local_punch - shift_start_time
    ).total_seconds()

    if elapsed_seconds <= 600:
        return 0

    return int(elapsed_seconds // 60)



def compute_work_hours(punch_in: datetime,
                       punch_out: datetime) -> float:
    """Round work hours to two decimal places using half-up rounding."""
    seconds = int((punch_out - punch_in).total_seconds())
    hours = Decimal(seconds) / Decimal(3600)

    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_end: str,
                     attendance_date: str, shift_start: str) -> int:
    """Count overtime only when at least 30 minutes beyond shift end."""
    hours, minutes = map(int, shift_end.split(":"))
    shift_day = datetime.strptime(attendance_date, "%Y-%m-%d").date()

    end_day = shift_day
    if shift_end <= shift_start:
        end_day += timedelta(days=1)

    shift_end_time = datetime(
        end_day.year, end_day.month, end_day.day,
        hours, minutes, tzinfo=IST
    )

    elapsed_seconds = int((punch_out - shift_end_time).total_seconds())

    if elapsed_seconds >= 1800:
        return elapsed_seconds // 60

    return 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
import re
from datetime import date
from pydantic import BaseModel, Field, field_validator, model_validator


class EmployeeIn(BaseModel):
    emp_code: str = Field(
        ...,
        pattern=r"^EMP\d{4,6}$",
    )
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=1, max_length=120)
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Shift time must be in HH:MM format")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("Shift start and end times must be different")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    doc["created_at"] = int(doc["created_at"].timestamp() * 1000)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}
    if department is not None:
        query["department"] = department

    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    for employee in items:
        if isinstance(employee.get("created_at"), datetime):
            employee["created_at"] = int(
                employee["created_at"].replace(tzinfo=timezone.utc).timestamp()
                * 1000
            )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="status must be PRESENT, WFH, or ON_DUTY",
        )

    if body.punched_at is not None:
        ts = epoch_ms_to_ist(body.punched_at)
    else:
        ts = datetime.now(IST).replace(microsecond=0)

    # The attendance date is determined using the IST calendar date.
    attendance_date = attendance_date_for(
    ts,
    emp["shift_start"],
    emp["shift_end"],
)

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": to_mongo_utc(ts),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this date",
        )

    doc.pop("_id", None)
    doc["id"] = str(result.inserted_id)

    # Return timestamps as epoch milliseconds.
    doc["punch_in"] = int(ts.timestamp() * 1000)
    return doc


        
class AttendanceCorrectionIn(BaseModel):
    reason: str
    regularized_by: str
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None

       
    

   
 



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    cursor = (
        db.attendance_logs.find(q, {"_id": 1, "emp_code": 1, "date": 1,
                                    "status": 1, "punch_in": 1,
                                    "punch_out": 1, "work_hours": 1,
                                    "late_minutes": 1, "overtime_minutes": 1,
                                    "half_day": 1, "history": 1})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = []
    for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        doc["punch_in"] = to_epoch_ms(doc.get("punch_in"))
        doc["punch_out"] = to_epoch_ms(doc.get("punch_out"))

        # Convert history timestamps when present.
        for entry in doc.get("history", []):
            if isinstance(entry.get("at"), datetime):
                entry["at"] = to_epoch_ms(entry["at"])

        items.append(doc)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

           


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #



class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    ts = ts.replace(microsecond=0)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(status_code=404, detail="No open punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Already punched out")

    punch_in_time = record["punch_in"]
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    elapsed = (ts - punch_in_time).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 3600:
        raise HTTPException(status_code=422, detail="Invalid punch-out time")

    work_hours = float(
        Decimal(str(elapsed / 3600)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )

    shift_end_hour, shift_end_minute = map(int, emp["shift_end"].split(":"))
    shift_start_hour, shift_start_minute = map(int, emp["shift_start"].split(":"))

    punch_in_ist = punch_in_time.astimezone(IST)
    punch_out_ist = ts.astimezone(IST)

    end_date = punch_in_ist.date()
    if (shift_end_hour, shift_end_minute) <= (
        shift_start_hour, shift_start_minute
    ):
        if (punch_in_ist.hour, punch_in_ist.minute) < (
            shift_end_hour, shift_end_minute
        ):
            end_date += timedelta(days=1)

    shift_end = datetime(
        end_date.year, end_date.month, end_date.day,
        shift_end_hour, shift_end_minute, tzinfo=IST
    )

    overtime = max(
        0, int((punch_out_ist - shift_end).total_seconds() // 60)
    )
    if overtime < 30:
        overtime = 0

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Already punched out")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))
    for field in ("punch_in", "punch_out"):
       value = updated.get(field)
       if value is not None:
          if value.tzinfo is None:
             value = value.replace(tzinfo=timezone.utc)
          updated[field] = int(value.timestamp() * 1000)

    return updated
    



from datetime import date as date_type


@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(
    emp_code: str,
    date: str,
    body: AttendanceCorrectionIn,
):
    from pydantic import ValidationError
    import re

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")

    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date")

    if not 5 <= len(body.reason) <= 200:
        raise HTTPException(status_code=422, detail="reason must be 5-200 characters")

    if not 1 <= len(body.regularized_by) <= 50:
        raise HTTPException(status_code=422, detail="regularized_by must be 1-50 characters")

    if db.employees.find_one({"emp_code": emp_code}) is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    changes = {}
    updates = {}

    if body.status is not None:
        if body.status not in {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}:
            raise HTTPException(status_code=422, detail="Invalid status")
        if body.status != record.get("status"):
            updates["status"] = body.status
            changes["status"] = {"old": record.get("status"), "new": body.status}

    for field in ("punch_in", "punch_out"):
        value = getattr(body, field)
        if value is not None:
            if not isinstance(value, int) or not 100000000000 <= value <= 4102444800000:
                raise HTTPException(status_code=422, detail=f"{field} must be epoch milliseconds")

            new_time = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            new_time = new_time.replace(microsecond=0)
            old_time = record.get(field)

            if old_time is not None and old_time.tzinfo is None:
                old_time = old_time.replace(tzinfo=timezone.utc)

            if old_time != new_time:
                updates[field] = new_time
                changes[field] = {
                    "old": int(old_time.timestamp() * 1000) if old_time else None,
                    "new": int(new_time.timestamp() * 1000),
                }

    resulting_status = updates.get("status", record.get("status"))

    if resulting_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(status_code=422, detail="ABSENT/LEAVE cannot have punch times")
        updates["punch_in"] = None
        updates["punch_out"] = None
    elif resulting_status in {"PRESENT", "WFH", "ON_DUTY"}:
        final_in = updates.get("punch_in", record.get("punch_in"))
        final_out = updates.get("punch_out", record.get("punch_out"))

        if final_in is None:
            raise HTTPException(status_code=422, detail="Presence status requires punch_in")

        if final_out is not None:
            if final_in.tzinfo is None:
                final_in = final_in.replace(tzinfo=timezone.utc)
            if final_out.tzinfo is None:
                final_out = final_out.replace(tzinfo=timezone.utc)

            seconds = (final_out - final_in).total_seconds()
            if seconds <= 0 or seconds > 24 * 3600:
                raise HTTPException(status_code=422, detail="Invalid punch interval")

    if not changes:
        raise HTTPException(status_code=422, detail="No changes to apply")

    final_in = updates.get("punch_in", record.get("punch_in"))
    final_out = updates.get("punch_out", record.get("punch_out"))

    if resulting_status in {"ABSENT", "LEAVE"}:
        updates["punch_in"] = None
        updates["punch_out"] = None
        updates["work_hours"] = None
        updates["late_minutes"] = 0
        updates["overtime_minutes"] = 0
        updates["half_day"] = False
    else:
        if final_in is not None:
            employee = db.employees.find_one({"emp_code": emp_code})
            updates["late_minutes"] = compute_late_minutes(final_in, employee["shift_start"])

        if final_in is not None and final_out is not None:
            if final_in.tzinfo is None:
                final_in = final_in.replace(tzinfo=timezone.utc)
            if final_out.tzinfo is None:
                final_out = final_out.replace(tzinfo=timezone.utc)

            hours = Decimal(str((final_out - final_in).total_seconds() / 3600))
            work_hours = float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            updates["work_hours"] = work_hours
            updates["half_day"] = work_hours < 4.50
        else:
            updates["work_hours"] = None
            updates["half_day"] = False

    changes = {
        key: value for key, value in changes.items()
        if key in {"status", "punch_in", "punch_out"}
    }

    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": record.get("status"),
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
        },
        {
            "$set": updates,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Record changed concurrently; retry")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))
    return updated



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )


    year, month_number = map(int, month.split("-"))
    first_day = date(year, month_number, 1)
    last_day = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1],
    )

    
    joined_on = date.fromisoformat(employee["joined_on"])
    effective_start = max(first_day, joined_on)

    working_days = 0
    current_day = effective_start

    while current_day <= last_day:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

   
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                },
            }
        },
       
        {
    "$addFields": {
        "parsed_date": {
            "$dateFromString": {
                "dateString": "$date",
                "format": "%Y-%m-%d",
                "timezone": "Asia/Kolkata"
            }
        }
    }
},
{
    "$addFields": {
        "is_weekday": {
            "$in": [
                {"$dayOfWeek": "$parsed_date"},
                [2, 3, 4, 5, 6]
            ]
        }
    }
},
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_weekday",
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
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
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
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
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

   
    
    print("DEBUG attendance records:", list(
    db.attendance_logs.find(
        {"emp_code": emp_code},
        {"_id": 0, "date": 1, "status": 1, "half_day": 1}
    )
))
    results = list(db.attendance_logs.aggregate(pipeline))
    print("DEBUG monthly aggregation:", results)

    stats = results[0] if results else {}

    present_days = stats.get("present_days", 0)
    attendance_pct = None

    if working_days > 0:
        attendance_pct = float(
            (
                Decimal(str(present_days))
                / Decimal(working_days)
                * Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))
    first_day = date(year, month_number, 1)
    last_day = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1],
    )

   
    employee_filter = {}
    if department:
        employee_filter["department"] = department

    employees = list(
        db.employees.find(
            employee_filter,
            {"_id": 0, "emp_code": 1, "department": 1, "joined_on": 1},
        )
    )

    if not employees:
        return {"month": month, "items": []}

    employee_codes = [e["emp_code"] for e in employees]
    department_by_code = {
        e["emp_code"]: e["department"] for e in employees
    }

    pipeline = [
        
        {
    "$match": {
        "emp_code": {"$in": employee_codes},
        "date": {
            "$gte": first_day.isoformat(),
            "$lte": last_day.isoformat(),
        },
    }
},
{
    "$addFields": {
        "department": {
            "$literal": None
        }
    }
},
        {
            "$group": {
                "_id": "$emp_code",
                "present_days": {
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
                "avg_work_hours": {
                    "$avg": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": ["$work_hours", None]},
                                ]
                            },
                            "$work_hours",
                            None,
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
                "total_late_minutes": {"$sum": "$late_minutes"},
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]
                    }
                },
            }
        },
    ]

    aggregated = {
        row["_id"]: row
        for row in db.attendance_logs.aggregate(pipeline)
    }

   
    grouped = {}
    for employee in employees:
        dept = employee["department"]
        grouped.setdefault(dept, []).append(employee)

    items = []
    for dept in sorted(grouped):
        dept_employees = grouped[dept]
        present_days = 0
        late_count = 0
        total_late_minutes = 0
        leave_count = 0
        on_duty_count = 0
        work_hours_values = []

        for employee in dept_employees:
            stats = aggregated.get(employee["emp_code"], {})
            present_days += stats.get("present_days", 0)
            late_count += stats.get("late_count", 0)
            total_late_minutes += stats.get("total_late_minutes", 0)
            leave_count += stats.get("leave_count", 0)
            on_duty_count += stats.get("on_duty_count", 0)
            for hours in stats.get("work_hours_values", []):
               if hours is not None:
                              work_hours_values.append(hours)

        avg_work_hours = (
            round(sum(work_hours_values) / len(work_hours_values), 2)
            if work_hours_values
            else None
        )

        items.append({
            "department": dept,
            "headcount": len(dept_employees),
            "present_days": present_days,
            "avg_work_hours": avg_work_hours,
            "late_count": late_count,
            "total_late_minutes": total_late_minutes,
            "leave_count": leave_count,
            "on_duty_count": on_duty_count,
        })

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))
    first_day = date(year, month_number, 1)
    last_day = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1],
    )

    employee_filter = {}
    if department:
        employee_filter["department"] = department

    employees = list(
        db.employees.find(
            employee_filter,
            {"_id": 0, "emp_code": 1, "name": 1, "department": 1},
        )
    )

    employee_by_code = {
        employee["emp_code"]: employee for employee in employees
    }
    employee_codes = list(employee_by_code)

    if not employee_codes:
        return {"month": month, "items": []}

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": employee_codes},
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
    ]

    totals = list(db.attendance_logs.aggregate(pipeline))


    ranked = []
    previous_total = None
    rank = 0

    for position, record in enumerate(totals, start=1):
        total = record["total_late_minutes"]

        if total != previous_total:
            rank = position
            previous_total = total

        employee = employee_by_code[record["_id"]]
        ranked.append({
            "emp_code": record["_id"],
            "name": employee["name"],
            "department": employee["department"],
            "total_late_minutes": total,
            "rank": rank,
        })

  
    items = [row for row in ranked if row["rank"] <= limit]

    return {"month": month, "items": items}



@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    from datetime import datetime, timezone
    from decimal import Decimal, ROUND_HALF_UP

    try:
        start_date = date.fromisoformat(from_date)
        end_date = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format",
        )

    if start_date.isoformat() != from_date or end_date.isoformat() != to_date:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format",
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
        raise HTTPException(status_code=404, detail="Department not found")
    start_dt = datetime.combine(
        start_date, time.min, tzinfo=timezone.utc
    )
    end_exclusive_dt = datetime.combine(
        end_date + timedelta(days=1),
        time.min,
        tzinfo=timezone.utc,
    )

    pipeline = [
        {
            "$documents": [
                {"date": start_dt}
            ]
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_exclusive_dt],
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "date_string": {
                    "$dateToString": {
                        "date": "$date",
                        "format": "%Y-%m-%d",
                        "timezone": "UTC",
                    }
                },
                "day_of_week": {
                    "$dayOfWeek": "$date"
                },
            }
        },

        # Find employees who have joined by each date.
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": ["$joined_on", "$$day"]
                            },
                        }
                    },
                    {
                        "$lookup": {
                            "from": "attendance_logs",
                            "let": {
                                "employee_code": "$emp_code",
                                "day": "$$day",
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
                                                {
                                                    "$eq": [
                                                        "$date",
                                                        "$$day",
                                                    ]
                                                },
                                            ]
                                        }
                                    }
                                }
                            ],
                            "as": "logs",
                        }
                    },
                    {
                        "$unwind": {
                            "path": "$logs",
                            "preserveNullAndEmptyArrays": True,
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "emp_code": 1,
                            "log_date": "$logs.date",
                            "status": "$logs.status",
                            "half_day": "$logs.half_day",
                            "late_minutes": "$logs.late_minutes",
                        }
                    },
                ],
                "as": "employees_for_day",
            }
        },
        {
            "$unwind": {
                "path": "$employees_for_day",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$date_string",
                "day_of_week": {"$first": "$day_of_week"},
                "headcount": {
                    "$sum": {
                        "$cond": [
                            {
                                "$ne": [
                                    "$employees_for_day.emp_code",
                                    None,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$employees_for_day.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$in": [
                                            "$day_of_week",
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {
                                        "$eq": [
                                            "$employees_for_day.half_day",
                                            True,
                                        ]
                                    },
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
                            {
                                "$gt": [
                                    "$employees_for_day.late_minutes",
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "is_working_day": {
                    "$in": ["$day_of_week", [2, 3, 4, 5, 6]]
                },
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                {
                                    "$in": [
                                        "$day_of_week",
                                        [2, 3, 4, 5, 6],
                                    ]
                                },
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$multiply": [
                                {
                                    "$divide": [
                                        "$present_count",
                                        "$headcount",
                                    ]
                                },
                                100,
                            ]
                        },
                        None,
                    ]
                },
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d_raw": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        },
                    }
                },
            }
        },
        {
            "$project": {
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d_raw": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]
    result = db.command(
        "aggregate",
        1,
        pipeline=pipeline,
        cursor={},
    )

    rows = result["cursor"]["firstBatch"]
    items = []
    for row in rows:
        rate = row.get("attendance_rate")
        moving_avg = row.get("moving_avg_7d_raw")

        if rate is not None:
            rate = float(
                Decimal(str(rate)).quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP,
                )
            )

        if moving_avg is not None:
            moving_avg = float(
                Decimal(str(moving_avg)).quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP,
                )
            )

        items.append(
            {
                "date": row["date"],
                "is_working_day": row["is_working_day"],
                "headcount": row["headcount"],
                "present_count": row["present_count"],
                "late_count": row["late_count"],
                "attendance_rate": rate,
                "moving_avg_7d": moving_avg,
            }
        )

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": items,
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(
            status_code=422,
            detail="Unsupported endpoint",
        )

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status

        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

        cursor = db.attendance_logs.find(query).sort([
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ]).skip((page - 1) * page_size).limit(page_size)

        return db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        })

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        try:
            year, month_number = map(int, month.split("-"))
            first_day = date(year, month_number, 1)
            last_day = date(
                year,
                month_number,
                calendar.monthrange(year, month_number)[1],
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": first_day.isoformat(),
                        "$lte": last_day.isoformat(),
                    },
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
        ]

        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")

        try:
            year, month_number = map(int, month.split("-"))
            first_day = date(year, month_number, 1)
            last_day = date(
                year,
                month_number,
                calendar.monthrange(year, month_number)[1],
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

        employee_filter = {}
        if department:
            employee_filter["department"] = department

        employee_codes = [
            e["emp_code"]
            for e in db.employees.find(employee_filter, {"emp_code": 1})
        ]

        pipeline = [
            {
                "$match": {
                    "emp_code": {"$in": employee_codes},
                    "date": {
                        "$gte": first_day.isoformat(),
                        "$lte": last_day.isoformat(),
                    },
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
        ]

        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")

        try:
            year, month_number = map(int, month.split("-"))
            first_day = date(year, month_number, 1)
            last_day = date(
                year,
                month_number,
                calendar.monthrange(year, month_number)[1],
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": first_day.isoformat(),
                        "$lte": last_day.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {"$limit": limit},
        ]

        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="department, from, and to are required",
            )

        if from_date > to_date:
            raise HTTPException(status_code=422, detail="'from' must be <= 'to'")

        pipeline = [
            {
                "$match": {
                    "date": {"$gte": from_date, "$lte": to_date},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
        ]

        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
