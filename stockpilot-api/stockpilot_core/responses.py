"""HTTP response helpers: idempotent writes and spreadsheet-safe CSV."""
import csv
import hashlib
import io
import json

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from .database import transaction

def idempotent(database, scope, key, body, operation):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    with database.connect() as db, transaction(db):
        old = db.execute("SELECT * FROM idempotency WHERE scope=? AND request_key=?", (scope, key)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise HTTPException(409, "Idempotency key was already used with a different request")
            return JSONResponse(json.loads(old["response_json"]), status_code=old["status_code"],
                                headers={"Idempotency-Replayed": "true"})
        result, status = operation(db)
        encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        db.execute("INSERT INTO idempotency(scope,request_key,request_hash,response_json,status_code) VALUES(?,?,?,?,?)",
                   (scope, key, digest, encoded, status))
        return JSONResponse(result, status_code=status, headers={"Idempotency-Replayed": "false"})


def csv_response(columns, rows, filename):
    def safe_cell(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return "" if value is None else value

    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_cell(row[column]) for column in columns])
    return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Content-Type-Options": "nosniff"})
