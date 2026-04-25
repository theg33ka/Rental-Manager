from sqlalchemy import text

from rental_manager.database import SessionLocal, init_db


IDS_TO_DELETE = [585, 783]


def main():
    init_db()

    with SessionLocal() as session:
        print("Connected to database.")

        before = session.execute(
            text("""
                SELECT id, lease_id, period_start, period_end, due_date
                FROM rent_charges
                WHERE id = ANY(:ids)
                ORDER BY id
            """),
            {"ids": IDS_TO_DELETE},
        ).mappings().all()

        print(f"Rows before delete: {len(before)}")
        for row in before:
            print(dict(row))

        deleted = session.execute(
            text("""
                DELETE FROM rent_charges
                WHERE id = ANY(:ids)
                RETURNING id, lease_id, period_start, period_end, due_date
            """),
            {"ids": IDS_TO_DELETE},
        ).mappings().all()

        print(f"Deleted rows: {len(deleted)}")
        for row in deleted:
            print(dict(row))

        session.commit()
        print("Committed.")

        after = session.execute(
            text("""
                SELECT id, lease_id, period_start, period_end, due_date
                FROM rent_charges
                WHERE id = ANY(:ids)
                ORDER BY id
            """),
            {"ids": IDS_TO_DELETE},
        ).mappings().all()

        print(f"Rows after commit: {len(after)}")
        for row in after:
            print(dict(row))


if __name__ == "__main__":
    main()